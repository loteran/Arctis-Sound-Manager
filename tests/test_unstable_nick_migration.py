# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""A saved output stored as an unstable nick is pinned to its node.name (#413).

Before #413 a picker could store "USB Audio #2" — a nick WirePlumber hands out
in node order — as the setting itself. The lists now key those sinks by
node.name, so the old value matched no entry and the picker showed nothing
chosen. The daemon pins it once to the sink that carries the nick now.
"""
from __future__ import annotations

from types import SimpleNamespace

from arctis_sound_manager.pw_utils import migrate_unstable_sink_ids


def _sink(name: str, nick: str) -> SimpleNamespace:
    return SimpleNamespace(name=name, description=name,
                           proplist={"node.name": name, "node.nick": nick})


SINKS = [
    _sink("alsa_output.usb-card.analog-stereo", "USB Audio"),
    _sink("alsa_output.usb-card.iec958-stereo", "USB Audio #1"),
    _sink("alsa_output.usb-card.speakers", "USB Audio #2"),
    _sink("alsa_output.pci-hdmi", "HDMI 0"),
]


def test_suffixed_nick_is_pinned_to_its_sink():
    out = migrate_unstable_sink_ids({"external_output_device": "USB Audio #2"}, SINKS)
    assert out == {"external_output_device": "alsa_output.usb-card.speakers"}


def test_bare_shared_base_nick_is_pinned_when_one_sink_carries_it():
    out = migrate_unstable_sink_ids({"generic_output_device": "USB Audio"}, SINKS)
    assert out == {"generic_output_device": "alsa_output.usb-card.analog-stereo"}


def test_stable_nick_and_node_names_are_left_alone():
    values = {"external_output_device": "HDMI 0",
              "redirect_audio_on_disconnect_device": "alsa_output.usb-card.speakers"}
    assert migrate_unstable_sink_ids(values, SINKS) == {}


def test_nick_carried_by_several_sinks_is_left_alone():
    sinks = [_sink("a", "USB Audio"), _sink("b", "USB Audio")]
    assert migrate_unstable_sink_ids({"external_output_device": "USB Audio"}, sinks) == {}


def test_nick_no_longer_present_is_left_alone():
    assert migrate_unstable_sink_ids({"external_output_device": "USB Audio #5"}, SINKS) == {}
