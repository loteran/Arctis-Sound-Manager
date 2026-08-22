# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression test for the debt left by HW-1 (RAPPORT-CHAOS-ASM.md).

nova_7_discrete_battery.yaml / nova_7p_discrete_battery.yaml no longer map
`mic_status` (commit 7f28e18): the vendor spec for that family never defines
those bytes. `micro_autoswitch` (settings.py) is a *global* setting — it has
no device profile of its own — and its "mute" trigger (mode 2) is driven
entirely by `mic_status` (core.py:107, resolve_mic_autoswitch_target). Offer
that button on a Nova 7 Gen 1 / 7P Gen 1 and it looks like a working control
that silently never fires — the exact defect class #146 was about.

The mechanism under test (QSettingsWidget._option_available /
set_available_status_keys, gui/settings_widget.py) is generic: a
ConfigSetting's `option_requires_status` maps a BUTTON_GROUP value to the
live status key(s) it needs, and the widget gates on whatever status keys
DevicePage.update_status last reported for the *active* device — not on any
hardcoded setting name. micro_autoswitch is simply the one caller today.

Mode 1 ("connection") and mode 3 ("both") are deliberately left
unconstrained here: mode 1 only needs the device's online_status block,
which this hardware family still has (only mic_status was fabricated), and
mode 3 keeps doing something useful through its connection half even
without mic_status (settings.py documents the degrade). Only mode 2 is
fully inert without mic_status, so only mode 2 gets disabled.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QPushButton

from arctis_sound_manager.gui.settings_widget import QSettingsWidget

# The exact ConfigSetting kwargs micro_autoswitch is declared with in
# settings.py, reproduced here rather than imported so this test pins the
# wire shape (what actually crosses D-Bus as JSON) rather than a Python
# object identity that could pass even if the JSON round-trip broke it.
_MICRO_AUTOSWITCH_KWARGS = {
    "type": "button_group",
    "default_value": 0,
    "values_mapping": {
        0: "micro_autoswitch_off",
        1: "micro_autoswitch_connection",
        2: "micro_autoswitch_mute",
        3: "micro_autoswitch_both",
    },
    # As it would arrive after a real json.dumps/json.loads round trip
    # (dbus_service.get_settings -> DbusWrapper): dict keys become strings.
    "option_requires_status": {"2": "mic_status"},
}


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


def _widget(qt_app) -> QSettingsWidget:
    # Matches DevicePage's actual construction of the "general" settings
    # section (gui/device_page.py): QSettingsWidget(content, "general", "general").
    return QSettingsWidget(None, "general", "general")


def _apply_settings(widget: QSettingsWidget, micro_autoswitch_value: int) -> None:
    widget.update_settings({
        "settings_config": {"micro_autoswitch": dict(_MICRO_AUTOSWITCH_KWARGS)},
        "general": {"micro_autoswitch": micro_autoswitch_value},
    })


def _buttons(widget: QSettingsWidget) -> list[QPushButton]:
    container = widget._settings_widgets["micro_autoswitch"]
    return container.findChildren(QPushButton)


def test_all_modes_offered_when_profile_reports_mic_status(qt_app):
    """A device whose profile maps mic_status (e.g. nova_pro_wireless.yaml)
    gets the full control: nothing here should ever narrow more than the
    active profile's own gaps require."""
    widget = _widget(qt_app)
    _apply_settings(widget, micro_autoswitch_value=0)

    widget.set_available_status_keys({"headset_power_status", "mic_status", "chat_mix"})

    buttons = _buttons(widget)
    assert len(buttons) == 4
    assert all(b.isEnabled() for b in buttons)


def test_mute_mode_disabled_on_nova7_gen1_profile(qt_app):
    """The six Nova 7 Gen 1 / 7P Gen 1 PIDs: mic_status was dropped, so the
    "mute" trigger (button index 2) must not be offered as if it worked. It
    stays visible (never hidden) with an explanatory tooltip, and the other
    three buttons — which don't depend on mic_status — stay fully usable."""
    widget = _widget(qt_app)
    _apply_settings(widget, micro_autoswitch_value=0)

    # nova_7_discrete_battery.yaml's actual representation: headset_power_status,
    # headset_battery_charge, chat_mix, media_mix, cable_charging — no mic_status.
    widget.set_available_status_keys(
        {"headset_power_status", "headset_battery_charge", "chat_mix", "media_mix", "cable_charging"}
    )

    buttons = _buttons(widget)
    assert len(buttons) == 4  # never hidden, only disabled
    off_btn, connection_btn, mute_btn, both_btn = buttons

    assert off_btn.isEnabled()
    assert connection_btn.isEnabled()
    assert not mute_btn.isEnabled()
    assert mute_btn.toolTip()  # explains why, not just a dead-looking button
    # mode 3 ("both") still does something useful via its connection half —
    # must not be disabled outright for a partial gap.
    assert both_btn.isEnabled()


def test_existing_mute_selection_survives_being_offered_on_a_gen1_device(qt_app):
    """A user who set mode 2 on a headset that supports it, then plugs in a
    Nova 7 Gen 1: the stored value must not be silently reset or overwritten
    just because the control that set it is no longer fully usable here."""
    widget = _widget(qt_app)
    _apply_settings(widget, micro_autoswitch_value=2)  # already "mute" from before

    widget.set_available_status_keys({"headset_power_status", "chat_mix"})  # no mic_status

    assert widget.settings["micro_autoswitch"] == 2  # untouched

    buttons = _buttons(widget)
    mute_btn = buttons[2]
    assert not mute_btn.isEnabled()
    # Still shown as the active selection, so the user sees what's actually
    # stored rather than the control quietly looking like "Off".
    assert mute_btn.property("active") is True


def test_set_available_status_keys_is_a_noop_when_unchanged(qt_app, monkeypatch):
    """Status arrives roughly once a second; refresh_panel() tears down and
    rebuilds every row in the section, so a stable key set must not trigger
    it on every poll."""
    widget = _widget(qt_app)
    _apply_settings(widget, micro_autoswitch_value=0)
    widget.set_available_status_keys({"headset_power_status", "mic_status"})

    calls = []
    monkeypatch.setattr(widget, "refresh_panel", lambda: calls.append(1))

    widget.set_available_status_keys({"headset_power_status", "mic_status"})
    assert calls == []

    widget.set_available_status_keys({"headset_power_status"})  # actually changed
    assert calls == [1]
