# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the home page status bar: battery gating and power-status colour.

Two defects, both from the same root cause — the home page read
`headset_power_status` as a raw string instead of going through
`power_status.normalize_power_value()`:

* The battery pill was shown whenever a percentage was present. The wireless
  adapter keeps reporting the last percentage it saw after the headset is
  switched off, so the main window displayed a frozen reading (a real Nova 7
  Gen 2 sat at "Headset 57%" with the headset off). The tray already gates on
  power status for this exact reason (#124 / PR #125); the home page did not.
* `_STATUS_COLORS` only listed the 'online'/'offline'/'cable_charging'
  vocabulary, so every headset reporting plain 'on' (Nova 5, Nova 7*,
  Arctis 7+, 9, 1 Wireless) fell through to the gray "unknown" colour while
  powered on.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from arctis_sound_manager.gui.home_page import (
    _STATUS_CHARGING_COLOR,
    _STATUS_ONLINE_COLOR,
    _STATUS_UNKNOWN_COLOR,
    _status_color,
)


# ── power-status colour ────────────────────────────────────────────────────────

@pytest.mark.parametrize("value", ["on", "online", "ON", " Online "])
def test_powered_on_is_teal_in_both_vocabularies(value):
    """'on' is what Nova 5/7, Arctis 7+/9/1 Wireless report; it used to go gray."""
    assert _status_color(value) == _STATUS_ONLINE_COLOR


@pytest.mark.parametrize("value", ["off", "offline", "OFF"])
def test_powered_off_is_gray_in_both_vocabularies(value):
    assert _status_color(value) == _STATUS_UNKNOWN_COLOR


def test_cable_charging_keeps_its_own_colour():
    """cable_charging normalizes to ON but must stay blue, not teal."""
    assert _status_color("cable_charging") == _STATUS_CHARGING_COLOR


@pytest.mark.parametrize("value", [None, "standby", "", 42])
def test_unrecognized_power_status_is_gray(value):
    assert _status_color(value) == _STATUS_UNKNOWN_COLOR


# ── battery gating ─────────────────────────────────────────────────────────────

def _status(power: str | None, pct: int | None = 57) -> dict:
    headset: dict = {}
    if pct is not None:
        headset["headset_battery_charge"] = {"value": pct, "type": "percentage"}
    if power is not None:
        headset["headset_power_status"] = {"value": power, "type": "label"}
    return {"headset": headset}


class _RecordingBar:
    """Stands in for _StatusBar, capturing what update_status() forwards."""

    def __init__(self):
        self.calls: list[tuple] = []

    def update(self, power, headset_bat, dac_bat):
        self.calls.append((power, headset_bat, dac_bat))

    def set_no_device(self):
        self.calls.append(("no_device", None, None))


def _run_update_status(status: dict) -> tuple:
    """Drive HomePage.update_status against stub widgets, return the bar call.

    update_status only touches _status_bar, _headset_name_lbl and
    _last_device_name, so an unbound call with a stub instance exercises the
    real logic without building a QWidget tree.
    """
    from types import SimpleNamespace

    from arctis_sound_manager.gui.home_page import HomePage

    bar = _RecordingBar()
    stub = SimpleNamespace(
        _status_bar=bar,
        _headset_name_lbl=SimpleNamespace(
            hide=lambda: None, show=lambda: None,
            setText=lambda *_: None, setStyleSheet=lambda *_: None,
        ),
        _last_device_name="",
    )
    HomePage.update_status(stub, status)
    return bar.calls[-1]


def test_battery_hidden_when_headset_off():
    """The reported regression: a frozen percentage from a powered-off headset."""
    _, headset_bat, _ = _run_update_status(_status("off", 57))
    assert headset_bat is None


def test_battery_hidden_when_headset_offline():
    """Nova Pro Wireless/Elite/Omni vocabulary must be gated too."""
    _, headset_bat, _ = _run_update_status(_status("offline", 57))
    assert headset_bat is None


@pytest.mark.parametrize("power", ["on", "online", "cable_charging"])
def test_battery_shown_when_headset_reachable(power):
    _, headset_bat, _ = _run_update_status(_status(power, 73))
    assert headset_bat == 73


def test_battery_shown_when_power_status_absent():
    """A device that reports no power status at all keeps a working gauge —
    only a definite OFF hides it."""
    _, headset_bat, _ = _run_update_status(_status(None, 73))
    assert headset_bat == 73


def test_battery_shown_for_unknown_power_vocabulary():
    """Nova Elite's 'standby' is UNKNOWN, not OFF: don't drop the reading."""
    _, headset_bat, _ = _run_update_status(_status("standby", 73))
    assert headset_bat == 73
