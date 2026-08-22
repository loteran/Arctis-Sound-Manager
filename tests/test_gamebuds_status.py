# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Issue #202: the Arctis GameBuds profile never had a `status.request` at all,
so the reporter had no battery percentage and no reliable connection state.
`base_arctis_gamebuds_dongle.device` (command 0xB0) gives both, but each bud
reports its own connect-status and battery byte — see
tests/test_status_offsets_vs_spec.py for the offset table those two bytes
each come from.

This file pins the two product decisions built on top of that raw data:

  * headset_battery_charge is the MIN of the two buds' battery levels, not an
    average or the max — the ear that dies first is the one that ends a
    stereo session.
  * headset_power_status is a `max` of left/right connect_status fed through
    the same on_off parser every other Arctis family uses. This mirrors
    SteelSeries' own firmware: `get-wireless-device-connection-status` in
    base_arctis_gamebuds_dongle.device computes exactly
    `(left_connect_status == 3) or (right_connect_status == 3)`. Since 3 is
    the top of the struct's documented 0-3 range, `max(left, right) == 3` is
    the same boolean as that OR. Using only one side would go offline
    whenever the user wears just the other bud.

Also exercises the `max`/`min` response_mapping combinator added to
ConfigStatusResponseMapping.get_status_values() to make a single-key
online_status possible for a two-earbud device, and checks it does not
disturb any plain-int mapping (every other profile in the repo).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from ruamel.yaml import YAML

from arctis_sound_manager.config import (
    ConfigStatusResponseMapping,
    DeviceConfiguration,
    parsed_status,
)

DEVICES = Path(__file__).parent.parent / "src" / "arctis_sound_manager" / "devices"


def _config(name: str) -> DeviceConfiguration:
    return DeviceConfiguration(YAML(typ="safe").load(DEVICES / name))


def _mapping(config: DeviceConfiguration, starts_with: int):
    return next(m for m in config.status.response_mapping
                if m.starts_with == starts_with)


def _frame(left_connect: int, right_connect: int, left_batt: int, right_batt: int,
           bt_left: int = 0, bt_right: int = 0) -> list[int]:
    """A synthetic 0xB0 headset_settings reply, ASM-offset order (no report
    id), padded out to a plausible 65-byte HID report."""
    frame = [0xb0, bt_left, bt_right, left_connect, right_connect, left_batt, right_batt]
    frame += [0] * (65 - len(frame))
    return frame


@pytest.fixture()
def gamebuds() -> DeviceConfiguration:
    return _config("gamebuds.yaml")


# ── Synthetic frame parse ────────────────────────────────────────────────────

def test_both_buds_connected_reports_the_lower_battery(gamebuds):
    frame = _frame(left_connect=3, right_connect=3, left_batt=40, right_batt=70)
    raw = _mapping(gamebuds, 0xB0).get_status_values(frame)
    parsed = parsed_status(raw, gamebuds)

    assert raw["left_battery_level"] == 40
    assert raw["right_battery_level"] == 70
    assert parsed["headset_battery_charge"] == 40  # min(40, 70), not avg(55) or max(70)
    assert parsed["headset_power_status"] == "on"


def test_battery_reading_survives_either_side_being_lower(gamebuds):
    """The MIN decision must not accidentally be 'always read the left bud'."""
    frame = _frame(left_connect=3, right_connect=3, left_batt=90, right_batt=15)
    parsed = parsed_status(_mapping(gamebuds, 0xB0).get_status_values(frame), gamebuds)

    assert parsed["headset_battery_charge"] == 15


def test_only_right_bud_worn_is_still_online(gamebuds):
    """Left bud in the case (not paired/searching, e.g. 0x00) must not make
    ASM think a headset with a connected right bud is offline (the failure
    mode this task explicitly warns about)."""
    frame = _frame(left_connect=0x00, right_connect=0x03, left_batt=0, right_batt=55)
    parsed = parsed_status(_mapping(gamebuds, 0xB0).get_status_values(frame), gamebuds)

    assert parsed["headset_power_status"] == "on"
    assert gamebuds.online_status is not None
    assert parsed[gamebuds.online_status.status_variable] == gamebuds.online_status.online_value \
        or parsed[gamebuds.online_status.status_variable] in {"on", "online"}


def test_only_left_bud_worn_is_still_online(gamebuds):
    """Same as above, mirrored: the combinator must not favor either side."""
    frame = _frame(left_connect=0x03, right_connect=0x01, left_batt=55, right_batt=0)
    parsed = parsed_status(_mapping(gamebuds, 0xB0).get_status_values(frame), gamebuds)

    assert parsed["headset_power_status"] == "on"


@pytest.mark.parametrize("left,right", [(0x00, 0x00), (0x01, 0x00), (0x02, 0x01), (0x02, 0x02)])
def test_neither_bud_connected_is_offline(gamebuds, left, right):
    """0x02 (PAIRED_NOT_CONNECTED) must not be mistaken for 'connected' — only
    0x03 counts, exactly like every other Arctis family's on_off parser
    (off=0x02, on=0x03)."""
    frame = _frame(left_connect=left, right_connect=right, left_batt=10, right_batt=10)
    parsed = parsed_status(_mapping(gamebuds, 0xB0).get_status_values(frame), gamebuds)

    assert parsed["headset_power_status"] == "off"


