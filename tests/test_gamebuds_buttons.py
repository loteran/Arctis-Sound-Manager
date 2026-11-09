# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""GameBuds button (touch gesture) remapping: one 0xc9 frame, read back from 0xb0."""
from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock

from ruamel.yaml import YAML

from arctis_sound_manager.config import DeviceConfiguration
from arctis_sound_manager.core import CoreEngine
from arctis_sound_manager.settings import DeviceSettings

DEVICES = Path(__file__).parent.parent / "src" / "arctis_sound_manager" / "devices"

PROBE = bytes.fromhex(
    "b0 00 00 03 03 2e 60 08 02 00 00 1e 01 03 cd cd 50 b5 b5 52 00 00 00 49 49 50 b6 b6 52"
    " 00 00 00 00 00 00 00 00 00 00 00 00 01 00 00"
)


def _config(name="gamebuds.yaml"):
    return DeviceConfiguration(YAML(typ="safe").load(DEVICES / name))


def _engine(user_chosen=()):
    config = _config()
    settings = DeviceSettings(0x1038, 0x230A)
    for section in config.settings.values():
        for s in section:
            settings.settings[s.name] = s.default_value
    for n in user_chosen:
        settings._user_chosen.add(n)
    sent = []
    engine = MagicMock()
    engine._device_lock = threading.RLock()
    engine.device_config = config
    engine.device_settings = settings
    engine.logger = MagicMock()
    engine._readback_pending = {0}
    engine._readback_wins_window = False
    engine.get_command_endpoint_address.return_value = 0
    engine._resolve_update_sequence = lambda c, v: CoreEngine._resolve_update_sequence(engine, c, v)
    engine._send_setting = lambda c, v: sent.append(engine._resolve_update_sequence(c, v))
    engine.sent = sent
    return engine


def _setting(engine, name):
    return next(c for s in engine.device_config.settings.values() for c in s if c.name == name)


def test_defaults_build_the_captured_frame():
    e = _engine()
    seq = e._resolve_update_sequence(_setting(e, "right_1_tap"), 0xCD)
    assert len(seq) == 27
    assert bytes(seq).hex() == ("c9" "cdcd50" "b5b552" "000000" "494950" "b6b652" "000000" "000000" "000000" "0000")


def test_changing_left_1_tap_matches_gg_capture():
    e = _engine()
    e.device_settings.settings["left_1_tap"] = 0x51  # observer-less dict here
    seq = e._resolve_update_sequence(_setting(e, "left_1_tap"), 0x51)
    assert bytes(seq).hex().startswith("c9cdcd50b5b552000000" "515150" "b6b652")


def test_change_touches_rf_bt_only_and_keeps_call_and_hold():
    e = _engine()
    e.device_settings.settings["right_med_hold_call"] = 0x77
    e.device_settings.settings["left_hold"] = 1
    base = e._resolve_update_sequence(_setting(e, "right_1_tap"), 0xCD)
    e.device_settings.settings["right_2_tap"] = 0xE9
    new = e._resolve_update_sequence(_setting(e, "right_2_tap"), 0xE9)
    diff = [i for i in range(27) if base[i] != new[i]]
    assert diff == [4, 5]            # rf and bt of right_2_tap
    assert new[21] == 0x77 and new[26] == 1


def test_readback_adopts_probe_values_silently():
    e = _engine()
    e.device_settings.settings["right_1_tap"] = 0
    e.device_settings.settings["left_1_tap"] = 0
    CoreEngine._absorb_settings_readback(e, list(PROBE))
    s = e.device_settings
    assert s.get("right_1_tap") == 0xCD and s.get("right_2_tap") == 0xB5
    assert s.get("left_1_tap") == 0x49 and s.get("left_2_tap") == 0xB6
    assert s.get("right_3_tap") == 0 and s.get("left_1_tap_call") == 0x50
    assert s.get("left_2_tap_call") == 0x52 and s.get("right_hold") == 0
    assert s.get("left_hold") == 0 and s.get("right_hold") == 0


def test_all_zero_table_is_not_adopted():
    e = _engine()
    frame = list(PROBE)
    frame[0x0E:0x14] = [0] * 6
    CoreEngine._absorb_settings_readback(e, frame)
    assert e.device_settings.get("right_1_tap") == 0xCD  # default kept
    assert e.device_settings.get("right_1_tap_call") == 0x50


def test_user_choice_kept_hidden_values_follow_device():
    e = _engine(user_chosen={"right_1_tap", "right_1_tap_call"})
    e.device_settings.settings["right_1_tap"] = 0xE9
    e.device_settings.settings["right_1_tap_call"] = 0x11
    CoreEngine._absorb_settings_readback(e, list(PROBE))
    assert e.device_settings.get("right_1_tap") == 0xE9
    assert e.device_settings.get("right_1_tap_call") == 0x50


def test_adoption_writes_nothing_to_the_device():
    e = _engine()
    fired = []
    e.device_settings.settings.add_observer(lambda k, v: fired.append(k))
    e.device_settings.settings["right_1_tap"] = 0
    fired.clear()
    CoreEngine._absorb_settings_readback(e, list(PROBE))
    assert fired == []


def test_status_mapping_unaffected_and_wire_status_still_parses():
    cfg = _config()
    assert cfg.settings_readback[0].request == [0xB0]
    assert cfg.status.request == 0xB0


def test_hidden_settings_send_nothing():
    e = _engine()
    e.logger = MagicMock()
    e.usb_device = object()
    e._send_setting = MagicMock()
    e.device_config = e.device_config
    CoreEngine.on_setting_changed(e, "left_hold", 1)
    e._send_setting.assert_not_called()


def test_buttons_card_only_for_gamebuds_profiles():
    """The Headset page makes one card per profile section: only profiles
    declaring `buttons` get a Buttons card."""
    for f in sorted(DEVICES.glob("*.yaml")):
        cfg = _config(f.name)
        has = "buttons" in cfg.settings
        assert has == (f.name == "gamebuds.yaml"), f.name
    sections = _config().settings["buttons"]
    visible = [c.name for c in sections if not getattr(c, "hidden", False)]
    assert len(visible) == 8 and len(sections) == 18
