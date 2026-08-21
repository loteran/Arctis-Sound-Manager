# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
test_nova_elite_sidetone.py — the Nova Elite sidetone must reach a true off (#201).

Byte 2 of the 0x38 / 0x39 frames is the on/off state and byte 3 the level 1-10.
Both were hardcoded on, so the slider bottomed out at level 1 — loud enough that
a keyboard next to the boom mic came back through the earcups — and neither
sidetone could be turned off. This is the same defect #161 fixed on the Nova Pro
Omni, and the fix is the same: derive the state byte from the setting.

Two places had to change, and both are locked here, because fixing only one
leaves the bug: the slider's own update_sequence, and the init sequence ASM
replays at every device connection.
"""
from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from ruamel.yaml import YAML

from arctis_sound_manager.config import DeviceConfiguration
from arctis_sound_manager.core import CoreEngine
from arctis_sound_manager.settings import DeviceSettings

ELITE_YAML = (Path(__file__).parent.parent / "src" / "arctis_sound_manager"
              / "devices" / "nova_elite.yaml")

SIDETONES = {"mic_sidetone_boom": 0x38, "mic_sidetone_ear": 0x39}


@pytest.fixture(scope="module")
def raw() -> dict:
    return YAML(typ="safe").load(ELITE_YAML)["device"]


@pytest.fixture(scope="module")
def config(raw) -> DeviceConfiguration:
    return DeviceConfiguration({"device": raw})


@pytest.fixture()
def engine(config):
    engine = MagicMock()
    engine._device_lock = threading.RLock()
    engine.device_config = config
    engine.device_settings = DeviceSettings(config.vendor_id, config.product_ids[0])
    engine._setting_default = lambda name: CoreEngine._setting_default(engine, name)
    return engine


def _setting(raw: dict, name: str) -> dict:
    return next(section[name] for section in raw["settings"].values() if name in section)


@pytest.mark.parametrize("name,opcode", SIDETONES.items())
def test_slider_reaches_off(raw, name, opcode):
    """The slider must start at 0, and 0 must be labelled as an off, not 10%."""
    setting = _setting(raw, name)
    assert setting["min"] == 0x00, f"{name} cannot be turned off"
    assert setting["min_label"] == "off"
    assert setting["update_sequence"] == [
        0x01, opcode, "value.enabled", "value.at_least_1"]


@pytest.mark.parametrize("name,opcode", SIDETONES.items())
def test_slider_resolves_state_and_level(engine, config, name, opcode):
    """0 writes state=0; any level writes state=1 with that level.

    The level byte stays >= 1 even while off — the firmware misbehaves on a 0
    level, which is why value.at_least_1 exists at all.
    """
    setting = next(s for section in config.settings.values()
                   for s in section if s.name == name)
    resolve = CoreEngine._resolve_update_sequence

    assert resolve(engine, setting, 0) == [0x01, opcode, 0x00, 0x01]
    assert resolve(engine, setting, 1) == [0x01, opcode, 0x01, 0x01]
    assert resolve(engine, setting, 7) == [0x01, opcode, 0x01, 0x07]
    assert resolve(engine, setting, 10) == [0x01, opcode, 0x01, 0x0a]


@pytest.mark.parametrize("name,opcode", SIDETONES.items())
def test_init_does_not_force_the_state_byte_on(raw, name, opcode):
    """The init sequence must derive the state byte too.

    Half a fix is no fix: a slider that can write state=0 is undone at the next
    connection by an init that hardcodes state=1.
    """
    command = next(c for c in raw["device_init"]
                   if len(c) > 1 and c[0] == 0x01 and c[1] == opcode)
    assert command == [
        0x01, opcode, f"settings.{name}.enabled", f"settings.{name}.at_least_1"]


@pytest.mark.parametrize("name,opcode", SIDETONES.items())
def test_init_resolves_the_saved_choice(engine, name, opcode):
    """A saved off survives the init; so does a saved level."""
    command = [0x01, opcode, f"settings.{name}.enabled", f"settings.{name}.at_least_1"]

    engine.device_settings.settings[name] = 0
    assert CoreEngine.translate_init_bytes(engine, list(command)) == [
        0x01, opcode, 0x00, 0x01]

    engine.device_settings.settings[name] = 8
    assert CoreEngine.translate_init_bytes(engine, list(command)) == [
        0x01, opcode, 0x01, 0x08]


def test_off_reads_back_as_zero_percent(config):
    """A sidetone that is off reports level 1, which must display as 0%.

    The status frame carries the level byte, not the state, so the only thing
    keeping the panel honest is that the percentage scale starts at 1 — an off
    sidetone would otherwise read as 10% on a panel the user just muted.
    """
    from arctis_sound_manager.config import parsed_status

    off = parsed_status({name: 1 for name in SIDETONES}, config)
    assert all(value == 0 for value in off.values()), off

    loud = parsed_status({name: 10 for name in SIDETONES}, config)
    assert all(value == 100 for value in loud.values()), loud
