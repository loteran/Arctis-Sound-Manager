# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""The screen must not call "I have no battery" an offline headset.

`update_display` used to decide the question with a raw string test:

    connected = power_status not in ("offline", "paired_offline", "")

which folded two unrelated answers into one. The empty string is what
`parsed_status` yields for a variable the device never reports — so a wired
DAC, which has neither a battery nor a power status to give, was described as
"Offline" forever. Reported on Discord as "in the custom DAC settings under
battery it simply shows offline on my DAC".

Three states, then: a level to show, a headset that is genuinely off, and
nothing to say. Only the middle one is "Offline"; the last one draws no battery
element at all.
"""
from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from arctis_sound_manager.oled_manager import OledManager


class _Core:
    def __init__(self, status: dict) -> None:
        self.usb_device = None
        self._usb_write_lock = threading.Lock()
        # Non-None so parsed_status() is reached; `oled` is read by the
        # manager's constructor to resolve the panel's transport parameters.
        self.device_config = SimpleNamespace(oled=None, name="Test DAC")
        self.device_status = status
        self.general_settings = SimpleNamespace(
            oled_brightness=50, oled_show_time=True, oled_show_battery=True,
            oled_show_profile=False, oled_show_eq=False,
            oled_show_mic_status=False, oled_show_sonar_mode=False,
            oled_show_eq_chat=False, oled_show_weather_city=False,
            oled_time_24h=True, oled_display_order=[], weather_enabled=False,
            oled_font_time=20, oled_font_battery=16, oled_font_mic=12,
            oled_font_profile=8, oled_font_eq=8, oled_font_eq_chat=8,
            oled_font_sonar_mode=8, oled_font_weather_temp=20,
            weather_lat=0.0, weather_lon=0.0, weather_units="metric",
            weather_city_display="", weather_location="",
        )


def _render_params(monkeypatch, parsed: dict) -> dict:
    """Run update_display far enough to capture what it asked to draw."""
    manager = OledManager(_Core({"raw": 1}))

    monkeypatch.setattr("arctis_sound_manager.oled_manager.parsed_status",
                        lambda *a, **k: parsed)
    # Everything past the decision is drawing and sending bytes to a panel,
    # which these tests do not need — they read the parameters it settled on.
    monkeypatch.setattr(manager._renderer, "render_status_image",
                        lambda *a, **k: (object(), 0))
    monkeypatch.setattr(manager, "_send_current_frame", lambda *a, **k: None)
    manager._splash_until = 0

    manager.update_display(activity=False)
    return manager._last_render_params


@pytest.fixture(autouse=True)
def _no_hardware(monkeypatch):
    """OledManager must not reach for a device in any of these."""
    monkeypatch.setattr(OledManager, "set_brightness", lambda *a, **k: None)


def test_a_device_with_no_battery_draws_no_battery_element(monkeypatch):
    """The wired-DAC case. No level, no power status — so nothing is claimed."""
    params = _render_params(monkeypatch, {})

    assert params["show_battery"] is False


def test_a_headset_that_is_off_still_says_offline(monkeypatch):
    """"Offline" is the right answer here, and must survive the fix."""
    params = _render_params(monkeypatch, {
        "headset_power_status": "offline",
        "headset_battery_charge": 57,
    })

    assert params["show_battery"] is True
    assert params["connected"] is False


def test_a_level_without_a_power_status_is_shown(monkeypatch):
    """UNKNOWN is not OFF. A device that reports a battery but no power status
    has a perfectly good reading, and hiding it behind "Offline" throws away
    the only thing the user asked to see."""
    params = _render_params(monkeypatch, {"headset_battery_charge": 45})

    assert params["show_battery"] is True
    assert params["connected"] is True
    assert params["battery_percent"] == 45


def test_the_off_dialect_is_understood_too(monkeypatch):
    """Device YAMLs say 'off' or 'offline' depending on the model. Only
    'offline' was listed, so on an 'off' device a powered-down headset would
    have kept a frozen percentage on screen — #124, one layer down."""
    params = _render_params(monkeypatch, {
        "headset_power_status": "off",
        "headset_battery_charge": 57,
    })

    assert params["connected"] is False


def test_standby_keeps_the_reading(monkeypatch):
    """The Nova Elite's 'standby' is a word we have no rule for. Unknown means
    unknown: show what we have rather than invent an outage."""
    params = _render_params(monkeypatch, {
        "headset_power_status": "standby",
        "headset_battery_charge": 60,
    })

    assert params["connected"] is True
    assert params["show_battery"] is True


def test_charging_survives_normalization(monkeypatch):
    """'cable_charging' normalizes to ON, which is right for the connection and
    wrong for the icon — the charging bolt needs the detail that normalizing
    throws away, so it is read from the raw value."""
    params = _render_params(monkeypatch, {
        "headset_power_status": "cable_charging",
        "headset_battery_charge": 80,
    })

    assert params["charging"] is True
    assert params["connected"] is True
