# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Don't offer a screen element the device cannot fill.

The list of OLED elements is static, so every DAC got a "Battery" checkbox —
including the wired ones, which have no battery anywhere in them. Ticking it
put "Offline" on the screen, which reads as a connection fault rather than
"this device has nothing to report". Reported on Discord.

The row now follows what the device actually says about itself. The subtlety
worth pinning is the startup case: an empty payload means the daemon has not
answered yet, and a missing key there says nothing about the hardware — so
silence must not take the row away.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from arctis_sound_manager.gui.dac_page import DacPage


class _Row:
    """Records setVisible() calls.

    A real QWidget is useless here: one with no parent and no show() is never
    "visible" whatever the code does to it, so isVisible() would pass or fail
    for reasons having nothing to do with the behaviour under test. What
    matters is what update_status *decides*, which is exactly one call.
    """

    def __init__(self) -> None:
        self.calls: list[bool] = []

    def setVisible(self, value: bool) -> None:  # noqa: N802 - Qt's spelling
        self.calls.append(bool(value))


class _Stub:
    """DacPage is a QWidget with a heavy constructor; update_status only ever
    touches this one attribute, so it is called unbound on a stand-in."""

    update_status = DacPage.update_status

    def __init__(self, row: _Row | None) -> None:
        self._display_rows = {"oled_show_battery": row} if row else {}


def test_a_device_reporting_a_battery_keeps_the_row():
    row = _Row()

    _Stub(row).update_status({"headset": {
        "headset_battery_charge": {"value": 80, "type": "percentage"}}})

    assert row.calls == [True]


def test_a_device_with_no_battery_loses_the_row():
    """The wired-DAC case: the daemon answered, and there is no battery in it."""
    row = _Row()

    _Stub(row).update_status({"headset": {
        "mic_status": {"value": "unmuted", "type": "label"}}})

    assert row.calls == [False]


def test_an_empty_payload_changes_nothing():
    """Startup, or a daemon that is not up yet. Absence of evidence is not
    evidence that the hardware has no battery — the row must not be touched
    at all, in either direction."""
    row = _Row()

    _Stub(row).update_status({})

    assert row.calls == []


def test_a_page_without_the_row_does_not_raise():
    """update_status is wired to a D-Bus signal, so it must survive being
    called before the rows exist."""
    _Stub(None).update_status({"headset": {}})
