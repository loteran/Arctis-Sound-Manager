# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""The tray item is created once and never destroyed (#194).

hide() on a QSystemTrayIcon destroys the KStatusNotifierItem behind it. ASM
used to keep the battery % in a second tray item and hide that item whenever
the level became unknown — which is exactly what happens when the headset
powers off, and also the moment a user is most likely to click the tray to find
out why their audio just moved. A click already in flight from the tray host
then ran KStatusNotifierItem::activate() on freed memory and killed asm-gui;
frame #0 in the core was inside KDE's library, with no ASM frame anywhere.

Deferring the hide narrowed the window without closing it. The second coredump
showed why it could not close it: under activate() sat g_main_loop_run, reached
from a Python QTimer slot through gi. Clips runs nested GLib main loops (the
shortcut portal, the ScreenCast portal), and a nested loop dispatches posted Qt
events — the tray's D-Bus Activate among them — at a moment ASM does not
choose. A deferred hide can therefore run inside one of those loops too.

So there is no hide any more. One item, for the life of the process; what the
battery level changes is the picture on it. These tests pin that: no status
payload, in any state, may reach show() or hide().
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
    touches these attributes. QSystrayApp is a QObject, so it cannot be
    instantiated without running Qt's constructor."""

    # The real helper: what turns a status payload into a percentage or None
    # is the behaviour under test here, not something worth re-mocking.
    _extract_battery_percent = staticmethod(QSystrayApp._extract_battery_percent)

    def __init__(self):
        self.logger = MagicMock()
        self.tray_icon = MagicMock()


@pytest.fixture
def app():
    return _Stub()


def _update(app, status, show_battery: bool = True):
    """Run the real _update_tray_icon, returning what it asked to draw."""
    with patch("arctis_sound_manager.gui.systray_app._show_battery_in_tray",
               return_value=show_battery), \
         patch("arctis_sound_manager.gui.systray_app._tray_icon_color",
               return_value="#ffffff"), \
         patch("arctis_sound_manager.gui.systray_app.get_tray_pixmap") as pixmap, \
         patch("arctis_sound_manager.gui.systray_app.QIcon"):
        QSystrayApp._update_tray_icon(app, status)
        return pixmap


def test_a_powered_off_headset_never_hides_the_item(app) -> None:
    """The crash, stated as a test. Power-off means a different icon — it must
    not mean an item the tray host is still holding a reference to going away.
    """
    pixmap = _update(app, _status("offline"))

    app.tray_icon.hide.assert_not_called()
    app.tray_icon.show.assert_not_called()
    # None, not a number: the logo on its own.
    assert pixmap.call_args[0][0] is None


def test_a_live_battery_only_repaints(app) -> None:
    pixmap = _update(app, _status("online", 42))

    app.tray_icon.hide.assert_not_called()
    app.tray_icon.show.assert_not_called()
    app.tray_icon.setIcon.assert_called_once()
    assert pixmap.call_args[0][0] == 42


def test_turning_the_battery_display_off_does_not_remove_anything(app) -> None:
    """systray_show_battery is about what is drawn, not about whether the item
    exists — the setting used to take the whole item away."""
    pixmap = _update(app, _status("online", 42), show_battery=False)

    app.tray_icon.hide.assert_not_called()
    assert pixmap.call_args[0][0] is None


def test_repeated_polls_stay_idempotent(app) -> None:
    """The background poll runs every 30s with an unchanged payload. Whatever
    it does has to be safe to do forever — which is true of setIcon and was not
    true of the hide it used to reach."""
    for _ in range(5):
        _update(app, _status("offline"))

    assert app.tray_icon.setIcon.call_count == 5
    app.tray_icon.hide.assert_not_called()


def test_the_tooltip_says_the_level_when_there_is_one(app) -> None:
    _update(app, _status("online", 42))
    assert "42%" in app.tray_icon.setToolTip.call_args[0][0]

    app.tray_icon.setToolTip.reset_mock()
    _update(app, _status("offline"))
    assert "%" not in app.tray_icon.setToolTip.call_args[0][0]
