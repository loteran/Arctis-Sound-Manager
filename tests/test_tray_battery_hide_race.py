# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""The tray battery item must not be destroyed from under an incoming click.

hide() on a QSystemTrayIcon destroys the KStatusNotifierItem behind it. Called
straight from a D-Bus status update — which is what happens the moment the
headset powers off — it can delete the item while a click from the tray host
is already in flight; KStatusNotifierItem::activate() then runs on freed
memory and takes the whole app down with SIGSEGV.

Observed on a Nova Pro Wireless: the headset powered off at 14:09:06 (the
daemon redirected audio to the fallback output), the battery item was hidden,
and a click on the tray at 14:16:11 killed asm-gui — frame #0 in the core was
KStatusNotifierItem::activate().
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from unittest.mock import MagicMock, patch

import pytest

from arctis_sound_manager.gui.systray_app import QSystrayApp


def _status(power: str, pct: int = 80) -> dict:
    return {
        "headset": {
            "headset_power_status": {"value": power, "type": "label"},
            "headset_battery_charge": {"value": pct, "type": "percentage"},
        }
    }


class _Stub:
    """Stands in for QSystrayApp: the method is called unbound, and only ever
    touches these three attributes. QSystrayApp is a QObject, so it cannot be
    instantiated without running Qt's constructor."""

    # The real helper: what turns a status payload into a percentage or None
    # is the behaviour under test here, not something worth re-mocking.
    _extract_battery_percent = staticmethod(QSystrayApp._extract_battery_percent)

    def __init__(self):
        self.logger = MagicMock()
        self.tray_icon = MagicMock()
        self.battery_icon = MagicMock()


@pytest.fixture
def app():
    return _Stub()


def _update(app, status):
    with patch("arctis_sound_manager.gui.systray_app.QTimer") as timer, \
         patch("arctis_sound_manager.gui.systray_app._show_battery_in_tray",
               return_value=True), \
         patch("arctis_sound_manager.gui.systray_app._tray_icon_color",
               return_value="#ffffff"), \
         patch("arctis_sound_manager.gui.systray_app.get_icon_pixmap"), \
         patch("arctis_sound_manager.gui.systray_app.get_battery_number_pixmap"), \
         patch("arctis_sound_manager.gui.systray_app.QIcon"):
        QSystrayApp._update_tray_icon(app, status)
        return timer


def test_hiding_is_deferred_not_immediate(app) -> None:
    """The whole point: the hide must not run inside the D-Bus call."""
    app.battery_icon.isVisible.return_value = True
    timer = _update(app, _status("offline"))

    app.battery_icon.hide.assert_not_called()
    timer.singleShot.assert_called_once()
    delay, callback = timer.singleShot.call_args[0]
    assert delay == 0
    assert callback == app.battery_icon.hide


def test_an_already_hidden_item_is_left_alone(app) -> None:
    """Without this check a hidden item was re-hidden on every status poll,
    so the risky call ran constantly instead of once at power-off."""
    app.battery_icon.isVisible.return_value = False
    timer = _update(app, _status("offline"))

    timer.singleShot.assert_not_called()
    app.battery_icon.hide.assert_not_called()


def test_a_live_battery_still_shows_the_item(app) -> None:
    app.battery_icon.isVisible.return_value = False
    _update(app, _status("online", 42))

    app.battery_icon.show.assert_called_once()
    app.battery_icon.setIcon.assert_called_once()
    app.battery_icon.hide.assert_not_called()


def test_a_visible_item_is_not_re_shown(app) -> None:
    app.battery_icon.isVisible.return_value = True
    _update(app, _status("online", 42))

    app.battery_icon.show.assert_not_called()
    app.battery_icon.setIcon.assert_called_once()
