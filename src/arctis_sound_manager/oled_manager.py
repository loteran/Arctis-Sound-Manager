from __future__ import annotations

import logging
import threading
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
_OLED_INTERFACE = 4
_OLED_WVALUE = 0x0300
_SCROLL_PAUSE_TOP_S = 5.0       # seconds to pause at top before scrolling
_SCROLL_PAUSE_BOTTOM_S = 3.0    # seconds to pause at bottom before resetting
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


class OledManager:
    def __init__(self, core: CoreEngine) -> None:
        self._core = core
        self._protocol = OledProtocol()
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
        self._burn_in_step: int = 0
        self._burn_in_x: int = 0
        self._burn_in_y: int = 0
        self._burn_in_last: float = 0.0
        self._splash_until: float = 0.0

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
        self._thread.start()
        self._scroll_thread.start()
        self._eq_scroll_thread.start()
        self._profile_scroll_thread.start()
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
        self._send_oled_packet(packet)

    def set_custom_display(self, enabled: bool) -> None:
        if not enabled:
            self._send_oled_packet(self._protocol.build_return_to_ui_packet())
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
        logger.info("OledManager stopped")

    def _reset_scroll(self) -> None:
        self._scroll_offset = 0
        self._eq_scroll_offset = 0
        self._profile_scroll_offset = 0
        self._reset_scroll_event.set()
        self._eq_reset_event.set()
        self._profile_reset_event.set()

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
            display_order=gs.oled_display_order,
            font_sizes={
                'time':         gs.oled_font_time,
                'battery':      gs.oled_font_battery,
                'profile':      gs.oled_font_profile,
                'eq':           gs.oled_font_eq,
                'weather_temp': gs.oled_font_weather_temp,
            },
        )
        image = self._renderer.render_status_image(
            **self._last_render_params,
            eq_scroll_offset=self._eq_scroll_offset,
            profile_scroll_offset=self._profile_scroll_offset,
        )

        with self._image_lock:
            self._current_image = image

        # On activity (user interaction), reset scroll and send immediately
        if activity:
            self._reset_scroll()

        self._send_current_frame()

    def _send_current_frame(self) -> None:
        with self._image_lock:
            if self._current_image is None:
                return
            frame = self._renderer.crop_frame(
                self._current_image, self._scroll_offset + self._burn_in_y, self._burn_in_x
            )
        packets = self._protocol.build_frame_packets(
            frame, self._protocol.DISPLAY_WIDTH, self._protocol.DISPLAY_HEIGHT
        )
        for packet in packets:
            self._send_oled_packet(packet)

    def _send_oled_packet(self, packet: list[int]) -> None:
        bmRequestType = usb.util.build_request_type(
            direction=usb.util.CTRL_OUT,
            type=usb.util.CTRL_TYPE_CLASS,
            recipient=usb.util.CTRL_RECIPIENT_INTERFACE,
        )
        with self._core._usb_write_lock:
            usb_device = self._core.usb_device
            if usb_device is None:
                return
            try:
                usb_device.ctrl_transfer(bmRequestType, 0x09, _OLED_WVALUE, _OLED_INTERFACE, packet)
            except usb.core.USBError as e:
                logger.warning("OLED USB error: %s", e)

    def _advance_burn_in(self) -> None:
        now = datetime.now().timestamp()
        if now - self._burn_in_last >= _BURN_IN_INTERVAL_S:
            self._burn_in_step = (self._burn_in_step + 1) % len(_BURN_IN_POSITIONS)
            self._burn_in_x, self._burn_in_y = _BURN_IN_POSITIONS[self._burn_in_step]
            self._burn_in_last = now

    def _refresh_loop(self) -> None:
        while not self._stop_event.wait(timeout=_REFRESH_INTERVAL_S):
            try:
                if datetime.now().timestamp() < self._splash_until:
                    continue
                self._advance_burn_in()
                gs = self._core.general_settings
                timeout = gs.oled_screen_timeout
                if timeout > 0 and not self._screen_off and not gs.oled_custom_display:
                    elapsed = datetime.now().timestamp() - self._last_update_time
                    if elapsed >= timeout:
                        self._screen_off = True
                        self._send_oled_packet(self._protocol.build_brightness_packet(0))
                        continue

                if not self._screen_off:
                    if not gs.oled_custom_display:
                        self._send_oled_packet(self._protocol.build_return_to_ui_packet())
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
        image = self._renderer.render_status_image(
            **params,
            eq_scroll_offset=self._eq_scroll_offset,
            profile_scroll_offset=self._profile_scroll_offset,
        )
        with self._image_lock:
            self._current_image = image
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
                # Available width: 128px canvas minus 1px left margin = 127px
                max_offset = text_w - (self._renderer.WIDTH - 1)

                if max_offset <= 0:
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
                if overflow <= 0:
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
