# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""redirect_audio() must not undo the user's routing pins.

Observed live: the daemon logs "Redirecting audio to Arctis_Game", and five
seconds later the media router logs "Manual move detected: 'Firefox' ->
Arctis_Game (saving override)". The daemon had moved every stream parked on an
ASM sink onto the new default — including apps the user had pinned elsewhere —
and the router, which watches for manual moves, read ASM's own move as the
user's and persisted the wrong sink. Each restart flipped the pin back.

The stream migration exists to rescue apps stuck on a *dead* loopback
(issue #50). An app sitting on a live sink it was pinned to is not that case.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from arctis_sound_manager.pactl import PulseAudioManager as PAM


class _SI:
    def __init__(self, index, sink, app, binary=""):
        self.index, self.sink = index, sink
        self.proplist = {"application.name": app, "application.process.binary": binary}


def test_pinned_stream_on_its_sink_is_left_alone():
    si = _SI(1, sink=7, app="Firefox", binary="firefox")
    assert PAM._stream_is_where_the_user_put_it(
        si, "Arctis_Media", {"Firefox": "Arctis_Media"}
    )


def test_pinned_stream_elsewhere_is_still_migrated():
    """A pin to Media does not protect a stream that is sitting on Game."""
    si = _SI(1, sink=7, app="Firefox", binary="firefox")
    assert not PAM._stream_is_where_the_user_put_it(
        si, "Arctis_Game", {"Firefox": "Arctis_Media"}
    )


def test_composite_key_pin_is_honoured():
    """Generic app names are keyed "name|binary" (issue #108) — two Electron
    apps both reporting "Chromium" must not share one pin."""
    si = _SI(1, sink=7, app="Chromium", binary="vesktop")
    assert PAM._stream_is_where_the_user_put_it(
        si, "Arctis_Chat", {"Chromium|vesktop": "Arctis_Chat"}
    )
    # The other Chromium app is not pinned there, so it stays migratable.
    other = _SI(2, sink=7, app="Chromium", binary="pear-desktop")
    assert not PAM._stream_is_where_the_user_put_it(
        other, "Arctis_Chat", {"Chromium|vesktop": "Arctis_Chat"}
    )


def test_unpinned_stream_is_migrated():
    si = _SI(1, sink=7, app="mpv", binary="mpv")
    assert not PAM._stream_is_where_the_user_put_it(
        si, "Arctis_Media", {"Firefox": "Arctis_Media"}
    )


def test_no_overrides_at_all_migrates_everything():
    si = _SI(1, sink=7, app="Firefox", binary="firefox")
    assert not PAM._stream_is_where_the_user_put_it(si, "Arctis_Media", {})


def test_redirect_skips_pinned_streams(monkeypatch):
    """End to end: the pinned app stays put, the unpinned one is moved."""
    mgr = PAM.__new__(PAM)
    mgr.logger = MagicMock()
    mgr.pulse = MagicMock()

    target = MagicMock(index=99)
    target.proplist = {"node.nick": "Arctis_Game", "node.name": "Arctis_Game"}
    target.name = "Arctis_Game"
    media = MagicMock(index=7)
    media.proplist = {"node.name": "Arctis_Media"}
    media.name = "Arctis_Media"

    monkeypatch.setattr(PAM, "sink_list_wrapper", lambda self: [target, media])
    monkeypatch.setattr(PAM, "_routing_overrides",
                        staticmethod(lambda: {"Firefox": "Arctis_Media"}))
    mgr.pulse.sink_input_list.return_value = [
        _SI(1, sink=7, app="Firefox", binary="firefox"),  # pinned to Media
        _SI(2, sink=7, app="mpv", binary="mpv"),          # not pinned
    ]
    monkeypatch.setattr("shutil.which", lambda _n: None)

    mgr.redirect_audio("Arctis_Game")

    moved = [c.args[0] for c in mgr.pulse.sink_input_move.call_args_list]
    assert 1 not in moved, "a stream pinned to its current sink must not be moved"
    assert 2 in moved, "an unpinned stream must still be migrated (issue #50)"
