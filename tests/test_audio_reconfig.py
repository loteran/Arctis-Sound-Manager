# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""The reconfiguration window, and what it refuses to move.

``audio_reconfig`` exists so a graph rebuild (EQ change, profile switch, Sonar
toggle, "restart the audio engine") is not mistaken for the user deliberately
dragging their game onto another channel. The router asks
:func:`audio_reconfig.in_progress` before persisting a move; if that ever
returns True when no rebuild is happening, the router stops recording manual
moves at all, and if it returns False during one, a momentary flicker gets
written down as the user's intent.

Both directions are pinned here, along with the two silent ways the marker can
lie: a stale file left by a process that died mid-restart, and a truncated one.
"""
from __future__ import annotations

import time

import pytest

from arctis_sound_manager import audio_reconfig


@pytest.fixture(autouse=True)
def _isolated_marker(tmp_path, monkeypatch):
    """Keep every test off the real XDG_RUNTIME_DIR marker."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    yield


def test_no_marker_means_no_window():
    assert audio_reconfig.in_progress() is False


def test_begin_opens_the_window():
    audio_reconfig.begin(30.0)
    assert audio_reconfig.in_progress() is True


def test_an_expired_marker_counts_as_closed():
    """A process that died mid-restart must not pin the window open forever."""
    audio_reconfig.begin(10.0)
    assert audio_reconfig.in_progress(now=time.time() + 3600) is False


def test_a_truncated_marker_counts_as_closed():
    """Garbage must not read as an open window.

    Treating an unreadable marker as "reconfiguring" would stop the router
    from ever recording a manual move again, with nothing in the log saying so.
    """
    audio_reconfig._marker_path().parent.mkdir(parents=True, exist_ok=True)
    audio_reconfig._marker_path().write_text("not-a-float\n")
    assert audio_reconfig.in_progress() is False


def test_begin_extends_but_never_shortens():
    """A nested restart must not cut short a window somebody else opened."""
    audio_reconfig.begin(600.0)
    far = audio_reconfig._read_expiry(audio_reconfig._marker_path())

    audio_reconfig.begin(1.0)

    assert audio_reconfig._read_expiry(audio_reconfig._marker_path()) == far


def test_end_leaves_a_settling_tail():
    """Closing keeps a short tail so the router can settle, not an abrupt stop."""
    audio_reconfig.begin(600.0)
    audio_reconfig.end()

    remaining = audio_reconfig._read_expiry(audio_reconfig._marker_path()) - time.time()
    assert 0 < remaining <= audio_reconfig._CLOSE_TAIL_S + 1


def test_end_without_a_window_is_harmless():
    audio_reconfig.end()
    assert audio_reconfig.in_progress() is False


def test_end_never_extends_a_shorter_window():
    audio_reconfig.begin(0.5)
    before = audio_reconfig._read_expiry(audio_reconfig._marker_path())
    audio_reconfig.end()
    assert audio_reconfig._read_expiry(audio_reconfig._marker_path()) == before


class TestStreamIdentity:
    """What the snapshot is allowed to pick up.

    ASM's own plumbing appears as sink-inputs like any application. Restoring
    those would rewire the graph itself rather than put a user's app back.
    """

    def test_a_normal_app_is_identified(self):
        props = {"application.name": "Steam",
                 "application.process.binary": "steam"}
        assert audio_reconfig._stream_identity(props) is not None

    @pytest.mark.parametrize("binary", ["pipewire", "pw-loopback", "pw-cli"])
    def test_asm_plumbing_is_skipped(self, binary):
        props = {"application.name": "x", "application.process.binary": binary}
        assert audio_reconfig._stream_identity(props) is None

    @pytest.mark.parametrize("media", ["EQ output", "Virtual Surround", "Sonar Game"])
    def test_internal_nodes_are_skipped(self, media):
        props = {"application.name": "x", "application.process.binary": "y",
                 "media.name": media}
        assert audio_reconfig._stream_identity(props) is None

    def test_a_nameless_stream_has_no_identity(self):
        assert audio_reconfig._stream_identity({}) is None


def test_context_manager_opens_then_closes(monkeypatch):
    """The window must survive the block and close after it."""
    monkeypatch.setattr(audio_reconfig, "snapshot_channel_streams", lambda: {})

    with audio_reconfig.audio_reconfiguration(duration_s=600.0, restore=False):
        assert audio_reconfig.in_progress() is True

    remaining = audio_reconfig._read_expiry(audio_reconfig._marker_path()) - time.time()
    assert remaining <= audio_reconfig._CLOSE_TAIL_S + 1


def test_context_manager_closes_even_when_the_block_raises(monkeypatch):
    """A failed restart is exactly when the window must not stay wedged open."""
    monkeypatch.setattr(audio_reconfig, "snapshot_channel_streams", lambda: {})

    with pytest.raises(RuntimeError):
        with audio_reconfig.audio_reconfiguration(duration_s=600.0, restore=False):
            raise RuntimeError("restart failed")

    remaining = audio_reconfig._read_expiry(audio_reconfig._marker_path()) - time.time()
    assert remaining <= audio_reconfig._CLOSE_TAIL_S + 1
