# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Picking which headset ASM drives, from the Headset tab (#199).

The preference already existed in Settings. Someone who keeps a GameBuds dongle
plugged in next to a Nova Pro base station is looking at the Headset tab when
they notice the wrong one is being driven, so the choice belongs there too.

Two pickers, one setting — and that is the point of these tests. Neither knows
the other exists: both write through ``change_setting`` and both read the
current value out of the settings payload the daemon pushes every second. What
has to hold is that a change made in one is reflected in the other, and that
neither invents a selection nobody made.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from arctis_sound_manager.gui import headset_page as hp


@pytest.fixture
def page(monkeypatch):
    QApplication.instance() or QApplication([])
    written: list[tuple] = []
    monkeypatch.setattr(hp.DbusWrapper, "change_setting",
                        staticmethod(lambda name, value: written.append((name, value))))
    widget = hp.HeadsetPage()
    widget.written = written
    yield widget
    widget.deleteLater()


_TWO = {"name": "connected_arctis_devices", "list": [
    {"id": "0x12e0", "name": "Arctis Nova Pro Wireless"},
    {"id": "0x2202", "name": "Arctis GameBuds"},
]}


def test_the_picker_stays_hidden_when_there_is_nothing_to_choose(page):
    """A picker offering one item is not a choice, and this card is the first
    thing you see on the page."""
    page.on_options_list_received({"name": "connected_arctis_devices",
                                   "list": [{"id": "0x12e0", "name": "Nova Pro"}]})

    assert not page._device_selector.isVisibleTo(page)


def test_it_appears_once_a_second_device_is_detected(page):
    page.on_options_list_received(_TWO)

    assert page._device_selector.isVisibleTo(page)
    assert page._device_selector.count() == 2


def test_a_change_made_in_settings_moves_this_picker(page):
    """Half of the synchronisation. The Settings tab writes the setting; the
    daemon pushes it back in the next payload; this follows without either
    picker knowing about the other."""
    page.on_options_list_received(_TWO)

    page.update_settings({"general": {"preferred_device": "0x2202"}})

    assert page._device_selector.currentText() == "Arctis GameBuds"


def test_picking_here_writes_the_same_setting(page):
    """The other half. Settings reads this value on its next refresh."""
    page.on_options_list_received(_TWO)

    page._on_device_picked(1)

    assert page.written == [("preferred_device", "0x2202")]


def test_following_the_setting_does_not_write_it_back(page):
    """A payload arriving every second must not turn into a write every second,
    which would loop the daemon through device selection forever."""
    page.on_options_list_received(_TWO)

    page.update_settings({"general": {"preferred_device": "0x2202"}})
    page.update_settings({"general": {"preferred_device": "0x2202"}})

    assert page.written == []


def test_no_preference_selects_nothing(page):
    """Showing the first entry as though it had been chosen would misreport
    which device is being driven — the daemon picks by enumeration order when
    nothing is set, and that is not necessarily the first in this list."""
    page.on_options_list_received(_TWO)

    page.update_settings({"general": {}})

    assert page._device_selector.currentIndex() == -1


def test_a_preference_for_an_absent_device_selects_nothing(page):
    """The dongle named in the setting is unplugged right now. The setting is
    kept — it applies again when it comes back — but nothing here claims it is
    the device in use."""
    page.update_settings({"general": {"preferred_device": "0xdead"}})
    page.on_options_list_received(_TWO)

    assert page._device_selector.currentIndex() == -1


def test_a_settings_payload_without_the_general_block_is_survivable(page):
    """update_settings runs on every push, including early ones that carry only
    device identification."""
    page.on_options_list_received(_TWO)

    page.update_settings({"device_name": "Arctis Nova Pro Wireless"})

    assert page._device_selector.currentIndex() == -1
