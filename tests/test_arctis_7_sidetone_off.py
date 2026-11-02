# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""
#337: GG on Windows turns the Arctis 7 sidetone off with 06 35 00 00 00
(spec: enabled, 0x00, amplitude). ASM fixed the enabled byte at 1, so its
"off" was sidetone enabled at amplitude 0. Same struct on the Arctis 1
Wireless.
"""
from __future__ import annotations

import pytest
from ruamel.yaml import YAML

from arctis_sound_manager.config import DeviceConfiguration
from arctis_sound_manager.core import CoreEngine
from tests.test_status_untagged_replies import DEVICES_DIR, _make_engine

_yaml = YAML(typ="safe")


@pytest.mark.parametrize("profile", ["arctis_7.yaml", "arctis_1_wireless.yaml"])
@pytest.mark.parametrize("value, frame", [(0, [0x06, 0x35, 0x00, 0x00, 0x00]),
                                          (0x0a, [0x06, 0x35, 0x01, 0x00, 0x0a])])
def test_sidetone_enabled_byte_follows_the_level(profile, value, frame):
    cfg = DeviceConfiguration(_yaml.load(DEVICES_DIR / profile))
    engine = _make_engine(cfg)
    setting = next(c for section in cfg.settings.values()
                   for c in section if c.name == "mic_side_tone")
    assert CoreEngine._resolve_update_sequence(engine, setting, value) == frame
