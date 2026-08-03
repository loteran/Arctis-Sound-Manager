# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for how clip capture picks its audio sources.

The rule that matters most here is the Bluetooth one. Bluetooth audio cannot
carry high-quality playback and a microphone at the same time: opening a
bluez input forces the card from A2DP onto HSP/HFP, which collapses what the
user is listening to into 16 kHz mono and, on some headsets, drops the link.
Clip capture must therefore never reach for a Bluetooth mic on its own.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from arctis_sound_manager.clip_capture import _default_microphone


def _source(name: str, **props) -> SimpleNamespace:
    return SimpleNamespace(name=name, proplist={"node.name": name, **props})


class _Pulse:
    def __init__(self, sources, default=""):
        self._sources = sources
        self._default = default

    def source_list(self):
        return self._sources

    def server_info(self):
        return SimpleNamespace(default_source_name=self._default)


def _no_setting():
    """Pretend the user has never chosen a microphone."""
    return patch("arctis_sound_manager.settings.GeneralSettings.read_from_file",
                 return_value=SimpleNamespace(micro_input_source="__auto__"))


def test_bluetooth_mic_is_never_chosen_automatically():
    """The defect this guards: capture silently knocking playback off A2DP."""
    pulse = _Pulse([
        _source("bluez_input.30:96:10:49:54:E2", **{"device.api": "bluez5"}),
        _source("alsa_input.usb-HyperX.analog-stereo"),
    ], default="bluez_input.30:96:10:49:54:E2")

    with _no_setting():
        assert _default_microphone(pulse) == "alsa_input.usb-HyperX.analog-stereo"


def test_no_mic_track_rather_than_a_bluetooth_one():
    """With only a Bluetooth mic present, the track is dropped entirely —
    a missing mic track is a far smaller loss than wrecked playback."""
    pulse = _Pulse([_source("bluez_input.AA:BB", **{"device.api": "bluez5"})])

    with _no_setting():
        assert _default_microphone(pulse) is None


def test_bluetooth_mic_is_used_when_explicitly_chosen():
    """The trade-off is the user's to make once they have made it."""
    pulse = _Pulse([
        _source("bluez_input.AA:BB", **{"device.api": "bluez5"}),
        _source("alsa_input.usb-HyperX.analog-stereo"),
    ])
    with patch("arctis_sound_manager.settings.GeneralSettings.read_from_file",
               return_value=SimpleNamespace(micro_input_source="bluez_input.AA:BB")):
        assert _default_microphone(pulse) == "bluez_input.AA:BB"


def test_bluetooth_detected_by_name_without_props():
    """Not every bluez node advertises device.api, so the name counts too."""
    pulse = _Pulse([
        _source("bluez_input.AA:BB"),
        _source("alsa_input.usb-HyperX.analog-stereo"),
    ], default="bluez_input.AA:BB")

    with _no_setting():
        assert _default_microphone(pulse) == "alsa_input.usb-HyperX.analog-stereo"


def test_system_default_wins_among_wired_inputs():
    pulse = _Pulse([
        _source("alsa_input.usb-Webcam.analog-stereo"),
        _source("alsa_input.usb-HyperX.analog-stereo"),
    ], default="alsa_input.usb-HyperX.analog-stereo")

    with _no_setting():
        assert _default_microphone(pulse) == "alsa_input.usb-HyperX.analog-stereo"


def test_monitors_are_never_microphones():
    """A monitor is a sink's own output — recording it as 'the mic' would put
    the game audio on the mic track and double it in the clip."""
    pulse = _Pulse([
        _source("alsa_output.usb-Arctis.analog-stereo.monitor",
                **{"device.class": "monitor"}),
        _source("alsa_input.usb-HyperX.analog-stereo"),
    ])

    with _no_setting():
        assert _default_microphone(pulse) == "alsa_input.usb-HyperX.analog-stereo"


def test_no_inputs_at_all_is_harmless():
    with _no_setting():
        assert _default_microphone(_Pulse([])) is None


# ── which channels get a track ─────────────────────────────────────────────────
#
# The defect these guard: capture wired up only the Sonar channels that had an
# application on them *at the moment it started*. Capture is started before the
# session, the game is launched after it, and the game track therefore never
# existed — a clip with no game audio, unrecoverable after the fact because the
# buffer only ever held what was wired up.

def _sink(index: int, name: str) -> SimpleNamespace:
    return SimpleNamespace(index=index, name=name)


def _stream(sink: int, app: str) -> SimpleNamespace:
    return SimpleNamespace(sink=sink, proplist={"application.name": app})


class _FullPulse:
    def __init__(self, sinks, streams):
        self._sinks, self._streams = sinks, streams

    def sink_list(self):
        return self._sinks

    def sink_input_list(self):
        return self._streams

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _resolve(sinks, streams, mic=None):
    from arctis_sound_manager import clip_capture
    with patch.object(clip_capture, "_default_microphone", return_value=mic), \
         patch("pulsectl.Pulse", return_value=_FullPulse(sinks, streams)):
        return clip_capture.resolve_audio_sources()


SONAR_SINKS = [_sink(1, "Arctis_Game"), _sink(2, "Arctis_Chat"),
               _sink(3, "Arctis_Media")]


def test_idle_sonar_channels_still_get_a_track():
    """The game is launched after capture starts — its channel is empty now."""
    tracks = _resolve(SONAR_SINKS, [_stream(3, "Google Chrome")])
    assert [name for name, _ in tracks] == ["game", "chat", "media"]
    assert dict(tracks)["game"] == "Arctis_Game.monitor"


def test_missing_sonar_channels_are_not_invented():
    """Sonar off: recording monitors that do not exist fails the whole pipeline."""
    tracks = _resolve([_sink(1, "Arctis_Game")], [])
    assert [name for name, _ in tracks] == ["game"]


def test_a_sink_outside_sonar_is_recorded_under_the_app_name():
    """Covers a game left on the headset or on Bluetooth, where every Sonar
    monitor really would be silent."""
    sinks = SONAR_SINKS + [_sink(9, "alsa_output.usb-Arctis")]
    tracks = _resolve(sinks, [_stream(9, "Palworld")])
    assert ("palworld", "alsa_output.usb-Arctis.monitor") in tracks


def test_track_names_survive_an_application_name_with_punctuation():
    """The name becomes a GStreamer element name; brackets there are a parse
    error that takes down every track, not just this one."""
    sinks = SONAR_SINKS + [_sink(9, "alsa_output.usb-Arctis")]
    tracks = _resolve(sinks, [_stream(9, "Rocket League (Steam).exe")])
    label = dict((s, n) for n, s in tracks)["alsa_output.usb-Arctis.monitor"]
    assert label.replace("_", "").isalnum()
    assert label == "rocket_league_steam"


def test_two_sinks_running_the_same_app_do_not_collide():
    """Duplicate appsink names fail parse_launch outright."""
    sinks = SONAR_SINKS + [_sink(9, "sink_a"), _sink(10, "sink_b")]
    tracks = _resolve(sinks, [_stream(9, "Discord"), _stream(10, "Discord")])
    names = [name for name, _ in tracks]
    assert len(names) == len(set(names))


def test_the_mic_is_its_own_track():
    tracks = _resolve(SONAR_SINKS, [], mic="alsa_input.usb-HyperX")
    assert tracks[-1] == ("mic", "alsa_input.usb-HyperX")
