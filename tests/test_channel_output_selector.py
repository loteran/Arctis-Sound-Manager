# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""The Equalizer tab's per-channel output picker (#262).

Deliberately the simple half of the two selectors: no preference memory, no
automatic fallback ladder like the Output channel's own selector has — see
gui/channel_output_selector.py's module docstring for why. Just list the
devices, show what is saved for this channel, and save + apply what gets
picked.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from arctis_sound_manager.gui import channel_output_selector as cos
from arctis_sound_manager.gui import channel_outputs as co


@pytest.fixture
def outputs_file(tmp_path, monkeypatch):
    path = tmp_path / "channel_output_devices.json"
    monkeypatch.setattr(co, "CHANNEL_OUTPUTS_FILE", path)
    return path


@pytest.fixture(autouse=True)
def _no_real_dbus(monkeypatch):
    """A pick must never reach the real bus — see test_channel_outputs.py's
    dbus_calls fixture for why."""
    import sys
    monkeypatch.setitem(
        sys.modules, "arctis_sound_manager.gui.dbus_wrapper",
        type("m", (), {"DbusWrapper": type(
            "DbusWrapper", (), {"apply_channel_outputs": staticmethod(lambda: None)})}),
    )


@pytest.fixture
def selector(outputs_file, monkeypatch):
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(cos.ChannelOutputSelector, "_available", lambda self: [
        ("", "Arctis Nova Pro Wireless"),
        ("bluez_output.earbuds", "HUAWEI FreeBuds 6"),
        ("alsa_output.pci-hdmi", "Q95A"),
    ])
    widget = cos.ChannelOutputSelector("game")
    yield widget
    widget.shutdown()
    widget.deleteLater()


def test_it_is_bound_to_the_channel_it_was_built_for(selector):
    assert selector._channel == "game"


def test_it_starts_on_the_saved_choice(outputs_file, monkeypatch):
    co.set_channel_output("media", "alsa_output.pci-hdmi")
    monkeypatch.setattr(cos.ChannelOutputSelector, "_available", lambda self: [
        ("", "Arctis Nova Pro Wireless"),
        ("alsa_output.pci-hdmi", "Q95A"),
    ])
    QApplication.instance() or QApplication([])

    widget = cos.ChannelOutputSelector("media")
    try:
        assert widget._combo.currentData() == "alsa_output.pci-hdmi"
    finally:
        widget.shutdown()


def test_defaults_to_the_headset_when_nothing_is_saved(selector):
    assert selector._combo.currentData() == ""


def test_picking_a_device_persists_it_for_this_channel_only(selector):
    index = selector._combo.findData("bluez_output.earbuds")
    selector._combo.setCurrentIndex(index)
    selector._on_picked(index)

    assert co.load_channel_outputs() == {"game": "bluez_output.earbuds"}


def test_picking_the_default_clears_the_override(selector):
    co.set_channel_output("game", "bluez_output.earbuds")
    selector.refresh()

    index = selector._combo.findData("")
    selector._combo.setCurrentIndex(index)
    selector._on_picked(index)

    assert co.load_channel_outputs() == {}


def test_picking_a_device_emits_target_changed(selector):
    seen = []
    selector.target_changed.connect(seen.append)

    index = selector._combo.findData("bluez_output.earbuds")
    selector._combo.setCurrentIndex(index)
    selector._on_picked(index)

    assert seen == ["bluez_output.earbuds"]


def test_a_change_made_elsewhere_is_picked_up_on_refresh(selector):
    """The Equalizer tab is not the only writer — a profile could restore a
    different destination for this channel while the tab is open."""
    co.set_channel_output("game", "alsa_output.pci-hdmi")

    selector.refresh()

    assert selector._combo.currentData() == "alsa_output.pci-hdmi"
