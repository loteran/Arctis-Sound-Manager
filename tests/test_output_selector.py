# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the Sonar output selector's signalling.

Every ``target_changed`` ends in a filter-chain restart, which interrupts
audio. The selector polls the device list on a timer, so the rule is narrow and
worth pinning down: emit when the resolved device actually changes, and never
merely because the page was built or the list was re-read.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from arctis_sound_manager.gui.output_selector import OutputSelector

HEADSET = "alsa_output.usb-SteelSeries_Arctis_Nova_7-00.analog-stereo"
BUDS = "bluez_output.30_96_10_49_54_E2.1"


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


def _selector(qt_app, devices, order=None):
    """Build a selector over a fixed device list, with no real PulseAudio."""
    OutputSelector._available = lambda self: list(self._fake)   # type: ignore[assignment]
    widget = OutputSelector.__new__(OutputSelector)
    widget._fake = devices                                       # type: ignore[attr-defined]
    OutputSelector.__init__(widget)
    widget._timer.stop()
    if order:
        widget._memory.order = list(order)
        widget._current = None
        widget._synced = False
        widget.refresh()
    return widget


def test_no_emit_when_the_page_is_built(qt_app):
    """Opening the Sonar tab must not restart the filter chain."""
    fired: list[str] = []
    widget = _selector(qt_app, [(HEADSET, "Arctis Nova 7"), (BUDS, "FreeBuds")])
    widget.target_changed.connect(fired.append)

    widget.refresh()

    assert fired == []
    widget.shutdown()


def test_no_emit_when_the_list_is_unchanged(qt_app):
    """The timer re-reads devices constantly; a stable list is not a change."""
    widget = _selector(qt_app, [(HEADSET, "Arctis Nova 7")])
    fired: list[str] = []
    widget.target_changed.connect(fired.append)

    for _ in range(5):
        widget.refresh()

    assert fired == []
    widget.shutdown()


def test_emits_when_the_preferred_device_appears(qt_app):
    """The earbuds come back: the channel must follow, exactly once."""
    widget = _selector(qt_app, [(HEADSET, "Arctis Nova 7")], order=[BUDS])
    fired: list[str] = []
    widget.target_changed.connect(fired.append)

    widget._fake = [(HEADSET, "Arctis Nova 7"), (BUDS, "FreeBuds")]
    widget.refresh()
    widget.refresh()

    assert fired == [BUDS]
    widget.shutdown()


def test_emits_when_the_current_device_disappears(qt_app):
    """The earbuds go in their case: fall back, once, to the headset."""
    widget = _selector(qt_app, [(HEADSET, "Arctis Nova 7"), (BUDS, "FreeBuds")],
                       order=[BUDS])
    fired: list[str] = []
    widget.target_changed.connect(fired.append)

    widget._fake = [(HEADSET, "Arctis Nova 7")]
    widget.refresh()
    widget.refresh()

    assert fired == [HEADSET]
    widget.shutdown()


def test_user_pick_emits(qt_app):
    widget = _selector(qt_app, [(HEADSET, "Arctis Nova 7"), (BUDS, "FreeBuds")])
    fired: list[str] = []
    widget.target_changed.connect(fired.append)

    widget._on_picked(widget._combo.findData(BUDS))

    assert fired == [BUDS]
    assert widget._memory.preferred == BUDS
    widget.shutdown()


def test_no_devices_emits_nothing(qt_app):
    widget = _selector(qt_app, [])
    fired: list[str] = []
    widget.target_changed.connect(fired.append)

    widget.refresh()

    assert fired == []
    widget.shutdown()
