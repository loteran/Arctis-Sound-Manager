# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""test_input_device_list.py — no input may be listed as a raw node.name.

The mirror of test_output_device_list.py, for the Sonar Micro EQ input source
picker. `node.description` is an optional PipeWire property and Bluetooth
sources routinely lack it, so a bluez headset labelled itself with its own
node.name — "bluez_input.30:96:10:49:54:E2". The entry was present but named
after a MAC address, which is why the headset gets reported as "not in the
list": nothing in the row identifies it. build_sink_options() already falls
back to pulsectl's own description for the output pickers (#134 / #146); the
input picker never did.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from arctis_sound_manager.dbus_service import ArctisManagerDbusSettingsService


def _source(node_name: str, description: str | None = None,
            pulse_desc: str = "", **props) -> SimpleNamespace:
    proplist = {"node.name": node_name}
    if description is not None:
        proplist["node.description"] = description
    proplist.update(props)
    return SimpleNamespace(name=node_name, proplist=proplist, description=pulse_desc)


def _options(sources: list) -> list[dict]:
    """Call the real picker builder with a stubbed pulse client."""
    svc = ArctisManagerDbusSettingsService.__new__(ArctisManagerDbusSettingsService)
    svc.core_engine = SimpleNamespace(
        pa_audio_manager=SimpleNamespace(pulse=MagicMock(source_list=lambda: sources))
    )
    return ArctisManagerDbusSettingsService._get_pulse_audio_sources_options(svc)


def _real_entries(options: list[dict]) -> list[dict]:
    """Drop the two synthetic __auto__ / __manual__ rows."""
    return [o for o in options if not o["id"].startswith("__")]


def test_bluetooth_source_gets_its_friendly_name():
    """The exact shape reported: bluez input with no node.description."""
    result = _real_entries(_options([
        _source("bluez_input.30:96:10:49:54:E2",
                pulse_desc="HUAWEI FreeBuds 6", **{"media.class": "Audio/Source"}),
    ]))

    assert len(result) == 1, "Bluetooth input dropped from the picker"
    assert result[0]["id"] == "bluez_input.30:96:10:49:54:E2", "id must stay the node.name"
    assert result[0]["name"] == "HUAWEI FreeBuds 6"


def test_alsa_source_without_description_gets_friendly_name():
    result = _real_entries(_options([
        _source("alsa_input.usb-HP__Inc_HyperX_DuoCast_202011110001-00.analog-stereo",
                pulse_desc="HyperX DuoCast Analog Stereo",
                **{"media.class": "Audio/Source"}),
    ]))

    assert result[0]["name"] == "HyperX DuoCast Analog Stereo"


def test_declared_description_still_wins():
    """The fallback must not override a name the node does provide."""
    result = _real_entries(_options([
        _source("bluez_input.AA:BB", description="Declared Name",
                pulse_desc="ignored", **{"media.class": "Audio/Source"}),
    ]))

    assert result[0]["name"] == "Declared Name"


def test_node_name_is_last_resort():
    """No description anywhere: still listed, still selectable."""
    result = _real_entries(_options([
        _source("alsa_input.mystery", **{"media.class": "Audio/Source"}),
    ]))

    assert result[0]["name"] == "alsa_input.mystery"


@pytest.mark.parametrize("source", [
    _source("alsa_output.something.monitor", pulse_desc="Monitor of Something"),
    _source("Arctis_Game", pulse_desc="Monitor", **{"device.class": "monitor"}),
    _source("effect_output.sonar-micro-eq", pulse_desc="Micro EQ"),
    _source("some.sonar-node", pulse_desc="Sonar"),
    _source("virtual.thing", pulse_desc="V", **{"media.class": "Audio/Source/Virtual"}),
])
def test_monitors_and_asm_nodes_stay_excluded(source):
    """The friendlier labels must not widen what the picker accepts."""
    assert _real_entries(_options([source])) == []


def test_synthetic_entries_come_first():
    options = _options([_source("bluez_input.AA:BB", pulse_desc="Buds",
                                **{"media.class": "Audio/Source"})])

    assert [o["id"] for o in options[:2]] == ["__auto__", "__manual__"]
