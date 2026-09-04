# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""pw-loopback playback nodes must carry node.pause-on-idle=false.

Issue #223 pinned the *filter-chain* playback nodes (sonar-*-eq.conf, the
HeSuVi chains) with ``node.pause-on-idle=false`` so a passive chain is not
torn down on a momentary idle. The pw-loopback nodes (``Arctis_*_sink_out``)
were left out of that pass: they got ``node.passive=true`` (issue #180) but no
``node.pause-on-idle=false``.

Result: a loopback whose own capture side idles for a moment — Discord closing
and reopening a stream — suspends *itself*, and the channel only comes back
while something else in the chain is actively pushing audio. That is the
reported symptom of a Chat channel that only has sound while a YouTube video
plays on Media: the Media loopback (and the shared downstream) stays awake and
re-pulls the suspended Chat node, but Chat alone does not come back.

The capture side must stay *off* the property: it is the Audio/Sink that
applications play into, and pinning it open would keep the whole chain — and
the headset — awake forever, regressing #180. Same split as the filter-chain
confs guarded by test_issue223.
"""

import pytest

from arctis_sound_manager.loopback_manager import (
    LoopbackSpec,
    _build_pw_loopback_argv,
)


@pytest.mark.parametrize(
    ("channel", "capture", "playback", "target"),
    [
        ("game", "Arctis_Game", "Arctis_Game_sink_out",
         "effect_input.sonar-game-eq"),
        ("chat", "Arctis_Chat", "Arctis_Chat_sink_out",
         "effect_input.sonar-chat-eq"),
        ("media", "Arctis_Media", "Arctis_Media_sink_out",
         "effect_input.sonar-media-eq"),
        ("aux", "Arctis_Aux", "Arctis_Aux_sink_out",
         "effect_input.sonar-aux-eq"),
    ],
)
def test_loopback_playback_pins_pause_on_idle(
    channel: str, capture: str, playback: str, target: str,
) -> None:
    argv = _build_pw_loopback_argv(LoopbackSpec(
        channel=channel, capture_name=capture, playback_name=playback,
        target=target, description=channel.title(),
    ))
    _, capture_props, playback_props = argv[:3]

    # The playback side must never suspend on a momentary idle: once woken by
    # an application, it stays across stream gaps (#223 / the Chat regression).
    assert "node.pause-on-idle=false" in playback_props
    # It stays passive (issue #180): it follows the capture side and never
    # drives the chain on its own.
    assert "node.passive=true" in playback_props

    # The capture side is the sink applications play into; pinning it open
    # would keep the whole chain (and the headset) awake forever (#180).
    assert "node.pause-on-idle" not in capture_props
