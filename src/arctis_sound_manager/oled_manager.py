# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

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
from PIL import Image

from arctis_sound_manager.oled_protocol import OledProtocol
from arctis_sound_manager.oled_renderer import OledRenderer
from arctis_sound_manager.weather_service import WeatherData, WeatherService
from arctis_sound_manager.config import parsed_status
from arctis_sound_manager import profile_manager

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
        power_status = parsed.get("headset_power_status", "")
        battery = int(parsed.get("headset_battery_charge", -1))
        charging = power_status == "cable_charging"
        connected = power_status not in ("offline", "paired_offline", "")

        device_config = self._core.device_config
        active_profile = profile_manager.active_profile_name() or (
            device_config.name if device_config else "Unknown"
        )

        self._blink = not self._blink
        time_str = datetime.now().strftime("%H:%M")
        eq_preset = _active_eq_preset("game")

        gs = self._core.general_settings

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
            show_battery=gs.oled_show_battery,
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

    def _send_current_frame(self) -> None:
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
        for packet in packets:
            self._send_oled_packet(packet)

    def _send_oled_packet(self, packet: list[int], *, control: bool = False) -> None:
        """Send one HID SET_REPORT packet to the OLED controller.

        Args:
            packet:  Raw byte list (first byte is the report id).
            control: True for brightness/return-to-ui (Output report, type 0x02);
                     False (default) for image frames (Feature report, type 0x03).
                     The distinction is required by the Wired GameDAC Gen 2 firmware
                     (issue #76, derived from ggoled reference implementation).
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
                    return
                try:
                    usb_device.ctrl_transfer(
                        bmRequestType, 0x09,
                        wvalue, self._oled_interface,
                        packet,
                    )
                    return
                except usb.core.USBError as e:
                    last_err = e
            # Back-off outside the lock so other USB users are not blocked.
            if attempt + 1 < _MAX_ATTEMPTS:
                time.sleep(min((attempt + 1) ** 2, 50) / 1000.0)

        logger.warning("OLED USB error after %d attempts: %s", _MAX_ATTEMPTS, last_err)

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
