# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for QSystrayApp._extract_battery_percent — the tray battery helper.

The wireless adapter keeps reporting a battery percentage even after the
headset is switched off, so a percentage alone is not a reliable "present"
signal. The helper must return None when headset_power_status says 'off'
so the tray battery item is hidden (#124).
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from arctis_sound_manager.gui.systray_app import QSystrayApp

_extract = QSystrayApp._extract_battery_percent


def _status(power: str | None, pct: int = 80) -> dict:
    headset: dict = {
        "headset_battery_charge": {"value": pct, "type": "percentage"},
    }
    if power is not None:
        headset["headset_power_status"] = {"value": power, "type": "label"}
    return {"headset": headset}


def test_returns_percent_when_powered_on():
    assert _extract(_status("on", 73)) == 73


def test_returns_none_when_powered_off():
    assert _extract(_status("off", 73)) is None


def test_returns_none_when_powered_offline():
    # 'offline' is the vocabulary used by Nova Pro Wireless/Elite/Omni and
    # Arctis Pro Wireless — the original #124 fix only handled 'off'.
    assert _extract(_status("offline", 73)) is None


def test_returns_percent_when_power_status_online():
    assert _extract(_status("online", 60)) == 60


def test_returns_percent_when_cable_charging():
    # On the charging stand, the Nova Pro Wireless is not "off".
    assert _extract(_status("cable_charging", 100)) == 100


def test_returns_percent_when_power_status_absent():
    # No headset_power_status key: keep the pre-#124 behaviour (show battery).
    assert _extract(_status(None, 42)) == 42


def test_finds_battery_in_non_headset_category():
    # Power status lives in the same category as the battery, whatever its name.
    status = {
        "power": {
            "headset_battery_charge": {"value": 55, "type": "percentage"},
            "headset_power_status": {"value": "off", "type": "label"},
        }
    }
    assert _extract(status) is None


def test_ignores_non_percentage_battery_entry():
    status = {"headset": {"headset_battery_charge": {"value": 3, "type": "discrete"}}}
    assert _extract(status) is None


def test_handles_empty_and_malformed_input():
    assert _extract({}) is None
    assert _extract("not a dict") is None
    assert _extract({"headset": None}) is None
