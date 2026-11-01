# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the Sonar output selector's signalling.

``target_changed`` is reserved for a device the user picked and is the only
signal a consumer may persist. Device-presence changes — a monitor going to
sleep, the headset standing in, the monitor coming back — travel on
``live_target_changed`` instead: they move the live link, not the saved
choice.

The selector polls the device list on a timer, so the rule is narrow and worth
pinning down: emit when the resolved device actually changes, and never merely
because the page was built or the list was re-read.
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
    """Build a selector over a fixed device list, with no real PulseAudio.

    The stub is bound to this instance, not written onto the class. Assigning
    ``OutputSelector._available`` here left every selector built later in the
    session — in any other test file — reaching for a ``_fake`` attribute only
    these fixtures set, so an unrelated test failed with AttributeError
    depending on collection order.
    """
    widget = OutputSelector.__new__(OutputSelector)
    widget._fake = devices                                       # type: ignore[attr-defined]
    widget._available = lambda: list(widget._fake)               # type: ignore[assignment]
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
    widget = _selector(qt_app, [(HEADSET, "Arctis Nova 7"), (BUDS, "FreeBuds")])
    picked, live = _record(widget)

    widget.refresh()

    assert picked == []
    assert live == []
    widget.shutdown()


def test_no_emit_when_the_list_is_unchanged(qt_app):
    """The timer re-reads devices constantly; a stable list is not a change."""
    widget = _selector(qt_app, [(HEADSET, "Arctis Nova 7")])
    picked, live = _record(widget)

    for _ in range(5):
        widget.refresh()

    assert picked == []
    assert live == []
    widget.shutdown()


def _record(widget):
    """Collect both the persisted-choice signal and the live-link signal."""
    picked: list[str] = []
    live: list[str] = []
    widget.target_changed.connect(picked.append)
    widget.live_target_changed.connect(live.append)
    return picked, live


def test_emits_when_the_preferred_device_appears(qt_app):
    """The earbuds come back: the live channel follows, exactly once."""
    widget = _selector(qt_app, [(HEADSET, "Arctis Nova 7")], order=[BUDS])
    picked, live = _record(widget)

    widget._fake = [(HEADSET, "Arctis Nova 7"), (BUDS, "FreeBuds")]
    widget.refresh()
    widget.refresh()

    assert live == [BUDS]
    assert picked == [], "an appearing device is not a new user choice"
    widget.shutdown()


def test_emits_when_the_current_device_disappears(qt_app):
    """The earbuds go in their case: fall back, once, to the headset."""
    widget = _selector(qt_app, [(HEADSET, "Arctis Nova 7"), (BUDS, "FreeBuds")],
                       order=[BUDS])
    picked, live = _record(widget)

    widget._fake = [(HEADSET, "Arctis Nova 7")]
    widget.refresh()
    widget.refresh()

    assert live == [HEADSET]
    assert picked == [], "a fallback must not become the saved choice"
    widget.shutdown()


def test_user_pick_emits_the_persisted_signal(qt_app):
    widget = _selector(qt_app, [(HEADSET, "Arctis Nova 7"), (BUDS, "FreeBuds")])
    picked, live = _record(widget)

    widget._on_picked(widget._combo.findData(BUDS))

    assert picked == [BUDS]
    assert live == []
    assert widget._memory.preferred == BUDS
    widget.shutdown()


def test_no_devices_emits_nothing(qt_app):
    widget = _selector(qt_app, [])
    picked, live = _record(widget)

    widget.refresh()

    assert picked == []
    assert live == []
    widget.shutdown()
