# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import errno
import logging
import threading
import time
from datetime import datetime
from typing import TYPE_CHECKING

import usb.core
import usb.util

if TYPE_CHECKING:
    from arctis_sound_manager.core import CoreEngine

from pathlib import Path

from arctis_sound_manager.oled_protocol import OledProtocol
from arctis_sound_manager.power_status import (HeadsetPower,
                                               normalize_power_value)
from arctis_sound_manager.weather_service import WeatherData, WeatherService
from arctis_sound_manager.config import parsed_status
from arctis_sound_manager import profile_manager

# Pillow is an optional runtime dependency: only headsets whose YAML profile
# declares an OLED screen ever need it. But core.py imports this module
# unconditionally at module scope regardless of which headset (if any) is
# connected, and OledRenderer pulls PIL in too — so a plain top-level
# `from PIL import Image` here used to kill the whole daemon with a bare
# `ModuleNotFoundError` on any Debian/Ubuntu install missing python3-pil,
# before system_deps_checker ever got a chance to report it as the
# DEGRADED, self-healable issue it already models (PKG-1). Guarding both
# imports lets `import arctis_sound_manager.core` — and therefore the
# daemon and `--verify-setup` — succeed either way; OledManager.__init__
# below turns a missing Pillow into a normal exception instead, which
# CoreEngine already catches and degrades from (OLED disabled, everything
# else keeps working).
try:
    from PIL import Image
    from arctis_sound_manager.oled_renderer import OledRenderer
    PIL_AVAILABLE = True
except ImportError:
    Image = None  # type: ignore[assignment]
    OledRenderer = None  # type: ignore[assignment]
    PIL_AVAILABLE = False

_CFG = Path.home() / ".config" / "arctis_manager"


def _active_eq_preset(channel: str) -> str:
    f = _CFG / f".sonar_preset_{channel}"
    return f.read_text().strip() if f.exists() else "Flat"

_REFRESH_INTERVAL_S = 5.0
_SPLASH_DURATION_S = 3.0
# Fallback OLED transport parameters for devices that carry no ``oled:``
# section in their YAML.  These values match the Nova Pro Wireless defaults
# and keep backwards-compatibility for any device that previously relied on
# the former module-level constants.
_OLED_INTERFACE_DEFAULT = 4
_OLED_WVALUE_DEFAULT = 0x0300
_OLED_REPORT_ID_DEFAULT = 0x06
_OLED_WIDTH_DEFAULT = 128
_OLED_HEIGHT_DEFAULT = 64
# HID SET_REPORT type codes (USB spec 9.3.1 / HID 1.11 §7.2.2).
# Frame packets use Feature (0x03); brightness/return-to-ui use Output (0x02)
# because that is what ggoled does and the Wired GameDAC requires (issue #76).
_OLED_REPORT_TYPE_FEATURE = 0x03
_OLED_REPORT_TYPE_OUTPUT = 0x02
_SCROLL_PAUSE_TOP_S = 5.0       # seconds to pause at top before scrolling
_SCROLL_PAUSE_BOTTOM_S = 3.0    # seconds to pause at bottom before resetting
# Don't start the vertical marquee for a tiny overflow — a few px past the
# panel are bottom padding, not content. Avoids pointless jitter when the
# layout essentially fits.
_SCROLL_VERTICAL_DEADZONE_PX = 3
_EQ_SCROLL_PAUSE_START_S = 2.0  # pause before EQ marquee starts (readability)
_EQ_SCROLL_PAUSE_END_S = 2.0    # pause at end before snapping back

# Circuit breaker for the OLED USB transport (issue #100): when the
# interface stays EBUSY (errno 16 — e.g. distrobox/container USB
# passthrough holding the handle, or another process claiming it), retrying
# every packet of every frame floods the log with "Resource busy" warnings.
# After this many consecutive *frames* fail with EBUSY, stop hammering the
# device for a while and emit a single warning instead of one per packet.
_OLED_BUSY_FAIL_THRESHOLD = 3
_OLED_BUSY_SUSPEND_S = 60.0

# How long to wait for a SET_REPORT to be acknowledged (issue #197). pyusb's
# default is a full second, and the wired GameDAC never acknowledges its screen
# writes at all: five attempts at one second each spent five seconds per strip
# on a panel that had already drawn it. A report this panel accepts comes back
# in single-digit milliseconds; past this, waiting longer buys nothing.
_OLED_SEND_TIMEOUT_MS = 250

# What to wait once a panel has shown it does not acknowledge at all (#196).
# The 250 ms above is spent in full on every strip of every frame on such a
# device, and a 128 px panel goes out as two strips: the right half is drawn a
# quarter of a second after the left one. Nobody sees that on a static screen,
# but on anything scrolling the two halves visibly disagree — which is what the
# reporter was left with once both halves finally drew.
#
# A report this panel accepts comes back in single-digit milliseconds, so this
# is still several times the observed round trip; it only stops the wait for an
# acknowledgement that is never coming. Both strips then go out inside ~40 ms.
_OLED_SEND_TIMEOUT_UNACKED_MS = 20

