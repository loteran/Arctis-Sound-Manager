# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for power_status — normalizing headset_power_status across the two
device YAML vocabularies ('off'/'on' vs 'offline'/'online'/'cable_charging')."""

import json

import pytest

from arctis_sound_manager.power_status import (HeadsetPower,
                                                extract_power_status,
                                                normalize_power_value,
                                                power_status_from_json)


# ── normalize_power_value ──────────────────────────────────────────────────

@pytest.mark.parametrize("value,expected", [
    ("off", HeadsetPower.OFF),
    ("offline", HeadsetPower.OFF),
    ("OFF", HeadsetPower.OFF),       # case-insensitive
    (" offline ", HeadsetPower.OFF), # tolerate surrounding whitespace
    ("on", HeadsetPower.ON),
    ("online", HeadsetPower.ON),
    ("cable_charging", HeadsetPower.ON),
    ("standby", HeadsetPower.UNKNOWN),   # Nova Elite value with no rule — not "off"
    ("something_else", HeadsetPower.UNKNOWN),
    (None, HeadsetPower.UNKNOWN),
    (42, HeadsetPower.UNKNOWN),
    ("", HeadsetPower.UNKNOWN),
])
def test_normalize_power_value(value, expected):
    assert normalize_power_value(value) == expected


# ── extract_power_status ───────────────────────────────────────────────────

def test_extract_power_status_off_vocabulary():
    status = {"headset": {"headset_power_status": {"value": "off"}}}
    assert extract_power_status(status) == HeadsetPower.OFF


def test_extract_power_status_offline_vocabulary():
    status = {"headset": {"headset_power_status": {"value": "offline"}}}
    assert extract_power_status(status) == HeadsetPower.OFF


def test_extract_power_status_online_vocabulary():
    status = {"headset": {"headset_power_status": {"value": "online"}}}
    assert extract_power_status(status) == HeadsetPower.ON


def test_extract_power_status_cable_charging_is_on():
    status = {"headset": {"headset_power_status": {"value": "cable_charging"}}}
    assert extract_power_status(status) == HeadsetPower.ON


def test_extract_power_status_missing_key_is_unknown():
    status = {"headset": {"headset_battery_charge": {"value": 42, "type": "percentage"}}}
    assert extract_power_status(status) == HeadsetPower.UNKNOWN


def test_extract_power_status_empty_dict_is_unknown():
    assert extract_power_status({}) == HeadsetPower.UNKNOWN


def test_extract_power_status_non_dict_is_unknown():
    assert extract_power_status(None) == HeadsetPower.UNKNOWN
    assert extract_power_status("not a dict") == HeadsetPower.UNKNOWN


def test_extract_power_status_searches_all_categories():
    status = {
        "mic": {"mic_status": {"value": "unmuted"}},
        "headset": {"headset_power_status": {"value": "offline"}},
    }
    assert extract_power_status(status) == HeadsetPower.OFF


# ── power_status_from_json ─────────────────────────────────────────────────

def test_power_status_from_json_valid():
    payload = json.dumps({"headset": {"headset_power_status": {"value": "online"}}})
    assert power_status_from_json(payload) == HeadsetPower.ON


def test_power_status_from_json_invalid_json():
    assert power_status_from_json("not valid json{{{") == HeadsetPower.UNKNOWN


def test_power_status_from_json_wrong_type():
    assert power_status_from_json(None) == HeadsetPower.UNKNOWN
