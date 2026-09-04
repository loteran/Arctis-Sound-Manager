# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Detect a loopback playback node that is SUSPENDED while audio flows.

Issue #223 pinned the loopback playback nodes with
``node.pause-on-idle=false`` so they never self-suspend.  The watchdog now
also checks at runtime: if a playback node is SUSPENDED while its capture
side is active, the audio path is broken and the loopback must be
recreated.  This prevents regressions of the #223 class — the exact silent
failure where a channel's playback node self-suspended and never re-pulled.

The helper ``_is_suspended_abnormally`` is a pure function over pw-dump
data, extracted so it can be unit-tested without a running PipeWire stack.
"""

from arctis_sound_manager.core import _is_suspended_abnormally


# ── Helpers ──────────────────────────────────────────────────────────────

def _make_node(name: str, state: str | None = None) -> dict:
    """Build a minimal PipeWire:Interface:Node object as pw-dump produces it."""
    info: dict = {"props": {"node.name": name}}
    if state is not None:
        info["state"] = state
    return {"type": "PipeWire:Interface:Node", "info": info}


def _graph(capture_state: str | None, playback_state: str | None) -> list:
    """Build a minimal two-node pw-dump for a single loopback channel."""
    return [
        _make_node("Arctis_Game", capture_state),
        _make_node("Arctis_Game_sink_out", playback_state),
    ]


# ── Tests ────────────────────────────────────────────────────────────────

class TestSuspendedDetection:
    """_is_suspended_abnormally: playback SUSPENDED + capture active → True."""

    def test_capture_running_playback_suspended(self) -> None:
        """The exact #223 regression: audio playing, playback stuck suspended."""
        data = _graph("running", "suspended")
        assert _is_suspended_abnormally(data, "Arctis_Game", "Arctis_Game_sink_out")

    def test_capture_idle_playback_suspended(self) -> None:
        """Capture just stopped (idle) but playback should be idle too, not suspended."""
        data = _graph("idle", "suspended")
        assert _is_suspended_abnormally(data, "Arctis_Game", "Arctis_Game_sink_out")

    def test_both_suspended_is_normal(self) -> None:
        """Both sides idle/suspended → nothing playing, the normal rest state."""
        data = _graph("suspended", "suspended")
        assert not _is_suspended_abnormally(data, "Arctis_Game", "Arctis_Game_sink_out")

    def test_both_running_is_normal(self) -> None:
        """Both running → audio flowing normally."""
        data = _graph("running", "running")
        assert not _is_suspended_abnormally(data, "Arctis_Game", "Arctis_Game_sink_out")

    def test_both_idle_is_normal(self) -> None:
        """Both idle → momentary gap, pause-on-idle keeps them warm."""
        data = _graph("idle", "idle")
        assert not _is_suspended_abnormally(data, "Arctis_Game", "Arctis_Game_sink_out")

    def test_playback_running_capture_absent(self) -> None:
        """Playback active, capture node not in graph — not our problem."""
        data = [_make_node("Arctis_Game_sink_out", "running")]
        assert not _is_suspended_abnormally(data, "Arctis_Game", "Arctis_Game_sink_out")

    def test_both_absent(self) -> None:
        """Neither node in the dump — dead-process pass handles it."""
        data: list = []
        assert not _is_suspended_abnormally(data, "Arctis_Game", "Arctis_Game_sink_out")

    def test_capture_suspended_playback_suspended(self) -> None:
        """Both suspended → nothing playing, normal rest state."""
        data = _graph("suspended", "suspended")
        assert not _is_suspended_abnormally(data, "Arctis_Game", "Arctis_Game_sink_out")

    def test_other_nodes_not_confused(self) -> None:
        """Unrelated nodes in the dump do not interfere with detection."""
        data = _graph("running", "suspended") + [
            _make_node("Some_Other_Sink", "running"),
            _make_node("Unrelated_sink_out", "suspended"),
        ]
        assert _is_suspended_abnormally(data, "Arctis_Game", "Arctis_Game_sink_out")

    def test_channel_names_dont_cross(self) -> None:
        """Game's playback should not be confused with Chat's capture."""
        data = [
            _make_node("Arctis_Game", "running"),
            _make_node("Arctis_Game_sink_out", "suspended"),
            _make_node("Arctis_Chat", "suspended"),
            _make_node("Arctis_Chat_sink_out", "running"),
        ]
        # Game: capture running + playback suspended → abnormal
        assert _is_suspended_abnormally(data, "Arctis_Game", "Arctis_Game_sink_out")
        # Chat: capture suspended + playback running → NOT abnormal
        assert not _is_suspended_abnormally(data, "Arctis_Chat", "Arctis_Chat_sink_out")
