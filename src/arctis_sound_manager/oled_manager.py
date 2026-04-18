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

from arctis_sound_manager.oled_protocol import OledProtocol
from arctis_sound_manager.oled_renderer import OledRenderer
from arctis_sound_manager import profile_manager

_CFG = Path.home() / ".config" / "arctis_manager"


def _active_eq_preset(channel: str) -> str:
    f = _CFG / f".sonar_preset_{channel}"
    return f.read_text().strip() if f.exists() else "Flat"

_REFRESH_INTERVAL_S = 5.0
_OLED_INTERFACE = 4
_OLED_WVALUE = 0x0300

logger = logging.getLogger(__name__)


class OledManager:
    def __init__(self, core: CoreEngine) -> None:
        self._core = core
        self._protocol = OledProtocol()
        self._renderer = OledRenderer()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._blink = False

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._refresh_loop,
            name="OledRefresh",
            daemon=True,
        )
        self._thread.start()
        self.set_brightness(self._core.general_settings.oled_brightness)
        logger.info("OledManager started (interval=%.1fs)", _REFRESH_INTERVAL_S)

    def set_brightness(self, level: int) -> None:
        packet = self._protocol.build_brightness_packet(level)
        self._send_oled_packet(packet)

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        logger.info("OledManager stopped")

    def update_display(self) -> None:
        status = self._core.device_status
        if status is None:
            return

        battery = int(status.get("headset_battery_charge", -1))
        charging = bool(status.get("headset_power_status", False))
        sidetone = int(status.get("sidetone", 0))

        device_config = self._core.device_config
        active_profile = profile_manager.active_profile_name() or (
            device_config.name if device_config else "Unknown"
        )

        self._blink = not self._blink
        time_str = datetime.now().strftime("%H:%M")
        eq_preset = _active_eq_preset("game")

        frame = self._renderer.render_status(
            battery_percent=battery,
            charging=charging,
            time_str=time_str,
            active_profile=active_profile,
            sidetone_level=sidetone,
            blink_state=self._blink,
            eq_preset=eq_preset,
        )
        packets = self._protocol.build_frame_packets(
            frame, self._protocol.DISPLAY_WIDTH, self._protocol.DISPLAY_HEIGHT
        )

        for packet in packets:
            self._send_oled_packet(packet)

    def _send_oled_packet(self, packet: list[int]) -> None:
        usb_device = self._core.usb_device
        if usb_device is None:
            return

        bmRequestType = usb.util.build_request_type(
            direction=usb.util.CTRL_OUT,
            type=usb.util.CTRL_TYPE_CLASS,
            recipient=usb.util.CTRL_RECIPIENT_INTERFACE,
        )
        try:
            usb_device.ctrl_transfer(bmRequestType, 0x09, _OLED_WVALUE, _OLED_INTERFACE, packet)
        except usb.core.USBError as e:
            logger.warning("OLED USB error: %s", e)

    def _refresh_loop(self) -> None:
        while not self._stop_event.wait(timeout=_REFRESH_INTERVAL_S):
            try:
                self.update_display()
            except Exception as e:
                logger.warning("OLED refresh error: %s", e)
