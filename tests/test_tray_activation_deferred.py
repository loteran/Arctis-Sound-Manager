# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""A left-click on the tray item must not build the window on the spot.

The slot runs inside KStatusNotifierItem::activate(), a D-Bus call the tray
host is still on the stack for. Building the main window there takes long
enough that Qt processes events underneath it, and a status poll landing in
that window hides the battery item — which deletes the very
KStatusNotifierItem whose activate() is on the stack. Returning into freed
memory took the whole app down with SIGSEGV, tray icon included, which from
the outside looks like the app closing itself.

Returning to the event loop first is the whole fix, so that is what is pinned
here: the click schedules the window, it does not open it inline.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from arctis_sound_manager.gui.systray_app import QSystrayApp


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_left_click_schedules_the_window_instead_of_opening_it(qapp):
    tray = MagicMock()

    QSystrayApp._on_tray_activated(tray, QSystemTrayIcon.ActivationReason.Trigger)
    tray.open_main_window.assert_not_called()

    qapp.processEvents()
    tray.open_main_window.assert_called_once_with()


def test_other_activation_reasons_are_left_to_the_context_menu(qapp):
    tray = MagicMock()

    QSystrayApp._on_tray_activated(tray, QSystemTrayIcon.ActivationReason.Context)
    qapp.processEvents()

    tray.open_main_window.assert_not_called()
