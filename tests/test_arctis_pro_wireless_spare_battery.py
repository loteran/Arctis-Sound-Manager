# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""#363: the base's 0x42aa reply is not the spare battery's level (it read
4 while the base showed 2 bars of 4). ASM shows only whether one is in."""
from __future__ import annotations

from ruamel.yaml import YAML

from arctis_sound_manager.config import DeviceConfiguration, parsed_status
from tests.test_status_untagged_replies import DEVICES_DIR, _make_engine, _read, _send

_yaml = YAML(typ="safe")


def _poll(frames):
    cfg = DeviceConfiguration(_yaml.load(DEVICES_DIR / "arctis_pro_wireless.yaml"))
    engine = _make_engine(cfg)
    for request, frame in zip((0x41aa, 0x40aa, 0x42aa), frames):
        _send(engine, request)
        _read(engine, frame)
    return parsed_status(engine.device_status, cfg)


def test_battery_in_the_base_is_not_a_percentage():
    # Anotherdoomguy's log: headset 2/4, spare slot 04 with a 2-bar battery.
    parsed = _poll([[0x04, 0x02], [0x02], [0x04]])
    assert parsed["headset_battery_charge"] == 50
    assert parsed["charge_slot_battery"] == "battery_in_base"
    assert "charge_slot_battery_charge" not in parsed


def test_empty_slot():
    assert _poll([[0x04, 0x02], [0x02], [0x00]])["charge_slot_battery"] == "no_battery"
