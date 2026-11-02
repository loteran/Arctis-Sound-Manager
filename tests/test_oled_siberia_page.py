# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""#363 / #305: the Arctis Pro Wireless base takes GG's page layout,
byte (y // 8) * width + x, bit y % 8 (bit 0 = top), confirmed on hardware
by oled_page_probe.py. ASM's packing must match the probe's."""
from __future__ import annotations

import importlib.util
import random
from pathlib import Path

from ruamel.yaml import YAML

from arctis_sound_manager.config import DeviceConfiguration
from arctis_sound_manager.oled_protocol import SiberiaOledProtocol

ROOT = Path(__file__).parent.parent
_spec = importlib.util.spec_from_file_location(
    "oled_page_probe", ROOT / "scripts/reverse-engineering/oled_page_probe.py")
probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(probe)


def test_page_lsb_matches_the_probe_that_looked_right():
    rnd = random.Random(363)
    w, h = 128, 48
    image = [[rnd.random() < 0.3 for _ in range(w)] for _ in range(h)]
    row_major = bytearray((w // 8) * h)
    for y in range(h):
        for x in range(w):
            if image[y][x]:
                row_major[y * (w // 8) + x // 8] |= 0x80 >> (x % 8)
    assert bytes(SiberiaOledProtocol.pack(bytes(row_major), w, h, "page_lsb")) \
        == probe.pack_pages(image)


def test_pro_wireless_profile_uses_it_and_is_verified():
    cfg = DeviceConfiguration(YAML(typ="safe").load(
        ROOT / "src/arctis_sound_manager/devices/arctis_pro_wireless.yaml"))
    assert cfg.oled.packing == "page_lsb" and cfg.oled.verified
