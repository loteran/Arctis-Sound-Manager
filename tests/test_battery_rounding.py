# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
test_battery_rounding.py — don't show precision the hardware doesn't have.

Several headsets report their battery in a handful of steps rather than as a
real percentage. The Nova Pro Wireless has nine levels, which scaled to
0/12/25/37/50/62/75/87/100 — numbers that read like a measurement when each
step really covers about twelve points.

`round_to` lets a profile state its true resolution. It must stay opt-in: the
same parser also scales station volume and mic levels, which are genuinely
continuous, and profiles whose steps already land on round values don't need it.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from ruamel.yaml import YAML

from arctis_sound_manager.config import DeviceConfiguration, parsed_status
from arctis_sound_manager.status_parser_fn import percentage

DEVICES = Path(__file__).parent.parent / "src" / "arctis_sound_manager" / "devices"


def test_nine_level_battery_lands_on_round_numbers():
    values = [percentage(0, 8, v, round_to=10) for v in range(9)]

    assert values == [0, 10, 30, 40, 50, 60, 80, 90, 100]
    assert all(v % 10 == 0 for v in values)
    assert len(set(values)) == 9, "levels must stay distinguishable"


def test_empty_and_full_stay_exact():
    """A rounded 0 or 100 would be alarming or misleading."""
    assert percentage(0, 8, 0, round_to=10) == 0
    assert percentage(0, 8, 8, round_to=10) == 100


def test_rounding_is_opt_in():
    assert percentage(0, 8, 5) == 62
    assert percentage(0, 8, 5, round_to=0) == 62
    assert percentage(0, 8, 5, round_to=1) == 62


def test_continuous_scales_are_untouched():
    """Station volume uses the same parser with an inverted range.

    perc_min always maps to 0% and perc_max to 100%, regardless of which one
    is numerically larger — station_volume's raw byte counts *down* as the
    dial turns up (perc_min=56, perc_max=0), confirmed against real Nova Pro
    Wireless hardware (#255).
    """
    assert [percentage(56, 0, v) for v in (0, 28, 56)] == [100, 50, 0]
    assert percentage(1, 100, 64) == 63


def test_nova_pro_wireless_profile_rounds_both_batteries():
    config = DeviceConfiguration(
        YAML(typ="safe").load(DEVICES / "nova_pro_wireless.yaml"))

    for level, expected in enumerate([0, 10, 30, 40, 50, 60, 80, 90, 100]):
        parsed = parsed_status({"headset_battery_charge": level,
                                "charge_slot_battery_charge": level}, config)
        assert parsed["headset_battery_charge"] == expected
        assert parsed["charge_slot_battery_charge"] == expected


@pytest.mark.parametrize("profile", [
    "nova_7p_perc_battery.yaml",   # real 0-100 percentage
    "nova_7_discrete_battery.yaml",  # 5 steps, already round
    "nova_4.yaml",
])
def test_other_profiles_keep_their_values(profile):
    """Only profiles that ask for it may be rounded."""
    config = DeviceConfiguration(YAML(typ="safe").load(DEVICES / profile))
    parse = config.status_parse["headset_battery_charge"]

    assert "round_to" not in parse.init_kwargs


def test_percentage_battery_is_reported_to_the_point():
    """A headset that measures 1 % steps must not be rounded away."""
    config = DeviceConfiguration(
        YAML(typ="safe").load(DEVICES / "nova_7p_perc_battery.yaml"))

    assert parsed_status({"headset_battery_charge": 64}, config)[
        "headset_battery_charge"] == 64
