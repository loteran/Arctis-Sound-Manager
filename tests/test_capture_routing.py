# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Recording streams must reach the Game channel, and never the microphone.

Issue #225: the router only ever walked sink-inputs, so a capture stream such
as Steam's Game Recording was left wherever WirePlumber's fallback put it. The
clips came back silent, or carrying only whatever sat on Media.

The correctness of the fix rests on one rule, pinned by
``test_microphone_capture_is_never_moved`` below: a capture stream is moved
only when it is *already* reading a monitor. Mirroring the sink-input handling
wholesale — as the issue suggested — would also catch Discord or OBS reading
the microphone and replace the user's voice with the game audio. That failure
already happened once in this project from the other direction and is pinned by
tests/test_mic_never_fed_by_an_output.py; it is silent to whoever causes it.
"""
from __future__ import annotations

import pytest

from arctis_sound_manager.power_status import HeadsetPower
from arctis_sound_manager.scripts import video_router

GAME_MON = "Arctis_Game.monitor"
MEDIA_MON = "Arctis_Media.monitor"
CHAT_MON = "Arctis_Chat.monitor"
MIC = "alsa_input.usb-HP__Inc_HyperX_DuoCast-00.analog-stereo"


class _FakeSource:
    def __init__(self, index: int, name: str):
        self.index = index
        self.name = name


class _FakeSourceOutput:
    def __init__(self, index: int, source: int, proplist: dict):
        self.index = index
        self.source = source
        self.proplist = proplist


class _FakePulse:
    """Stand-in for pulsectl.Pulse covering what the capture pass uses."""

    def __init__(self, sources: list, source_outputs: list):
        self._sources = sources
        self._source_outputs = source_outputs
        self.moves: list[tuple[int, int]] = []

    def source_list(self):
        return self._sources

    def source_output_list(self):
        return self._source_outputs

    def source_output_move(self, so_index: int, target_index: int):
        self.moves.append((so_index, target_index))
        for so in self._source_outputs:
            if so.index == so_index:
                so.source = target_index


def _sources() -> list[_FakeSource]:
    return [
        _FakeSource(0, GAME_MON),
        _FakeSource(1, MEDIA_MON),
        _FakeSource(2, CHAT_MON),
        _FakeSource(3, MIC),
    ]


def _steam(source_index: int) -> _FakeSourceOutput:
    return _FakeSourceOutput(
        10, source_index,
        {"application.name": "Steam", "media.class": "Stream/Input/Audio"},
    )


def test_recorder_on_wrong_monitor_moves_to_game():
    """The reported case: Steam parked on Media, so the clip has no game audio."""
    pulse = _FakePulse(_sources(), [_steam(1)])

    video_router._route_capture_streams(pulse, HeadsetPower.ON, {})

    assert pulse.moves == [(10, 0)], "Steam should have been moved to the Game monitor"


def test_microphone_capture_is_never_moved():
    """The guard the whole design rests on.

    Discord reading the microphone is a source-output exactly like Steam's
    recorder. Moving it onto a monitor would send the game audio out as the
    user's voice.
    """
    discord = _FakeSourceOutput(
        11, 3, {"application.name": "Discord", "media.class": "Stream/Input/Audio"})
    pulse = _FakePulse(_sources(), [discord])

    video_router._route_capture_streams(pulse, HeadsetPower.ON, {})

    assert pulse.moves == []
    assert discord.source == 3, "the microphone feed must stay on the microphone"


def test_override_pins_recorder_to_another_channel():
    """routing_overrides.json wins over the Game default."""
    pulse = _FakePulse(_sources(), [_steam(0)])

    video_router._route_capture_streams(pulse, HeadsetPower.ON, {"Steam": "Arctis_Chat"})

    assert pulse.moves == [(10, 2)]


def test_headset_off_leaves_capture_alone():
    """Every channel is equally silent, so moving between them proves nothing."""
    pulse = _FakePulse(_sources(), [_steam(1)])

    video_router._route_capture_streams(pulse, HeadsetPower.OFF, {})

    assert pulse.moves == []


def test_already_on_target_does_not_move():
    """No churn once the stream is where it belongs."""
    pulse = _FakePulse(_sources(), [_steam(0)])

    video_router._route_capture_streams(pulse, HeadsetPower.ON, {})

    assert pulse.moves == []


def test_missing_game_monitor_is_a_no_op():
    """With the channels not built yet there is nowhere to send a recorder."""
    sources = [_FakeSource(1, MEDIA_MON), _FakeSource(3, MIC)]
    pulse = _FakePulse(sources, [_steam(1)])

    video_router._route_capture_streams(pulse, HeadsetPower.ON, {})

    assert pulse.moves == []


def test_stream_without_application_name_is_ignored():
    """Anonymous streams carry no identity to route on."""
    anon = _FakeSourceOutput(12, 1, {"media.class": "Stream/Input/Audio"})
    pulse = _FakePulse(_sources(), [anon])

    video_router._route_capture_streams(pulse, HeadsetPower.ON, {})

    assert pulse.moves == []


def test_move_failure_is_logged_not_raised():
    """A stream that vanishes mid-tick must not take the whole tick down."""
    pulse = _FakePulse(_sources(), [_steam(1)])

    def _boom(*_args):
        raise RuntimeError("no such entity")

    pulse.source_output_move = _boom

    video_router._route_capture_streams(pulse, HeadsetPower.ON, {})  # must not raise


@pytest.mark.parametrize("name,expected", [
    (GAME_MON, True),
    (MIC, False),
    ("", False),
])
def test_is_monitor_source(name, expected):
    assert video_router._is_monitor_source(name) is expected
