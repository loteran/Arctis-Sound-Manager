# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for idle_detect — whole-graph activity detection for #180.

active_channels() must treat a Discord call or a browser tab the same as any
native app: present via pipewire-pulse or not, paused or playing, is exactly
what decides "active", and nothing else.
"""
from __future__ import annotations

from arctis_sound_manager.idle_detect import IdleTracker, active_channels

# Node ids mirroring the real graph.
GAME, CHAT, MEDIA = 10, 11, 12
GAME_EQ, CHAT_EQ = 20, 21
DISCORD, CHROME, PAUSED_APP = 30, 31, 32


def _sink(node_id: int, name: str) -> dict:
    return {
        "id": node_id,
        "type": "PipeWire:Interface:Node",
        "info": {"props": {"node.name": name}},
    }


def _stream(node_id: int, name: str, state: str) -> dict:
    return {
        "id": node_id,
        "type": "PipeWire:Interface:Node",
        "info": {
            "state": state,
            "props": {"node.name": name, "media.class": "Stream/Output/Audio"},
        },
    }


def _link(link_id: int, out_node: int, in_node: int) -> dict:
    return {
        "id": link_id,
        "type": "PipeWire:Interface:Link",
        "info": {"props": {"link.output.node": out_node, "link.input.node": in_node}},
    }


def _sinks() -> list:
    return [_sink(GAME, "Arctis_Game"), _sink(CHAT, "Arctis_Chat"), _sink(MEDIA, "Arctis_Media"),
            _sink(GAME_EQ, "effect_input.sonar-game-eq"), _sink(CHAT_EQ, "effect_input.sonar-chat-eq")]


# ── active_channels ──────────────────────────────────────────────────────

def test_empty_dump_is_inactive():
    assert active_channels([]) == set()


def test_no_streams_is_inactive():
    assert active_channels(_sinks()) == set()


def test_running_stream_on_raw_sink_is_active():
    dump = [*_sinks(),
            _stream(CHROME, "Google Chrome", "running"),
            _link(1, CHROME, MEDIA)]
    assert active_channels(dump) == {"media"}


def test_running_stream_on_eq_input_is_active():
    """A channel with Sonar EQ on: the stream sits on effect_input.*, not the
    raw sink — CHANNEL_SINKS covers both, so this must count the same."""
    dump = [*_sinks(),
            _stream(DISCORD, "Discord", "running"),
            _link(1, DISCORD, CHAT_EQ)]
    assert active_channels(dump) == {"chat"}


def test_pipewire_pulse_client_counts_same_as_a_native_one():
    """The #223-class mistake: excluding pipewire-pulse clients (Discord,
    most browsers) would make this detector blind to exactly the apps a false
    idle-cut must never touch. active_channels() must not special-case the
    client API at all."""
    dump = [*_sinks(),
            _stream(DISCORD, "Discord", "running"),
            _link(1, DISCORD, CHAT)]
    assert "chat" in active_channels(dump)


def test_paused_stream_is_not_active():
    """A long-lived but corked stream (Discord idling with nobody talking, a
    paused video) must not count — presence alone says nothing about #180."""
    dump = [*_sinks(),
            _stream(PAUSED_APP, "Some Player", "idle"),
            _link(1, PAUSED_APP, MEDIA)]
    assert active_channels(dump) == set()


def test_multiple_channels_active_at_once():
    dump = [*_sinks(),
            _stream(DISCORD, "Discord", "running"),
            _stream(CHROME, "Google Chrome", "running"),
            _link(1, DISCORD, CHAT),
            _link(2, CHROME, MEDIA)]
    assert active_channels(dump) == {"chat", "media"}


def test_stream_linked_to_something_else_is_ignored():
    dump = [*_sinks(),
            _stream(CHROME, "Google Chrome", "running"),
            _link(1, CHROME, 999)]  # not a channel sink
    assert active_channels(dump) == set()


# ── IdleTracker ───────────────────────────────────────────────────────────

def test_starts_active_and_does_not_cut_immediately():
    tracker = IdleTracker(idle_after_s=600.0)
    assert tracker.feed(0.0, any_active=False) == "none"
    assert tracker.state == "active"


def test_cuts_after_the_idle_threshold():
    tracker = IdleTracker(idle_after_s=600.0, min_transition_interval_s=0.0)
    tracker.feed(0.0, any_active=False)  # starts the idle clock
    assert tracker.feed(599.0, any_active=False) == "none"
    assert tracker.feed(600.0, any_active=False) == "cut"
    assert tracker.state == "idle"


def test_activity_resets_the_idle_clock():
    tracker = IdleTracker(idle_after_s=600.0, min_transition_interval_s=0.0)
    tracker.feed(0.0, any_active=False)
    tracker.feed(500.0, any_active=True)  # resets the clock
    assert tracker.feed(600.0, any_active=False) == "none"  # only 100s idle so far


def test_restores_on_activity_after_a_cut():
    tracker = IdleTracker(idle_after_s=600.0, min_transition_interval_s=0.0)
    tracker.feed(0.0, any_active=False)
    tracker.feed(600.0, any_active=False)
    assert tracker.state == "idle"
    assert tracker.feed(700.0, any_active=True) == "restore"
    assert tracker.state == "active"


def test_anti_flap_floor_absorbs_a_transition_too_soon():
    tracker = IdleTracker(idle_after_s=10.0, min_transition_interval_s=60.0)
    tracker.feed(0.0, any_active=False)
    assert tracker.feed(10.0, any_active=False) == "cut"
    # Comes back and goes idle again almost immediately — must be absorbed.
    tracker.feed(11.0, any_active=True)
    assert tracker.feed(15.0, any_active=False) == "none"


def test_disarms_after_too_many_transitions_per_hour():
    tracker = IdleTracker(idle_after_s=0.0, min_transition_interval_s=0.0,
                           max_transitions_per_hour=2)
    t = 0.0
    tracker.feed(t, any_active=False)
    for _ in range(6):
        t += 1.0
        tracker.feed(t, any_active=True)
        t += 1.0
        tracker.feed(t, any_active=False)
    assert tracker.disarmed is True


def test_disarmed_tracker_stops_transitioning():
    tracker = IdleTracker(idle_after_s=0.0, min_transition_interval_s=0.0,
                           max_transitions_per_hour=1)
    tracker.disarmed = True
    assert tracker.feed(0.0, any_active=False) == "none"
    assert tracker.feed(1.0, any_active=True) == "none"
