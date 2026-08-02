# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Device labels in the home-page channel combos must name the device.

``node.description`` and ``node.nick`` are both optional PipeWire properties,
and Bluetooth sinks routinely have neither. The label ladder ended at
``sink.name``, so a pair of earbuds appeared in the Game/Chat/Media/Output
dropdowns as "bluez_output.30_96_10_49_54_E2.1" — a MAC address in place of a
product name, which users read as a broken entry rather than their headphones.
pulsectl resolves the friendly name even when the PipeWire properties are
absent, exactly as build_sink_options() already does for the D-Bus pickers.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from types import SimpleNamespace

from arctis_sound_manager.gui.home_page import HomePage


def _sink(name: str, description: str = "", **props) -> SimpleNamespace:
    return SimpleNamespace(name=name, proplist=dict(props), description=description)


def _label(sink) -> str:
    """Drive the real label ladder without building a HomePage."""
    captured: dict = {}

    class _Card:
        def set_device_options(self, options, current=""):
            captured.setdefault("options", []).extend(options)

    stub = SimpleNamespace(
        _game_card=_Card(), _chat_card=_Card(), _media_card=_Card(),
        _ext_card=_Card(), _available_sinks=None, _ext_device_nick=None,
    )
    HomePage._refresh_device_combos(stub, [sink])
    # Drop the synthetic "headset by default" entry the channel combos prepend;
    # it is the only option carrying an empty sink id.
    return next(lbl for sink_id, lbl in captured.get("options", []) if sink_id)


def test_bluetooth_sink_shows_its_product_name():
    """The reported symptom: earbuds listed by MAC-bearing node name."""
    sink = _sink("bluez_output.30_96_10_49_54_E2.1",
                 description="HUAWEI FreeBuds 6")

    assert _label(sink) == "HUAWEI FreeBuds 6"


def test_node_description_still_wins():
    sink = _sink("bluez_output.AA_BB.1", description="ignored",
                 **{"node.description": "Declared Name"})

    assert _label(sink) == "Declared Name"


def test_nick_beats_pulsectl_description():
    sink = _sink("alsa_output.usb-Thing", description="ignored",
                 **{"node.nick": "Nick"})

    assert _label(sink) == "Nick"


def test_node_name_remains_the_last_resort():
    sink = _sink("alsa_output.mystery")

    assert _label(sink) == "alsa_output.mystery"
