# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Which channel ASM hands the system default to, and which one ChatMix drives.

Two different things were named alike and one of them got the wrong sink.
The headset reports its ChatMix dial as "media mix" and "chat mix" — firmware
vocabulary, in which "media" is the GAME half — so ``PULSE_MEDIA_NODE_NAME``
was set to ``Arctis_Game``. Reading it as "the media sink" is what made ASM
adopt Game as the system default.

Nothing was visibly broken by that, which is why it lasted: audio played, the
channels worked, the mixer looked right. What it silently did was file every
app that follows the default — a browser, a music player, system sounds,
anything the router does not know by name — as game audio, and then let the
ChatMix dial balance that against Discord.
"""
from __future__ import annotations

import threading
from unittest.mock import MagicMock

from arctis_sound_manager.constants import (PULSE_CHAT_NODE_NAME,
                                            PULSE_GAME_NODE_NAME,
                                            PULSE_MEDIA_NODE_NAME)


# ── The constants themselves ──────────────────────────────────────────────────

def test_the_three_channels_are_distinct_sinks():
    """The bug in one line: two of these used to be the same string."""
    assert PULSE_GAME_NODE_NAME == "Arctis_Game"
    assert PULSE_MEDIA_NODE_NAME == "Arctis_Media"
    assert PULSE_CHAT_NODE_NAME == "Arctis_Chat"
    assert len({PULSE_GAME_NODE_NAME, PULSE_MEDIA_NODE_NAME, PULSE_CHAT_NODE_NAME}) == 3


# ── The system default ────────────────────────────────────────────────────────

def _make_engine(*, online: bool = True, on_connect: bool = True, channel: int = 0):
    from arctis_sound_manager.core import CoreEngine

    engine = CoreEngine.__new__(CoreEngine)
    engine._device_lock = threading.Lock()
    engine.is_device_online = MagicMock(return_value=online)
    engine.general_settings = MagicMock(
        redirect_audio_on_connect=on_connect,
        redirect_audio_on_connect_channel=channel,
    )
    engine.pa_audio_manager = MagicMock()
    return engine


def test_the_default_output_goes_to_media():
    """Media is the channel for everything that follows the default."""
    engine = _make_engine()
    engine.redirect_to_media_sink()
    engine.pa_audio_manager.redirect_audio.assert_called_once_with("Arctis_Media")


def test_the_default_output_is_never_game():
    """The regression this file exists for, stated directly."""
    engine = _make_engine()
    engine.redirect_to_media_sink()
    (target,), _ = engine.pa_audio_manager.redirect_audio.call_args
    assert target != PULSE_GAME_NODE_NAME


def test_nothing_is_adopted_while_the_headset_is_off():
    """The channels point at the headset, so taking the default with it off
    would move audio onto a device that is not there."""
    engine = _make_engine(online=False)
    engine.redirect_to_media_sink()
    engine.pa_audio_manager.redirect_audio.assert_not_called()


def test_the_setting_still_wins():
    engine = _make_engine(on_connect=False)
    engine.redirect_to_media_sink()
    engine.pa_audio_manager.redirect_audio.assert_not_called()


# ── The configurable channel (issue #273) ───────────────────────────────────

def test_channel_setting_can_target_game():
    """A user whose system default is already the Arctis ALSA node does not
    want Media as a second, unasked-for sink — they can point new apps at
    Game instead."""
    engine = _make_engine(channel=1)
    engine.redirect_to_media_sink()
    engine.pa_audio_manager.redirect_audio.assert_called_once_with(PULSE_GAME_NODE_NAME)


def test_channel_setting_can_target_chat():
    engine = _make_engine(channel=2)
    engine.redirect_to_media_sink()
    engine.pa_audio_manager.redirect_audio.assert_called_once_with(PULSE_CHAT_NODE_NAME)


def test_channel_setting_falls_back_to_media_on_bad_value():
    """A stale/corrupt settings file must not crash the daemon on connect."""
    engine = _make_engine(channel=99)
    engine.redirect_to_media_sink()
    engine.pa_audio_manager.redirect_audio.assert_called_once_with(PULSE_MEDIA_NODE_NAME)


# ── The ChatMix dial ──────────────────────────────────────────────────────────

def _make_pactl(sink_names: list[str]):
    from arctis_sound_manager.pactl import PulseAudioManager

    mgr = PulseAudioManager.__new__(PulseAudioManager)
    mgr.logger = MagicMock()
    mgr.pulse = MagicMock()
    sinks = [MagicMock(proplist={"node.name": n}) for n in sink_names]
    mgr.get_arctis_sinks = MagicMock(return_value=sinks)
    return mgr, {s.proplist["node.name"]: s for s in sinks}


def test_chatmix_still_drives_game_not_media():
    """The other half of the fix: renaming the constant must not move the dial.

    The firmware's "media mix" is the Game side of the balance. Pointing this
    at Arctis_Media would have left the dial with no effect on game audio —
    trading one silent misrouting for a louder one.
    """
    mgr, by_name = _make_pactl([PULSE_GAME_NODE_NAME, PULSE_CHAT_NODE_NAME])

    mgr.set_mix(media_mix=80, chat_mix=40)

    volumes = {
        call.args[0]: call.args[1]
        for call in mgr.pulse.volume_set_all_chans.call_args_list
    }
    assert volumes[by_name[PULSE_GAME_NODE_NAME]] == 0.8
    assert volumes[by_name[PULSE_CHAT_NODE_NAME]] == 0.4


def test_chatmix_leaves_the_media_channel_alone():
    """Media follows the system default; the dial has no business touching it."""
    mgr, by_name = _make_pactl(
        [PULSE_GAME_NODE_NAME, PULSE_CHAT_NODE_NAME, PULSE_MEDIA_NODE_NAME]
    )

    mgr.set_mix(media_mix=80, chat_mix=40)

    touched = {call.args[0] for call in mgr.pulse.volume_set_all_chans.call_args_list}
    assert by_name[PULSE_MEDIA_NODE_NAME] not in touched
