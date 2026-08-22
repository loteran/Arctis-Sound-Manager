# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

from arctis_sound_manager.status_parser_fn import int_int_mapping, int_str_mapping, on_off, percentage


def test_percentage():
    fn = percentage
    assert getattr(fn, '_status_type') == 'percentage'

    assert fn(0, 100, 0) == 0
    assert fn(-56, 0, -56) == 0

    assert fn(0, 100, 75) == 75
    assert fn(-200, 0, -50) == 75

    assert fn(0, 100, 100) == 100
    assert fn(-123, 123, 123) == 100

def test_percentage_clamps_out_of_range_values_regardless_of_round_to():
    """RAPPORT-CHAOS-ASM.md HW-2: the clamp used to live inside
    `if round_to > 1:`, so any profile calling percentage() without round_to —
    media_mix, chat_mix, station_volume, most headset_battery_charge entries —
    was unclamped. A value outside [perc_min, perc_max], e.g. a battery byte
    read against the wrong sibling profile's scale, produced an absurd
    percentage instead of being capped at 0-100.
    """
    fn = percentage

    # The exact reproduction from the report: a real 76% on a 0-4 scale
    # misfiled as 0-100 used to render as 1900%.
    assert fn(perc_min=0, perc_max=4, value=76) == 100
    assert fn(perc_min=0, perc_max=4, value=76, round_to=10) == 100

    # Below-range values must not go negative either, with or without
    # round_to.
    assert fn(perc_min=10, perc_max=20, value=0) == 0
    assert fn(perc_min=10, perc_max=20, value=0, round_to=10) == 0

    # In-range values are untouched by the clamp, with or without round_to —
    # the fix must be behaviour-neutral for every currently-correct profile.
    assert fn(0, 100, 75) == 75
    assert fn(0, 8, 5, round_to=10) == 60


def test_on_off():
    fn = on_off
    assert getattr(fn, '_status_type') == 'on_off'

    assert fn(0x01, 0x01, 0) == 'on'
    assert fn(0, 1, 0) == 'off'
    assert fn(1, 1, 3) == 'on'
    assert fn(3, 2, 3) == 'off'

def test_int_str_mapping():
    fn = int_str_mapping
    mapping = {0x00: "off", 0x01: "-12db", 0x02: "on"}

    assert getattr(fn, '_status_type') == 'int_str_mapping'

    assert fn(mapping, 0x00) == "off"
    assert fn(mapping, 0x01) == "-12db"
    assert fn(mapping, 0x02) == "on"
    assert fn(mapping, 0x03) is None

def test_int_int_mapping():
    fn = int_int_mapping
    mapping = {0: 10, 1: 20, 2: 30}

    assert getattr(fn, '_status_type') == 'int_int_mapping'

    assert fn(mapping, 0) == 10
    assert fn(mapping, 1) == 20
    assert fn(mapping, 2) == 30
    assert fn(mapping, 3) is None
