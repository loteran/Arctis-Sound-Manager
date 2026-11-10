# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""Nova 3 Wireless settings readback, checked on a real headset (#331).

DarNCelsius set mic volume 10, sidetone 5 and the volume limiter on in ASM,
then probed: the 0x20 reply carries them at 61-63, as on the Nova 5.
"""
from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock

from ruamel.yaml import YAML

from arctis_sound_manager.config import DeviceConfiguration
from arctis_sound_manager.core import CoreEngine

DEVICES_DIR = Path(__file__).parent.parent / "src" / "arctis_sound_manager" / "devices"

AUDIO_SETTINGS = [int(b, 16) for b in (
    "20 1f 00 01 00 82 05 3e 00 01 00 82 05 7d 00 01 00 82 05 fa 00 01 00 82 05 "
    "f4 01 01 00 82 05 e8 03 01 00 82 05 d0 07 01 00 82 05 a0 0f 01 00 82 05 40 "
    "1f 01 00 82 05 80 3e 01 00 82 05 0a 05 01").split()]
UX_SETTINGS = [0xa0, 0x1e, 0x00, 0x00, 0x01] + [0] * 59


def test_the_probed_values_are_adopted():
    engine = MagicMock()
    engine.device_config = DeviceConfiguration(
        YAML(typ="safe").load(DEVICES_DIR / "nova_3_wireless.yaml"))
    engine.device_settings.settings = {}
    engine.device_settings.get.side_effect = (
        lambda name, default=None: engine.device_settings.settings.get(name, default))
    engine.device_settings.was_chosen_by_user.return_value = False
    engine._readback_done = threading.Event()
    engine._readback_pending = {0, 1}
    engine._readback_wins_window = False

    CoreEngine._absorb_settings_readback(engine, AUDIO_SETTINGS)
    CoreEngine._absorb_settings_readback(engine, UX_SETTINGS)

    assert engine.device_settings.settings == {
        "mic_volume": 10, "mic_side_tone": 5, "volume_limiter": 1, "pm_shutdown": 30}
    assert engine._readback_done.is_set()
