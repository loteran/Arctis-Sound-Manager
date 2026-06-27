# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""
translate_init_bytes() must never push a stray 0 to the device when a saved
setting value is missing. A 0 min-caps the control on the hardware — e.g. the
mic volume dropping to 1/10 after a reconnect/update instead of the user's
saved level. Missing values must fall back to the profile-declared default.
"""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock

from ruamel.yaml import YAML

from arctis_sound_manager.config import DeviceConfiguration

DEVICES_DIR = Path(__file__).parent.parent / "src" / "arctis_sound_manager" / "devices"
_yaml = YAML(typ="safe")


def _load_config(name: str) -> DeviceConfiguration:
    return DeviceConfiguration(_yaml.load(DEVICES_DIR / name))


def _make_engine(cfg: DeviceConfiguration, device_settings) -> MagicMock:
    from arctis_sound_manager.core import CoreEngine

    engine = MagicMock()
    engine._device_lock = threading.RLock()
    engine.device_config = cfg
    engine.device_settings = device_settings
    # Bind the real methods — a bare MagicMock would shadow them with mocks.
    engine._setting_default = lambda name: CoreEngine._setting_default(engine, name)
    return engine


def test_missing_mic_volume_falls_back_to_profile_default():
    from arctis_sound_manager.core import CoreEngine
    from arctis_sound_manager.settings import DeviceSettings

    cfg = _load_config("nova_pro_wireless.yaml")
    # No saved value loaded for mic_volume.
    ds = DeviceSettings(cfg.vendor_id, cfg.product_ids[0])
    engine = _make_engine(cfg, ds)

    seq = CoreEngine.translate_init_bytes(engine, [0x06, 0x37, "settings.mic_volume"])

    # Profile default is 0x0a (100%), NOT 0 (which would land the mic at 1/10).
    assert seq == [0x06, 0x37, 0x0A]


def test_saved_mic_volume_is_used_when_present():
    from arctis_sound_manager.core import CoreEngine
    from arctis_sound_manager.settings import DeviceSettings

    cfg = _load_config("nova_pro_wireless.yaml")
    ds = DeviceSettings(cfg.vendor_id, cfg.product_ids[0])
    ds.mic_volume = 7
    engine = _make_engine(cfg, ds)

    seq = CoreEngine.translate_init_bytes(engine, [0x06, 0x37, "settings.mic_volume"])

    assert seq == [0x06, 0x37, 7]


def test_setting_default_returns_zero_for_unknown_setting():
    from arctis_sound_manager.core import CoreEngine
    from arctis_sound_manager.settings import DeviceSettings

    cfg = _load_config("nova_pro_wireless.yaml")
    engine = _make_engine(cfg, DeviceSettings(cfg.vendor_id, cfg.product_ids[0]))

    assert CoreEngine._setting_default(engine, "does_not_exist") == 0
