# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Locks the RAPPORT-CHAOS-ASM.md HW-4 fix for the Nova Elite: the profile now
maps the combined `starts_with: 0x01b0` `wireless_settings` reply, sourced
from ~/steelseries-research/decoded-115/base_arctis_nova_elite_tx.device
(outside this repo — see docs/HARDWARE-QUESTIONS.md for the citation). Before
this, `status.request` sent 0x01b0 but nothing parsed the reply: every status
row stayed blank from daemon start until the matching control pushed once via
its own individual 0x07xx async frame.

Also locks the INT-3 OLED transport fix (interface 3 / report_id 0x01,
sourced from JerwuQu/ggoled#26 and the merged ggoled#35 — a real Nova Elite
owner's hardware test), replacing the old interface-4/report-6 guess that was
never anything but a placeholder copied from the Nova Pro Wireless.

Neither of these facts can be re-derived from inside this repo (the spec
lives outside it, and no headset is on hand to capture from) — these tests
pin the conclusions the way test_status_offsets_vs_spec.py already does for
the earlier HW-1 findings.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from ruamel.yaml import YAML

from arctis_sound_manager.config import (ConfigStatusResponseMapping,
                                         DeviceConfiguration, parsed_status)

ELITE_YAML = (Path(__file__).parent.parent / "src" / "arctis_sound_manager"
              / "devices" / "nova_elite.yaml")


@pytest.fixture(scope="module")
def raw() -> dict:
    return YAML(typ="safe").load(ELITE_YAML)["device"]


@pytest.fixture(scope="module")
def config(raw) -> DeviceConfiguration:
    return DeviceConfiguration({"device": raw})


def _b0_mapping(raw) -> ConfigStatusResponseMapping:
    entry = next(m for m in raw["status"]["response_mapping"]
                 if m["starts_with"] == 0x01b0)
    return ConfigStatusResponseMapping(**entry)


def _wireless_settings_frame() -> list[int]:
    """A synthetic 0x01b0 reply, one distinct value per byte 0x02-0x10 so a
    transposed offset shows up as a wrong value rather than a lucky match."""
    frame = [0x00] * 20
    frame[0x00], frame[0x01] = 0x01, 0xb0
    frame[0x02] = 0x01  # bt_power_default -> bluetooth_default
    frame[0x03] = 0x02  # bt_call_default -> bluetooth_auto_mute
    frame[0x04] = 0xEE  # bt_connection_mode: deliberately NOT mapped
    frame[0x05] = 0x04  # bt_connection_status -> bluetooth_connection
    frame[0x06] = 60    # headset_batt_level -> headset_battery_charge
    frame[0x07] = 45    # charger_batt_level -> charge_slot_battery_charge
    frame[0x08] = 7     # transparent -> transparent_noise_cancelling_level
    frame[0x09] = 0x01  # mic_mute -> mic_status
    frame[0x0a] = 0x02  # transparent_anc_mode -> noise_cancelling
    frame[0x0b] = 8     # muted_mic_brightness -> mic_led_brightness
    frame[0x0c] = 0x03  # inactivity_timer -> auto_off_time_minutes
    frame[0x0d] = 0x01  # wireless_mode
    frame[0x0e] = 0x08  # radio_connection_status -> headset_power_status
    frame[0x0f] = 0x02  # charging_status -> headset_charging_status
    frame[0x10] = 0x03  # active_noise_cancellation -> noise_cancelling_level
    return frame


def test_wireless_settings_reply_is_mapped(raw):
    """The block used to not exist at all — `status.request` fired into the
    void. This is the core HW-4 fix."""
    assert any(m["starts_with"] == 0x01b0 for m in raw["status"]["response_mapping"])


def test_wireless_settings_offsets_match_the_spec_field_order(raw):
    mapping = _b0_mapping(raw)
    offsets = {k: v for k, v in mapping.__dict__.items() if k != "starts_with"}

    assert offsets == {
        "bluetooth_default": 0x02,
        "bluetooth_auto_mute": 0x03,
        "bluetooth_connection": 0x05,
        "headset_battery_charge": 0x06,
        "charge_slot_battery_charge": 0x07,
        "transparent_noise_cancelling_level": 0x08,
        "mic_status": 0x09,
        "noise_cancelling": 0x0a,
        "mic_led_brightness": 0x0b,
        "auto_off_time_minutes": 0x0c,
        "wireless_mode": 0x0d,
        "headset_power_status": 0x0e,
        "headset_charging_status": 0x0f,
        "noise_cancelling_level": 0x10,
    }


def test_bt_connection_mode_is_deliberately_unmapped(raw):
    """Offset 0x04 (bt_connection_mode) has no established name anywhere in
    this profile — see the profile's own comment. It must stay absent rather
    than be guessed at."""
    mapping = _b0_mapping(raw)
    offsets = {v: k for k, v in mapping.__dict__.items() if k != "starts_with"}

    assert 0x04 not in offsets


def test_wireless_settings_reply_decodes_through_the_profile(raw, config):
    values = _b0_mapping(raw).get_status_values(_wireless_settings_frame())
    parsed = parsed_status(values, config)

    assert parsed["headset_battery_charge"] == 60
    assert parsed["charge_slot_battery_charge"] == 45
    assert parsed["headset_power_status"] == "online"       # 0x08, per existing status_parse
    assert parsed["headset_charging_status"] == "charging"  # 0x02, per existing status_parse
    assert parsed["mic_status"] == "muted"
    assert parsed["noise_cancelling"] == "on"
    assert parsed["noise_cancelling_level"] == "high"
    assert parsed["auto_off_time_minutes"] == 10
    assert parsed["wireless_mode"] == "range"


def test_oled_transport_matches_the_ggoled_hardware_report(raw):
    """JerwuQu/ggoled#26 / #35 (merged 2026-07-19): interface 3, report ID 1
    for the OLED collection on PID 0x2244 — not interface 4 / report 6, which
    was a placeholder copied from the Nova Pro Wireless and never confirmed."""
    assert raw["oled"]["interface"] == 3
    assert raw["oled"]["report_id"] == 0x01
    # Only the high byte of `wvalue` is live (oled_manager._compute_wvalue
    # recomputes the low byte from report_id per packet) — it must stay the
    # Feature-report type.
    assert (raw["oled"]["wvalue"] >> 8) & 0xFF == 0x03