def test_is_device_online_true_when_either_bud_connected():
    """End-to-end through CoreEngine.is_device_online(), not just the parser,
    since that is what actually gates redirect_audio_on_connect/disconnect."""
    from arctis_sound_manager import core

    engine = core.CoreEngine.__new__(core.CoreEngine)
    engine.device_config = _config("gamebuds.yaml")
    engine.device_status = _mapping(engine.device_config, 0xB0).get_status_values(
        _frame(left_connect=0x00, right_connect=0x03, left_batt=0, right_batt=42))
    engine.general_settings = None
    engine.usb_device = object()

    assert engine.is_device_online() is True


def test_is_device_online_false_when_both_buds_disconnected():
    from arctis_sound_manager import core

    engine = core.CoreEngine.__new__(core.CoreEngine)
    engine.device_config = _config("gamebuds.yaml")
    engine.device_status = _mapping(engine.device_config, 0xB0).get_status_values(
        _frame(left_connect=0x01, right_connect=0x00, left_batt=0, right_batt=0))
    engine.general_settings = None
    engine.usb_device = object()

    assert engine.is_device_online() is False


# ── online_status / representation wiring ────────────────────────────────────

def test_online_status_points_at_a_mapped_and_represented_variable(gamebuds):
    """core.is_device_online() and dbus_service both key off status_variable
    matching a real representation entry — a typo here silently makes the
    device permanently 'online' or permanently invisible."""
    online = gamebuds.online_status
    assert online is not None
    assert online.status_variable == "headset_power_status"
    assert online.online_value == "online"
    assert online.status_variable in gamebuds.status_parse
    assert any(online.status_variable in section
               for section in gamebuds.status.representation.values())


def test_representation_lists_only_the_two_derived_variables(gamebuds):
    """Per-bud raw bytes (left/right_connect_status, left/right_battery_level,
    bt_left/right_connect_status) are real spec fields kept in
    response_mapping as combinator inputs, but nothing downstream of
    dbus_service consumes anything but headset_power_status /
    headset_battery_charge (see power_status.py, systray_app.py,
    home_page.py, dac_page.py) — listing the raw per-bud bytes here would be
    dead rows with no i18n key and no reader."""
    assert gamebuds.status.representation == {
        "headset": ["headset_power_status", "headset_battery_charge"],
    }


# ── The `max` / `min` combinator itself ─────────────────────────────────────

def test_max_combinator():
    mapping = ConfigStatusResponseMapping(starts_with=0xb0, connected={'max': [1, 2]})
    assert mapping.get_status_values([0xb0, 2, 3, 0]) == {'connected': 3}
    assert mapping.get_status_values([0xb0, 3, 2, 0]) == {'connected': 3}


def test_min_combinator():
    mapping = ConfigStatusResponseMapping(starts_with=0xb0, battery={'min': [1, 2]})
    assert mapping.get_status_values([0xb0, 40, 70, 0]) == {'battery': 40}
    assert mapping.get_status_values([0xb0, 70, 40, 0]) == {'battery': 40}


def test_combinator_skips_offsets_past_the_end_of_a_short_frame():
    mapping = ConfigStatusResponseMapping(starts_with=0xb0, battery={'min': [1, 99]})
    assert mapping.get_status_values([0xb0, 40]) == {'battery': 40}


def test_combinator_produces_nothing_if_all_its_offsets_are_out_of_range():
    mapping = ConfigStatusResponseMapping(starts_with=0xb0, battery={'min': [98, 99]})
    assert mapping.get_status_values([0xb0, 40]) == {}


def test_unknown_combinator_op_raises():
    mapping = ConfigStatusResponseMapping(starts_with=0xb0, battery={'avg': [1, 2]})
    with pytest.raises(ValueError):
        mapping.get_status_values([0xb0, 40, 70])


def test_combinator_does_not_disturb_plain_int_mappings_elsewhere():
    """Every existing profile maps plain ints only — the new dict branch in
    get_status_values() must be additive, not a behaviour change for the
    other ~15 device YAMLs."""
    for device_yaml in sorted(DEVICES.glob("*.yaml")):
        config = DeviceConfiguration(YAML(typ="safe").load(device_yaml))
        if config.status is None:
            continue
        for mapping in config.status.response_mapping:
            for key, value in mapping.__dict__.items():
                if key == "starts_with":
                    continue
                if device_yaml.name == "gamebuds.yaml" and key in (
                        "headset_power_status", "headset_battery_charge"):
                    assert isinstance(value, dict)
                else:
                    assert isinstance(value, int), (
                        f"{device_yaml.name}: {key!r} is {value!r}, expected a plain "
                        "int offset"
                    )