# How many unacknowledged writes in a row before believing it of the device
# rather than of the moment. Two is one whole frame on a 128 px panel: a single
# lost acknowledgement under load is not a silent device, but a frame where
# every strip went unanswered is.
_OLED_UNACKED_STREAK = 2

_BURN_IN_INTERVAL_S = 60.0
_BURN_IN_POSITIONS: list[tuple[int, int]] = [
    (0, 0), (1, 0), (1, 1), (0, 1), (-1, 1),
    (-1, 0), (-1, -1), (0, -1), (1, -1),
]

# speed (1–5) → seconds between each 1px scroll step
_SPEED_TO_INTERVAL: dict[int, float] = {
    1: 0.8,
    2: 0.4,
    3: 0.2,
    4: 0.1,
    5: 0.05,
}

logger = logging.getLogger(__name__)


def _compute_wvalue(report_type: int, report_id: int) -> int:
    """Return the HID SET_REPORT wValue for a given report type and id.

    wValue = (report_type << 8) | (report_id & 0xFF) per USB HID spec §7.2.
    Extracted as a pure helper so it can be unit-tested without USB hardware.
    """
    return (report_type << 8) | (report_id & 0xFF)


class OledManager:
    def __init__(self, core: CoreEngine) -> None:
        if not PIL_AVAILABLE:
            # Turns the module-import-time crash this class used to cause
            # (PKG-1) into a normal exception at construction time instead.
            # CoreEngine's own try/except around `OledManager(self)` already
            # catches this and logs "OLED display disabled" — the same path
            # used for a refused USB interface or a missing font — so a
            # headset that does have a screen simply loses the OLED feature
            # instead of taking the whole daemon down with it.
            # system_deps_checker._build_checks() already carries a DEGRADED
            # "PIL / Pillow (python module)" entry with the per-distro
            # install command; `asm-daemon --verify-setup` surfaces it now
            # that this module can actually finish importing without Pillow.
            raise RuntimeError(
                "Pillow (PIL) is not installed — OLED display support is "
                "unavailable. Install your distro's python3-pillow package "
                "(or `pip install pillow`)."
            )
        self._core = core

        # Resolve per-device OLED transport parameters from the device YAML.
        # Fall back to the former hard-coded Wireless values so devices without
        # an ``oled:`` section keep identical byte-for-byte behaviour.
        oled_cfg = core.device_config.oled if core.device_config is not None else None
        self._oled_interface: int = oled_cfg.interface if oled_cfg is not None else _OLED_INTERFACE_DEFAULT
        self._oled_wvalue: int = oled_cfg.wvalue if oled_cfg is not None else _OLED_WVALUE_DEFAULT
        _report_id: int = oled_cfg.report_id if oled_cfg is not None else _OLED_REPORT_ID_DEFAULT
        _width: int = oled_cfg.width if oled_cfg is not None else _OLED_WIDTH_DEFAULT
        _height: int = oled_cfg.height if oled_cfg is not None else _OLED_HEIGHT_DEFAULT

        self._oled_report_id: int = _report_id
        # Derive the frame report type from the high byte of the YAML wvalue
        # (0x0300 → 0x03 Feature; 0x0200 → 0x02 Output).  The actual wValue
        # passed to ctrl_transfer is recomputed per-packet in _send_oled_packet.
        self._oled_frame_report_type: int = (self._oled_wvalue >> 8) & 0xFF

        self._protocol = OledProtocol(report_id=_report_id, width=_width, height=_height)
        self._renderer = OledRenderer()
        self._weather = WeatherService()
        self._stop_event = threading.Event()
        self._reset_scroll_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._scroll_thread: threading.Thread | None = None
        self._image_lock = threading.Lock()
        self._current_image: Image.Image | None = None
        self._blink = False
        self._last_update_time: float = 0.0
        self._screen_off: bool = False
        self._scroll_offset: int = 0
        self._eq_scroll_offset: int = 0
        self._eq_reset_event = threading.Event()
        self._eq_scroll_thread: threading.Thread | None = None
        self._profile_scroll_offset: int = 0
        self._profile_reset_event = threading.Event()
        self._profile_scroll_thread: threading.Thread | None = None
        self._last_render_params: dict = {}
        self._header_h: int = 0
        self._burn_in_step: int = 0
        self._burn_in_x: int = 0
        self._burn_in_y: int = 0
        self._burn_in_last: float = 0.0
        self._splash_until: float = 0.0
        self._eq_chat_scroll_offset: int = 0
        self._eq_chat_reset_event = threading.Event()
        self._eq_chat_scroll_thread: threading.Thread | None = None

        # How long to wait for a SET_REPORT acknowledgement, and how many writes
        # in a row have gone unacknowledged (issue #196). Starts optimistic and
        # drops to _OLED_SEND_TIMEOUT_UNACKED_MS once this device has shown it
        # never acknowledges; a single acknowledged write puts it back.
        self._send_timeout_ms: int = _OLED_SEND_TIMEOUT_MS
        self._unacked_streak: int = 0

        # OLED USB circuit breaker state (issue #100).
        self._frame_fail_streak: int = 0
        self._suspend_until: float = 0.0
        self._last_send_errno: int | None = None
        # Tracked purely to reset the breaker if the same manager instance
        # ever outlives a device re-attach (belt-and-suspenders: today
        # CoreEngine always builds a fresh OledManager on re-attach, which
        # already resets these via __init__).
        self._last_usb_device_id: int | None = (
            id(core.usb_device) if core.usb_device is not None else None
        )

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._reset_scroll_event.clear()
        self._last_update_time = datetime.now().timestamp()
        self._screen_off = False
        self._scroll_offset = 0
        self._thread = threading.Thread(
            target=self._refresh_loop,
            name="OledRefresh",
            daemon=True,
        )
        self._scroll_thread = threading.Thread(
            target=self._scroll_loop,
            name="OledScroll",
            daemon=True,
        )
        self._eq_scroll_thread = threading.Thread(
            target=self._eq_scroll_loop,
            name="OledEqScroll",
            daemon=True,
        )
        self._profile_scroll_thread = threading.Thread(
            target=self._profile_scroll_loop,
            name="OledProfileScroll",
            daemon=True,
        )
        self._eq_chat_scroll_thread = threading.Thread(
            target=self._eq_chat_scroll_loop,
            name="OledEqChatScroll",
            daemon=True,
        )
        self._thread.start()
        self._scroll_thread.start()
        self._eq_scroll_thread.start()
        self._profile_scroll_thread.start()
        self._eq_chat_scroll_thread.start()
        self.set_brightness(self._core.general_settings.oled_brightness)
        self._show_splash()
        logger.info("OledManager started (interval=%.1fs)", _REFRESH_INTERVAL_S)

    def _show_splash(self) -> None:
        """Draw the ASM splash — unless the user asked for the DAC's own UI.

        Custom Display off means "keep the screen the DAC's". The splash is our
        content, and it does more than flash a logo: it sets `_splash_until`,
        and the refresh loop sleeps until that expires, so for
        `_SPLASH_DURATION_S` nothing sends the return-to-UI packet that hands
        the panel back to the firmware. The DAC sits frozen on our logo every
        time the daemon starts, the GUI opens, or the tray icon is clicked —
        which is what "it seems to hang" means when Custom Display is off.
        Reported on Discord by Messiah Complex on a Nova Pro Wired DAC.
        """
        if not self._core.general_settings.oled_custom_display:
            return
        self._splash_until = datetime.now().timestamp() + _SPLASH_DURATION_S
        frame = self._renderer.render_splash_image()
        packets = self._protocol.build_frame_packets(
            frame, self._protocol.DISPLAY_WIDTH, self._protocol.DISPLAY_HEIGHT
        )
        for packet in packets:
            self._send_oled_packet(packet)

    def set_brightness(self, level: int) -> None:
        packet = self._protocol.build_brightness_packet(level)
        self._send_oled_packet(packet, control=True)

    def set_custom_display(self, enabled: bool) -> None:
        if not enabled:
            self._send_oled_packet(self._protocol.build_return_to_ui_packet(), control=True)
        else:
            self._reset_scroll()
            self.update_display(activity=True)

    def refresh(self) -> None:
        """Re-render the custom display now so a settings change takes effect live.

        Which elements are shown, their fonts, scroll speed, the weather — all
        of it used to require flipping the Custom Display toggle off and on to
        appear, because SetSetting only persisted the value (#172). Calling this
        after a change redraws from the current settings immediately.

        No-op while the DAC shows its own UI (Custom Display disabled): there is
        nothing of ours on screen, and drawing a frame would wrongly take the
        screen back over.
        """
        if not self._core.general_settings.oled_custom_display:
            return
        self._reset_scroll()
        self.update_display(activity=True)

    def invalidate_weather_cache(self) -> None:
        self._weather.invalidate()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        if self._scroll_thread is not None:
            self._scroll_thread.join(timeout=2.0)
            self._scroll_thread = None
        if self._eq_scroll_thread is not None:
            self._eq_scroll_thread.join(timeout=2.0)
            self._eq_scroll_thread = None
        if self._profile_scroll_thread is not None:
            self._profile_scroll_thread.join(timeout=2.0)
            self._profile_scroll_thread = None
        if self._eq_chat_scroll_thread is not None:
            self._eq_chat_scroll_thread.join(timeout=2.0)
            self._eq_chat_scroll_thread = None
        logger.info("OledManager stopped")

    def _reset_scroll(self) -> None:
        self._scroll_offset = 0
        self._eq_scroll_offset = 0
        self._profile_scroll_offset = 0
        self._eq_chat_scroll_offset = 0
        self._reset_scroll_event.set()
        self._eq_reset_event.set()
        self._profile_reset_event.set()
        self._eq_chat_reset_event.set()

    def update_display(self, activity: bool = True) -> None:
        if datetime.now().timestamp() < self._splash_until:
            return
        status = self._core.device_status
        if status is None:
            return

        if activity:
            if self._screen_off:
                self._screen_off = False
                self.set_brightness(self._core.general_settings.oled_brightness)
            self._last_update_time = datetime.now().timestamp()

        parsed = parsed_status(status, self._core.device_config)
        # Three states, not two. This used to be a raw string test against
        # ("offline", "paired_offline", ""), which folded two different answers
        # into one: a headset that is *off*, and a device that never reports a
        # power status at all. The empty string is the second one — and on a
        # wired DAC, which has no battery and no power status to give, it meant
        # the screen wrote "Offline" forever. Reported on Discord as "battery
        # just shows offline on my DAC".
        #
        # normalize_power_value() also settles the vocabulary: device YAMLs say
        # 'off' (Nova 5, Nova 7*, Arctis 7+/9/1 Wireless) or 'offline' (Nova Pro
        # Wireless, Elite, Omni). Only 'offline' was listed here, so on an
        # 'off' device a powered-down headset would have kept a frozen
        # percentage on screen — the same half-the-devices mistake as #124. No
        # 'off' device has an OLED today, so this was latent rather than live;
        # it is fixed here because the next one might.
        power_raw = parsed.get("headset_power_status")
        battery = int(parsed.get("headset_battery_charge", -1))
        power = normalize_power_value(power_raw)
        # Kept as a literal: 'cable_charging' is one dialect's word for "on, and
        # on its stand", and normalizing it to ON deliberately loses the detail
        # the charging icon needs.
        charging = (isinstance(power_raw, str)
                    and power_raw.strip().lower() == "cable_charging")
        connected = power is not HeadsetPower.OFF
        # UNKNOWN is not OFF. A device that reports a level but no power status
        # (or one whose status word we have no rule for, like the Elite's
        # 'standby') has a perfectly good reading, and hiding it behind
        # "Offline" throws away the only thing the user asked to see. The home
        # page already draws this distinction; the screen did not.
        has_battery = battery >= 0

        device_config = self._core.device_config
        active_profile = profile_manager.active_profile_name() or (
            device_config.name if device_config else "Unknown"
        )

        self._blink = not self._blink
        eq_preset = _active_eq_preset("game")

        gs = self._core.general_settings

        # 24-hour ("14:05") or 12-hour ("2:05 PM") clock, per user setting.
        if getattr(gs, "oled_time_24h", True):
            time_str = datetime.now().strftime("%H:%M")
        else:
            time_str = datetime.now().strftime("%I:%M %p").lstrip("0")

        # New OLED data sources
        mic_status = str(parsed.get("mic_status", ""))
        eq_chat_preset = _active_eq_preset("chat")
        eq_mode_file = _CFG / ".eq_mode"
        eq_mode = eq_mode_file.read_text().strip() if eq_mode_file.exists() else "custom"

        weather_data: WeatherData | None = None
        if gs.weather_enabled and gs.weather_lat and gs.weather_lon:
            weather_data = self._weather.get(
                gs.weather_lat, gs.weather_lon,
                gs.weather_units, gs.weather_city_display or gs.weather_location,
            )

        self._last_render_params = dict(
            battery_percent=battery,
            charging=charging,
            connected=connected,
            time_str=time_str,
            active_profile=active_profile,
            blink_state=self._blink,
            eq_preset=eq_preset,
            weather=weather_data,
            show_time=gs.oled_show_time,
            # Nothing to say beats saying the wrong thing: a device with no
            # battery to report and no power status simply gets no battery
            # element. "Offline" would be a claim about a connection on a DAC
            # that has no wireless link at all. A headset that is genuinely off
            # still gets it — there, "Offline" is the answer.
            show_battery=gs.oled_show_battery and (has_battery
                                                   or power is HeadsetPower.OFF),
            show_profile=gs.oled_show_profile,
            show_eq=gs.oled_show_eq,
            show_mic_status=gs.oled_show_mic_status,
            show_sonar_mode=gs.oled_show_sonar_mode,
            show_eq_chat=gs.oled_show_eq_chat,
            show_weather_city=gs.oled_show_weather_city,
            mic_status=mic_status,
            eq_mode=eq_mode,
            eq_chat_preset=eq_chat_preset,
            display_order=gs.oled_display_order,
            font_sizes={
                'time':         gs.oled_font_time,
                'battery':      gs.oled_font_battery,
                'mic':          gs.oled_font_mic,
                'profile':      gs.oled_font_profile,
                'eq':           gs.oled_font_eq,
                'eq_chat':      gs.oled_font_eq_chat,
                'sonar_mode':   gs.oled_font_sonar_mode,
                'weather_temp': gs.oled_font_weather_temp,
            },
        )
        image, header_h = self._renderer.render_status_image(
            **self._last_render_params,
            eq_scroll_offset=self._eq_scroll_offset,
            profile_scroll_offset=self._profile_scroll_offset,
            eq_chat_scroll_offset=self._eq_chat_scroll_offset,
        )

        with self._image_lock:
            self._current_image = image
            self._header_h = header_h

        # On activity (user interaction), reset scroll and send immediately
        if activity:
            self._reset_scroll()

        self._send_current_frame()

    def _check_usb_device_reattached(self) -> None:
        """Reset the circuit breaker if the underlying USB device object changed.

        CoreEngine always builds a fresh OledManager on re-attach today (its
        instance attributes already start at zero), but this guard keeps the
        breaker correct even if that ever changes, without touching core.py.
        """
        current = self._core.usb_device
        current_id = id(current) if current is not None else None
        if current_id != self._last_usb_device_id:
            self._last_usb_device_id = current_id
            self._frame_fail_streak = 0
            self._suspend_until = 0.0

    def _send_current_frame(self) -> None:
        self._check_usb_device_reattached()

        now = datetime.now().timestamp()
        if now < self._suspend_until:
            # Circuit breaker open: interface is known busy, don't hammer it.
            logger.debug("OLED frame skipped: suspended until %.0f (interface busy)", self._suspend_until)
            return

        with self._image_lock:
            if self._current_image is None:
                return
            frame = self._renderer.crop_frame(
                self._current_image, self._scroll_offset + self._burn_in_y,
                self._header_h, self._burn_in_x,
            )
        packets = self._protocol.build_frame_packets(
            frame, self._protocol.DISPLAY_WIDTH, self._protocol.DISPLAY_HEIGHT
        )

        # Every strip is sent, even after one fails (issue #197). A frame is
        # split into strips of at most 64 px, each its own SET_REPORT; giving
        # up at the first failure meant the right half of a 128 px panel was
        # never sent at all, and the DAC kept whatever it had drawn there.
        # On the wired GameDAC that happened on every single frame, because it
        # executes screen writes without ever acknowledging them.
        frame_ok = True
        for packet in packets:
            if not self._send_oled_packet(packet):
                frame_ok = False

        if frame_ok:
            self._frame_fail_streak = 0
            return

        if self._last_send_errno == errno.EBUSY:
            self._frame_fail_streak += 1
        else:
            # Not the EBUSY spam scenario this breaker targets — don't count
            # it towards the busy-streak.
            self._frame_fail_streak = 0

        if self._frame_fail_streak >= _OLED_BUSY_FAIL_THRESHOLD:
            self._suspend_until = now + _OLED_BUSY_SUSPEND_S
            self._frame_fail_streak = 0
            logger.warning(
                "OLED suspended for 60s: interface busy — likely another "
                "process or container USB passthrough"
            )

    def _on_write_acknowledged(self) -> None:
        """A write came back. Wait properly for the next one."""
        self._unacked_streak = 0
        if self._send_timeout_ms != _OLED_SEND_TIMEOUT_MS:
            logger.info(
                "OLED: the panel acknowledged a write — waiting %d ms again",
                _OLED_SEND_TIMEOUT_MS,
            )
            self._send_timeout_ms = _OLED_SEND_TIMEOUT_MS

    def _on_write_unacknowledged(self) -> None:
        """A write drew but never came back. Stop paying for the answer.

        The wired GameDAC executes screen writes without acknowledging them, so
        every strip costs the whole timeout even though the pixels are already
        lit. On a 128 px panel that is two strips, and the second one lands a
        quarter of a second after the first — invisible on a static screen,
        plainly visible on anything that scrolls (issue #196).

        Once a full frame has gone unanswered, waiting is no longer buying
        information about this device, so drop to a timeout that only covers the
        round trip a working panel actually takes. It is not permanent: an
        acknowledged write restores the patient one, so a device that goes quiet
        under load rather than by design gets its margin back.
        """
        self._unacked_streak += 1
        if (self._unacked_streak < _OLED_UNACKED_STREAK
                or self._send_timeout_ms == _OLED_SEND_TIMEOUT_UNACKED_MS):
            return
        logger.info(
            "OLED: %d writes in a row drew without acknowledging — dropping the "
            "send timeout to %d ms so both halves of the panel land together",
            self._unacked_streak, _OLED_SEND_TIMEOUT_UNACKED_MS,
        )
        self._send_timeout_ms = _OLED_SEND_TIMEOUT_UNACKED_MS

    def _send_oled_packet(self, packet: list[int], *, control: bool = False) -> bool:
        """Send one HID SET_REPORT packet to the OLED controller.

        Args:
            packet:  Raw byte list (first byte is the report id).
            control: True for brightness/return-to-ui (Output report, type 0x02);
                     False (default) for image frames (Feature report, type 0x03).
                     The distinction is required by the Wired GameDAC Gen 2 firmware
                     (issue #76, derived from ggoled reference implementation).

        Returns:
            True if the device accepted the packet, False if all retries
            were exhausted (or the device was gone). Callers that send a
            whole frame worth of packets are responsible for deciding what
            to log/suspend at the frame level (issue #100) — this method
            only logs at DEBUG to avoid per-packet spam.
        """
        # wValue = (report_type << 8) | report_id — correct HID SET_REPORT semantics.
        report_type = _OLED_REPORT_TYPE_OUTPUT if control else self._oled_frame_report_type
        wvalue = _compute_wvalue(report_type, self._oled_report_id)

        bmRequestType = usb.util.build_request_type(
            direction=usb.util.CTRL_OUT,
            type=usb.util.CTRL_TYPE_CLASS,
            recipient=usb.util.CTRL_RECIPIENT_INTERFACE,
        )

        _MAX_ATTEMPTS = 5
        last_err: usb.core.USBError | None = None
        for attempt in range(_MAX_ATTEMPTS):
            with self._core._usb_write_lock:
                usb_device = self._core.usb_device
                if usb_device is None:
                    # No device to talk to (mid-disconnect) — not the EBUSY
                    # scenario the breaker targets, so clear any stale errno.
                    self._last_send_errno = None
                    return False
                try:
                    usb_device.ctrl_transfer(
                        bmRequestType, 0x09,
                        wvalue, self._oled_interface,
                        packet,
                        timeout=self._send_timeout_ms,
                    )
                    self._on_write_acknowledged()
                    return True
                except usb.core.USBError as e:
                    last_err = e
                    if e.errno == errno.ETIMEDOUT:
                        # The wired GameDAC draws the strip and never sends the
                        # acknowledgement back (issue #197). An unacknowledged
                        # report is not an unexecuted one: resending it would
                        # redraw pixels that are already lit, and each retry
                        # costs another full timeout. Treat it as delivered.
                        logger.debug(
                            "OLED write not acknowledged (errno 110) — "
                            "treating as sent, the panel draws it anyway"
                        )
                        self._on_write_unacknowledged()
                        return True
            # Back-off outside the lock so other USB users are not blocked.
            if attempt + 1 < _MAX_ATTEMPTS:
                time.sleep(min((attempt + 1) ** 2, 50) / 1000.0)

        self._last_send_errno = getattr(last_err, "errno", None)
        logger.debug("OLED USB error after %d attempts: %s", _MAX_ATTEMPTS, last_err)
        return False

    def _advance_burn_in(self) -> None:
        now = datetime.now().timestamp()
        if now - self._burn_in_last >= _BURN_IN_INTERVAL_S:
            self._burn_in_step = (self._burn_in_step + 1) % len(_BURN_IN_POSITIONS)
            self._burn_in_x, self._burn_in_y = _BURN_IN_POSITIONS[self._burn_in_step]
            self._burn_in_last = now

    def _refresh_loop(self) -> None:
        while not self._stop_event.is_set():
            remaining_splash = self._splash_until - datetime.now().timestamp()
            if remaining_splash > 0:
                self._stop_event.wait(timeout=remaining_splash + 0.05)
                continue
            self._stop_event.wait(timeout=_REFRESH_INTERVAL_S)
            if self._stop_event.is_set():
                break
            try:
                self._advance_burn_in()
                gs = self._core.general_settings
                timeout = gs.oled_screen_timeout
                if timeout > 0 and not self._screen_off and not gs.oled_custom_display:
                    elapsed = datetime.now().timestamp() - self._last_update_time
                    if elapsed >= timeout:
                        self._screen_off = True
                        self._send_oled_packet(self._protocol.build_brightness_packet(0), control=True)
                        continue

                if not self._screen_off:
                    if not gs.oled_custom_display:
                        self._send_oled_packet(self._protocol.build_return_to_ui_packet(), control=True)
                        # timeout=0 means "never sleep": re-assert brightness every cycle
                        # to prevent the DAC firmware's own ~60s screen-off from firing.
                        if timeout == 0:
                            self.set_brightness(gs.oled_brightness)
                    else:
                        self.update_display(activity=False)
                        self.set_brightness(gs.oled_brightness)
            except Exception as e:
                logger.warning("OLED refresh error: %s", e)

    def _update_scroll_frame(self) -> None:
        """Re-render _current_image with current eq/profile scroll offsets and send it."""
        params = self._last_render_params
        if not params:
            return
        image, header_h = self._renderer.render_status_image(
            **params,
            eq_scroll_offset=self._eq_scroll_offset,
            profile_scroll_offset=self._profile_scroll_offset,
            eq_chat_scroll_offset=self._eq_chat_scroll_offset,
        )
        with self._image_lock:
            self._current_image = image
            self._header_h = header_h
        self._send_current_frame()

    def _eq_scroll_wait(self, seconds: float) -> bool:
        """Like _scroll_wait but interrupts on EQ reset instead of vertical scroll reset."""
        deadline = datetime.now().timestamp() + seconds
        while True:
            if self._stop_event.is_set():
                return True
            if self._eq_reset_event.is_set():
                return True
            remaining = deadline - datetime.now().timestamp()
            if remaining <= 0:
                return False
            self._stop_event.wait(min(remaining, 0.05))

    def _eq_scroll_loop(self) -> None:
        """Horizontal marquee thread for the EQ name line when it overflows 128px."""
        while not self._stop_event.is_set():
            try:
                self._eq_reset_event.clear()
                self._eq_scroll_offset = 0

                gs = self._core.general_settings
                eq_speed = gs.oled_eq_scroll_speed
                if not gs.oled_show_eq or not gs.oled_custom_display or eq_speed == 0:
                    if self._eq_scroll_wait(0.5):
                        continue
                    continue

                eq_preset = _active_eq_preset("game")
                text_w = self._renderer.measure_eq_text(eq_preset, gs.oled_font_eq)
                # Draw origin is x=1; we want the last pixel at x=127 (WIDTH-1).
                # text_w is the full advance → place advance end at x=127 → offset = text_w - 126.
                max_offset = text_w - (self._renderer.WIDTH - 1)
                logger.debug(
                    "EQ scroll: preset=%r font_sz=%d text_w=%d max_offset=%d show_eq=%s custom=%s speed=%d",
                    eq_preset, gs.oled_font_eq, text_w, max_offset,
                    gs.oled_show_eq, gs.oled_custom_display, eq_speed,
                )

                if max_offset <= 0:
                    logger.debug("EQ scroll: text fits, no scroll needed")
                    if self._eq_scroll_wait(0.5):
                        continue
                    continue

                # Initial pause so the user can read the start of the name
                if self._eq_scroll_wait(_EQ_SCROLL_PAUSE_START_S):
                    continue

                # Scroll left pixel by pixel using the configured speed
                interval = _SPEED_TO_INTERVAL.get(eq_speed, 0.2)
                while self._eq_scroll_offset < max_offset:
                    if self._stop_event.is_set() or self._eq_reset_event.is_set():
                        break
                    self._eq_scroll_offset += 1
                    self._update_scroll_frame()
                    if self._eq_scroll_wait(interval):
                        break
                    interval = _SPEED_TO_INTERVAL.get(gs.oled_eq_scroll_speed, 0.2)
                else:
                    # Reached end — pause 2s then snap back
                    if self._eq_scroll_wait(_EQ_SCROLL_PAUSE_END_S):
                        continue
                    self._eq_scroll_offset = 0
                    self._update_scroll_frame()

            except Exception as e:
                logger.warning("OLED EQ scroll error: %s", e)
                self._stop_event.wait(0.5)

    def _profile_scroll_wait(self, seconds: float) -> bool:
        deadline = datetime.now().timestamp() + seconds
        while True:
            if self._stop_event.is_set():
                return True
            if self._profile_reset_event.is_set():
                return True
            remaining = deadline - datetime.now().timestamp()
            if remaining <= 0:
                return False
            self._stop_event.wait(min(remaining, 0.05))

    def _profile_scroll_loop(self) -> None:
        """Horizontal marquee thread for the Profile name line when it overflows 128px."""
        while not self._stop_event.is_set():
            try:
                self._profile_reset_event.clear()
                self._profile_scroll_offset = 0

                gs = self._core.general_settings
                speed = gs.oled_eq_scroll_speed
                if not gs.oled_show_profile or not gs.oled_custom_display or speed == 0:
                    if self._profile_scroll_wait(0.5):
                        continue
                    continue

                active_profile = profile_manager.active_profile_name() or (
                    self._core.device_config.name if self._core.device_config else "Unknown"
                )
                text_w = self._renderer.measure_profile_text(active_profile, gs.oled_font_profile)
                max_offset = text_w - (self._renderer.WIDTH - 1)

                if max_offset <= 0:
                    if self._profile_scroll_wait(0.5):
                        continue
                    continue

                if self._profile_scroll_wait(_EQ_SCROLL_PAUSE_START_S):
                    continue

                interval = _SPEED_TO_INTERVAL.get(speed, 0.2)
                while self._profile_scroll_offset < max_offset:
                    if self._stop_event.is_set() or self._profile_reset_event.is_set():
                        break
                    self._profile_scroll_offset += 1
                    self._update_scroll_frame()
                    if self._profile_scroll_wait(interval):
                        break
                    interval = _SPEED_TO_INTERVAL.get(gs.oled_eq_scroll_speed, 0.2)
                else:
                    if self._profile_scroll_wait(_EQ_SCROLL_PAUSE_END_S):
                        continue
                    self._profile_scroll_offset = 0
                    self._update_scroll_frame()

            except Exception as e:
                logger.warning("OLED Profile scroll error: %s", e)
                self._stop_event.wait(0.5)

    def _scroll_wait(self, seconds: float) -> bool:
        """Wait for seconds, but return True immediately if stop or reset is requested."""
        deadline = datetime.now().timestamp() + seconds
        while True:
            if self._stop_event.is_set():
                return True
            if self._reset_scroll_event.is_set():
                return True
            remaining = deadline - datetime.now().timestamp()
            if remaining <= 0:
                return False
            self._stop_event.wait(min(remaining, 0.05))

    def _scroll_loop(self) -> None:
        """Fast scroll thread: advances scroll offset independently of content refresh."""
        while not self._stop_event.is_set():
            try:
                self._reset_scroll_event.clear()
                self._scroll_offset = 0

                gs = self._core.general_settings
                speed = gs.oled_scroll_speed
                if speed == 0 or self._screen_off or not gs.oled_custom_display:
                    if self._scroll_wait(0.5):
                        continue
                    continue

                with self._image_lock:
                    img = self._current_image
                overflow = (img.height - self._renderer.HEIGHT) if img is not None else 0
                if overflow <= _SCROLL_VERTICAL_DEADZONE_PX:
                    if self._scroll_wait(0.5):
                        continue
                    continue

                # Pause at top
                if self._scroll_wait(_SCROLL_PAUSE_TOP_S):
                    continue

                # Scroll down pixel by pixel
                interval = _SPEED_TO_INTERVAL.get(self._core.general_settings.oled_scroll_speed, 0.2)
                while self._scroll_offset < overflow:
                    if self._stop_event.is_set() or self._reset_scroll_event.is_set():
                        break
                    self._scroll_offset += 1
                    self._send_current_frame()
                    if self._scroll_wait(interval):
                        break
                    interval = _SPEED_TO_INTERVAL.get(self._core.general_settings.oled_scroll_speed, 0.2)
                else:
                    # Reached bottom naturally — pause then scroll back up
                    if self._scroll_wait(_SCROLL_PAUSE_BOTTOM_S):
                        continue

                    interval = _SPEED_TO_INTERVAL.get(self._core.general_settings.oled_scroll_speed, 0.2)
                    while self._scroll_offset > 0:
                        if self._stop_event.is_set() or self._reset_scroll_event.is_set():
                            break
                        self._scroll_offset -= 1
                        self._send_current_frame()
                        if self._scroll_wait(interval):
                            break
                        interval = _SPEED_TO_INTERVAL.get(self._core.general_settings.oled_scroll_speed, 0.2)

            except Exception as e:
                logger.warning("OLED scroll error: %s", e)
                self._stop_event.wait(0.5)

    def _eq_chat_scroll_wait(self, seconds: float) -> bool:
        deadline = datetime.now().timestamp() + seconds
        while True:
            if self._stop_event.is_set():
                return True
            if self._eq_chat_reset_event.is_set():
                return True
            remaining = deadline - datetime.now().timestamp()
            if remaining <= 0:
                return False
            self._stop_event.wait(min(remaining, 0.05))

    def _eq_chat_scroll_loop(self) -> None:
        """Horizontal marquee thread for the Chat EQ preset name line when it overflows 128px."""
        while not self._stop_event.is_set():
            try:
                self._eq_chat_reset_event.clear()
                self._eq_chat_scroll_offset = 0

                gs = self._core.general_settings
                eq_speed = gs.oled_eq_scroll_speed
                if not gs.oled_show_eq_chat or not gs.oled_custom_display or eq_speed == 0:
                    if self._eq_chat_scroll_wait(0.5):
                        continue
                    continue

                eq_chat_preset = _active_eq_preset("chat")
                text_w = self._renderer.measure_eq_chat_text(eq_chat_preset, gs.oled_font_eq_chat)
                max_offset = text_w - (self._renderer.WIDTH - 1)

                if max_offset <= 0:
                    if self._eq_chat_scroll_wait(0.5):
                        continue
                    continue

                if self._eq_chat_scroll_wait(_EQ_SCROLL_PAUSE_START_S):
                    continue

                interval = _SPEED_TO_INTERVAL.get(eq_speed, 0.2)
                while self._eq_chat_scroll_offset < max_offset:
                    if self._stop_event.is_set() or self._eq_chat_reset_event.is_set():
                        break
                    self._eq_chat_scroll_offset += 1
                    self._update_scroll_frame()
                    if self._eq_chat_scroll_wait(interval):
                        break
                    interval = _SPEED_TO_INTERVAL.get(gs.oled_eq_scroll_speed, 0.2)
                else:
                    if self._eq_chat_scroll_wait(_EQ_SCROLL_PAUSE_END_S):
                        continue
                    self._eq_chat_scroll_offset = 0
                    self._update_scroll_frame()

            except Exception as e:
                logger.warning("OLED EQ Chat scroll error: %s", e)
                self._stop_event.wait(0.5)
