# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""Frames the Nova Pro Wireless base pushes by itself (#337 GG capture)."""
from __future__ import annotations

from ruamel.yaml import YAML

from arctis_sound_manager.config import DeviceConfiguration, parsed_status
from tests.test_status_untagged_replies import DEVICES_DIR, _make_engine, _read

_yaml = YAML(typ="safe")


def test_nova_pro_wireless_battery_push():
    """#337 GG capture: the base pushes 07 b7 <headset> <spare> <state>."""
    engine = _make_engine(DeviceConfiguration(_yaml.load(DEVICES_DIR / "nova_pro_wireless.yaml")))
    _read(engine, [0x07, 0xb7, 0x08, 0x07, 0x02])
    parsed = parsed_status(engine.device_status, engine.device_config)
    assert parsed["headset_battery_charge"] == 100
    assert parsed["headset_power_status"] == "cable_charging"
    _read(engine, [0x07, 0xb7, 0x00, 0x07, 0x01])
    assert parsed_status(engine.device_status, engine.device_config)["headset_power_status"] == "offline"
