# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Findings from an audit of the device profiles against SteelSeries' own specs.

Three classes of defect, all invisible from inside the app — the headset never
complains, and the value it produces looks like an answer:

  1. A status variable pointing at a byte the spec declares `unused`. It shows
     padding, dressed as a connection state. Three profiles did this with
     `bluetooth_connection`.
  2. A PID filed under the wrong protocol family, so every byte is read one
     scale off. 0x22ab was read as a 0-4 battery when its own spec makes it a
     0-100 one.
  3. A status variable pointing past the end of the frame entirely — not even
     padding, just nothing the spec declares. `base_arctis_nova_7_tx.device`
     (shared by the Gen 1 Nova 7 and 7P, RAPPORT-CHAOS-ASM.md HW-1) defines
     seven `headset_status` fields ending at ASM offset 0x05:
     `report_id, command, connection_status, battery_status, charging_status,
     game_chatmix_level, chat_chatmix_level`. Offsets 0x06-0x09 were read
     anyway, as `bluetooth_connection` / `bluetooth_power_status` /
     `bluetooth_auto_mute` / `mic_status` — four fabricated values across six
     PIDs, one of which (`mic_status`) drove the live `micro_autoswitch`
     feature.

The specs are not redistributable and live outside this repo, so these tests
lock the conclusions rather than re-deriving them — see the profiles' own
comments for the reasoning and the spec file and struct each rests on.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from ruamel.yaml import YAML

from arctis_sound_manager.config import DeviceConfiguration

DEVICES = Path(__file__).parent.parent / "src" / "arctis_sound_manager" / "devices"


def _config(name: str) -> DeviceConfiguration:
    return DeviceConfiguration(YAML(typ="safe").load(DEVICES / name))


def _mapping(config: DeviceConfiguration, starts_with: int):
    return next(m for m in config.status.response_mapping
                if m.starts_with == starts_with)


# The `unused` byte in each family's headset_status struct, as the offset ASM
# would use for it (spec field index - 1, these buffers carrying no report id).
UNUSED_BYTE_OFFSET = {
    # base_arctis_nova_7_gen2_tx.device: unused sits between chat_chatmix_level
    # and bt_power_default.
    "nova_7_perc_battery.yaml": 0x06,
    "nova_7p_perc_battery.yaml": 0x06,
    # base_arctis_nova_5_tx.device: unused sits between connection_status and
    # battery_status.
    "nova_5.yaml": 0x02,
}


@pytest.mark.parametrize("profile,offset", sorted(UNUSED_BYTE_OFFSET.items()))
def test_no_status_variable_reads_the_spec_s_unused_byte(profile, offset):
    """Nothing may be read from a byte the spec declares as padding.

    All three profiles read it as `bluetooth_connection`, so the Bluetooth
    indicator reported a link state the frame never carried — and on the Nova
    5, which has no Bluetooth at all.
    """
    config = _config(profile)
    mapping = _mapping(config, 0xB0)

    offsets = {v: k for k, v in mapping.__dict__.items()
               if isinstance(v, int) and k != "starts_with"}

    assert offset not in offsets, (
        f"{profile} reads {offsets.get(offset)} from offset {offset:#04x}, "
        "which the spec declares unused"
    )


@pytest.mark.parametrize("profile", sorted(UNUSED_BYTE_OFFSET))
def test_bluetooth_connection_is_gone_from_those_profiles_entirely(profile):
    """A leftover status_parse entry or representation row would outlive the
    mapping and show up as an empty row rather than nothing at all."""
    config = _config(profile)

    assert "bluetooth_connection" not in config.status_parse
    for section in config.status.representation.values():
        assert "bluetooth_connection" not in section


def test_the_nova_5_has_no_empty_bluetooth_section_left():
    """Its only Bluetooth row was the phantom one — the section goes with it."""
    config = _config("nova_5.yaml")

    assert "bluetooth" not in config.status.representation


