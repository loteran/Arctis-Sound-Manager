# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Arctis Pro Wireless base's screen, driven as its siberia-840 spec
lays it out (#305): 0xD0/0xD1 take the screen, 0xD2 draws a whole 128x48
frame in one Feature report, 0xD0 00 00 hands it back.
"""

from __future__ import annotations

import threading
import time as time_mod
from types import SimpleNamespace

import pytest

from arctis_sound_manager.config import load_device_configurations
from arctis_sound_manager.oled_manager import OledManager
from arctis_sound_manager.oled_protocol import OledProtocol, SiberiaOledProtocol


def _pro_wireless():
    for config in load_device_configurations():
        if config.name == "SteelSeries Arctis Pro Wireless":
            return config
    raise AssertionError("no Arctis Pro Wireless profile")


def _protocol(packing: str = "column_msb") -> SiberiaOledProtocol:
    cfg = _pro_wireless().oled
    return SiberiaOledProtocol(
        report_id=cfg.report_id, width=cfg.width, height=cfg.height,
        frame_report_size=cfg.frame_report_size,
        control_report_size=cfg.control_report_size, packing=packing)


def _one_pixel(x: int, y: int, width: int = 128, height: int = 48) -> bytes:
    data = bytearray(((width + 7) // 8) * height)
    data[y * ((width + 7) // 8) + x // 8] |= 0x80 >> (x % 8)
    return bytes(data)


def test_profile_declares_the_siberia_screen():
    cfg = _pro_wireless()
    assert cfg.oled is not None
    assert cfg.oled.protocol == "siberia_840"
    assert (cfg.oled.width, cfg.oled.height, cfg.oled.interface) == (128, 48, 0)
    assert "gamedac" in cfg.status.representation


def test_confirmed_layout_takes_the_screen():
    # column_msb drew noise on the reporter's base (#305); oled_page_probe.py
    # confirmed GG's page layout on hardware (#363), so the screen is ASM's.
    config = _pro_wireless()
    assert config.oled.verified is True and config.oled.packing == "page_lsb"


def test_every_screen_is_verified():
    for config in load_device_configurations():
        if config.oled is not None:
            assert config.oled.verified, config.name


def test_one_frame_one_feature_report_starting_with_d2():
    packets = _protocol().build_frame_packets(bytes(768), 128, 48)

    assert len(packets) == 1
    # Report id 0x00 is not on the wire: 1025-byte chunk -> 1024 bytes.
    assert len(packets[0]) == 1024
    assert packets[0][:2] == [0xD2, 0x00]


def test_preamble_takes_the_screen_as_the_spec_does():
    preamble = _protocol().build_preamble_packets()

    assert [p[:3] for p in preamble] == [[0xD0, 0x01, 0x0F], [0xD1, 0x00, 0x10]]
    assert all(len(p) == 32 for p in preamble)


def test_brightness_and_hand_back():
    p = _protocol()
    assert p.build_brightness_packet(7)[:3] == [0x85, 0xAA, 7]
    assert p.build_brightness_packet(42)[:3] == [0x85, 0xAA, 10]
    assert p.build_return_to_ui_packet()[:3] == [0xD0, 0x00, 0x00]


@pytest.mark.parametrize("packing,index,byte", [
    # pixel (9, 10): column 9, page 1, bit 2 of its page / bit 1 of its row byte
    ("column_msb", 9 * 6 + 1, 0x80 >> 2),
    ("column_lsb", 9 * 6 + 1, 0x01 << 2),
    ("row_msb", 10 * 16 + 1, 0x80 >> 1),
    ("row_lsb", 10 * 16 + 1, 0x01 << 1),
])
def test_each_packing_puts_a_pixel_where_it_says(packing, index, byte):
    body = SiberiaOledProtocol.pack(_one_pixel(9, 10), 128, 48, packing)

    assert len(body) == 768
    assert [i for i, b in enumerate(body) if b] == [index]
    assert body[index] == byte


def test_unknown_packing_is_refused():
    with pytest.raises(ValueError):
        _protocol(packing="diagonal")


def test_nova_protocol_has_no_preamble():
    assert OledProtocol().build_preamble_packets() == []


class _Recorder:
    def __init__(self) -> None:
        self.sent: list[tuple[int, list[int]]] = []

    def ctrl_transfer(self, bm, req, wvalue, index, packet, timeout=None):
        self.sent.append((wvalue, list(packet[:3])))


def _manager(monkeypatch, clock: list[float]) -> tuple[OledManager, _Recorder]:
    device = _Recorder()
    core = SimpleNamespace(
        usb_device=device, _usb_write_lock=threading.Lock(),
        device_config=_pro_wireless(),
        general_settings=SimpleNamespace(oled_brightness=5, oled_custom_display=True),
    )
    monkeypatch.setattr(time_mod, "sleep", lambda *_: None)
    monkeypatch.setattr(time_mod, "monotonic", lambda: clock[0])
    manager = OledManager(core)
    monkeypatch.setattr(manager._renderer, "crop_frame", lambda *a, **k: bytes(768))
    manager._current_image = object()
    return manager, device


def test_frames_take_the_screen_first_then_only_when_due(monkeypatch):
    clock = [100.0]
    manager, device = _manager(monkeypatch, clock)

    manager._send_current_frame()
    manager._send_current_frame()
    clock[0] += SiberiaOledProtocol.PREAMBLE_REFRESH_S
    manager._send_current_frame()

    opcodes = [p[0] for _, p in device.sent]
    assert opcodes == [0xD0, 0xD1, 0xD2, 0xD2, 0xD0, 0xD1, 0xD2]
    # Preamble as Output reports, frames as Feature reports.
    assert [w >> 8 for w, _ in device.sent[:3]] == [0x02, 0x02, 0x03]


def test_handing_the_screen_back_means_taking_it_again(monkeypatch):
    clock = [100.0]
    manager, device = _manager(monkeypatch, clock)

    manager._send_current_frame()
    manager._hand_back_screen()
    manager._send_current_frame()

    assert [p[:2] for _, p in device.sent] == [
        [0xD0, 0x01], [0xD1, 0x00], [0xD2, 0x00],
        [0xD0, 0x00],
        [0xD0, 0x01], [0xD1, 0x00], [0xD2, 0x00],
    ]
