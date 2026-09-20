# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Where a channel's audio should physically play (#262).

Shared logic behind every "send this channel elsewhere" picker — the
Channels page's combo before it moved, and the Equalizer tab's per-channel
selector now. Device labels must name the device: ``node.description`` and
``node.nick`` are both optional PipeWire properties, and Bluetooth sinks
routinely have neither, so the label ladder falls all the way to pulsectl's
own description before the raw node name — otherwise a pair of earbuds shows
up as "bluez_output.30_96_10_49_54_E2.1", a MAC address in place of a product
name.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from arctis_sound_manager.gui import channel_outputs as co


def _sink(name: str, description: str = "", **props) -> SimpleNamespace:
    return SimpleNamespace(name=name, proplist=dict(props), description=description)


# ── device labels ────────────────────────────────────────────────────────────


def _explicit_label(sink) -> str:
    options = co.channel_output_options([sink])
    # Drop the synthetic "headset by default" entry; it is the only option
    # carrying an empty sink id.
    return next(lbl for sink_id, lbl in options if sink_id)


def test_bluetooth_sink_shows_its_product_name():
    """The reported symptom: earbuds listed by MAC-bearing node name."""
    sink = _sink("bluez_output.30_96_10_49_54_E2.1",
                 description="HUAWEI FreeBuds 6")

    assert _explicit_label(sink) == "HUAWEI FreeBuds 6"


def test_node_description_still_wins():
    sink = _sink("bluez_output.AA_BB.1", description="ignored",
                 **{"node.description": "Declared Name"})

    assert _explicit_label(sink) == "Declared Name"


def test_nick_beats_pulsectl_description():
    sink = _sink("alsa_output.usb-Thing", description="ignored",
                 **{"node.nick": "Nick"})

    assert _explicit_label(sink) == "Nick"


def test_node_name_remains_the_last_resort():
    sink = _sink("alsa_output.mystery")

    assert _explicit_label(sink) == "alsa_output.mystery"


# ── option list shape ────────────────────────────────────────────────────────


def test_headset_is_the_default_not_an_explicit_choice():
    """Unlike the Output channel's own list, this one is "send it elsewhere":
    the headset is what "" already means, not a second entry."""
    headset = _sink("alsa_output.usb-SteelSeries_Arctis-00",
                     **{"node.nick": "Arctis Nova Pro Wireless",
                        "device.vendor.id": "0x1038"})
    hdmi = _sink("alsa_output.pci-hdmi", **{"node.nick": "Q95A"})

    options = dict(co.channel_output_options([headset, hdmi]))

    assert options[""] == "Arctis Nova Pro Wireless", (
        "the default entry names the headset it stands for")
    assert "alsa_output.usb-SteelSeries_Arctis-00" not in options, (
        "the headset must not also appear as an explicit, separate choice"
    )
    assert options["alsa_output.pci-hdmi"] == "Q95A"


def test_asm_own_sinks_are_never_offered():
    virtual = _sink("Arctis_Game")

    options = co.channel_output_options([virtual])

    assert [sink_id for sink_id, _label in options] == [""], (
        "ASM's own virtual sinks must never be offered")


# ── persistence ──────────────────────────────────────────────────────────────


@pytest.fixture
def outputs_file(tmp_path, monkeypatch):
    path = tmp_path / "channel_output_devices.json"
    monkeypatch.setattr(co, "CHANNEL_OUTPUTS_FILE", path)
    return path


@pytest.fixture
def dbus_calls(monkeypatch):
    """Stub DbusWrapper so set_channel_output() never reaches the real bus —
    the daemon is a separate process reading its own, unrelated settings
    file, and a real call here would be exercising the developer's machine
    rather than this fixture."""
    calls = []

    class _DbusWrapper:
        @staticmethod
        def apply_channel_outputs():
            calls.append(True)

    import sys
    monkeypatch.setitem(
        sys.modules, "arctis_sound_manager.gui.dbus_wrapper",
        type("m", (), {"DbusWrapper": _DbusWrapper}),
    )
    return calls


def test_a_fresh_install_has_no_overrides(outputs_file):
    assert co.load_channel_outputs() == {}


def test_setting_a_channel_persists_it(outputs_file, dbus_calls):
    co.set_channel_output("game", "bluez_output.earbuds")

    assert json.loads(outputs_file.read_text()) == {"game": "bluez_output.earbuds"}
    assert co.load_channel_outputs() == {"game": "bluez_output.earbuds"}


def test_channels_are_independent(outputs_file, dbus_calls):
    co.set_channel_output("game", "bluez_output.earbuds")
    co.set_channel_output("media", "alsa_output.pci-hdmi")

    assert co.load_channel_outputs() == {
        "game": "bluez_output.earbuds", "media": "alsa_output.pci-hdmi"}


def test_clearing_a_channel_removes_its_entry(outputs_file, dbus_calls):
    co.set_channel_output("game", "bluez_output.earbuds")
    co.set_channel_output("game", None)

    assert co.load_channel_outputs() == {}


def test_corrupt_file_is_harmless(outputs_file):
    outputs_file.write_text("{not json")

    assert co.load_channel_outputs() == {}


def test_setting_a_channel_tells_the_daemon_to_apply_it(outputs_file, dbus_calls):
    co.set_channel_output("game", "bluez_output.earbuds")

    assert dbus_calls == [True]