def test_wow_upgrade_pid_is_read_as_gen_2_not_gen_1():
    """0x22ab is the WoW dongle after its firmware update, not a hardware
    revision of 0x227a.

    `arctis_nova_7_wow_upgrade_tx.device` includes base_arctis_nova_7_gen2_tx
    (battery `range 1 100`) and resolves get-main-product-id to the Gen 2 id;
    0x227a — the same headset before the update — includes
    base_arctis_nova_7_tx instead. Filed under the Gen 1 profile, its battery
    byte was scaled against perc_max 4: a real 76 % came out as 1900 %, since
    percentage() clamps nothing without round_to.
    """
    gen1 = _config("nova_7_discrete_battery.yaml")
    gen2 = _config("nova_7_perc_battery.yaml")

    assert 0x22AB in gen2.product_ids
    assert 0x22AB not in gen1.product_ids
    # The pre-upgrade PID stays where it belongs.
    assert 0x227A in gen1.product_ids
    assert 0x227A not in gen2.product_ids


def test_the_two_nova_7_families_disagree_on_the_battery_scale():
    """The whole point of the PID move: these profiles read battery differently.

    If both ever declared the same scale, the move above would be cosmetic and
    this test would stop protecting anything.
    """
    gen1 = _config("nova_7_discrete_battery.yaml")
    gen2 = _config("nova_7_perc_battery.yaml")

    assert gen1.status_parse["headset_battery_charge"].init_kwargs["perc_max"] == 4
    assert gen2.status_parse["headset_battery_charge"].init_kwargs["perc_max"] == 100


# base_arctis_nova_7_tx.device, `(struct headset_status)` (incoming): seven
# fields — report_id, command, connection_status, battery_status,
# charging_status, game_chatmix_level, chat_chatmix_level — ending at ASM
# offset 0x05 (spec field index minus one, no report id in these buffers).
# There is no field beyond that: no offset 0x06 through 0x09, not even
# padding. arctis_nova_7p_tx.device:8 includes this same base struct, so the
# 7P shares the ceiling. RAPPORT-CHAOS-ASM.md HW-1.
GEN1_NOVA7_LAST_DEFINED_OFFSET = {
    "nova_7_discrete_battery.yaml": 0x05,
    "nova_7p_discrete_battery.yaml": 0x05,
}

# The four keys HW-1 found reading past the end of the frame. Kept as an
# explicit list (rather than "whatever the mapping now contains") so the test
# names exactly what regressing looks like.
GEN1_NOVA7_FABRICATED_KEYS = (
    "bluetooth_connection", "bluetooth_power_status",
    "bluetooth_auto_mute", "mic_status",
)


@pytest.mark.parametrize("profile,last_offset",
                          sorted(GEN1_NOVA7_LAST_DEFINED_OFFSET.items()))
def test_no_status_variable_reads_past_the_gen_1_nova_7_frame(profile, last_offset):
    """Nothing may be read from an offset the spec doesn't define at all — not
    even as padding. A future profile mapping offset 0x06+ here, or filing a
    new PID under one of these two profiles, must fail this test rather than
    reintroduce HW-1.
    """
    config = _config(profile)
    mapping = _mapping(config, 0xB0)

    offsets = {v: k for k, v in mapping.__dict__.items()
               if isinstance(v, int) and k != "starts_with"}

    out_of_range = {off: name for off, name in offsets.items() if off > last_offset}
    assert not out_of_range, (
        f"{profile} reads {out_of_range} beyond offset {last_offset:#04x}, "
        "which is the last field base_arctis_nova_7_tx.device's "
        "headset_status struct defines"
    )


@pytest.mark.parametrize("profile", sorted(GEN1_NOVA7_LAST_DEFINED_OFFSET))
def test_gen_1_nova_7_fabricated_keys_are_gone_entirely(profile):
    """A leftover status_parse entry or representation row would outlive the
    mapping and show up as an empty row rather than nothing at all — the same
    shape of regression the Gen 2 test above guards against."""
    config = _config(profile)
    mapping = _mapping(config, 0xB0)

    mapped_names = {k for k, v in mapping.__dict__.items()
                     if isinstance(v, int) and k != "starts_with"}

    for key in GEN1_NOVA7_FABRICATED_KEYS:
        assert key not in mapped_names
        assert key not in config.status_parse
        for section in config.status.representation.values():
            assert key not in section


@pytest.mark.parametrize("profile", sorted(GEN1_NOVA7_LAST_DEFINED_OFFSET))
def test_gen_1_nova_7_has_no_empty_mic_or_bluetooth_section_left(profile):
    """Both profiles' `mic` and `bluetooth` representation sections held only
    fabricated variables — the sections go with them, not just their rows."""
    config = _config(profile)

    assert "mic" not in config.status.representation
    assert "bluetooth" not in config.status.representation
