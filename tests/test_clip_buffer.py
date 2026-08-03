# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the rolling clip buffer.

The rules that matter, and what breaks when they are wrong:

* A clip that does not start on a keyframe decodes as artefacts, so the cut is
  always pulled back to a keyframe — never forward past one.
* Pulling back must not overshoot the request: a 30 s clip may come back as
  28 s (nearest earlier keyframe) but never as 35 s.
* Audio is cut at the video's start, or the channels drift out of sync with
  the picture by up to one keyframe interval.
* Eviction must not drop the keyframe the retained delta frames depend on.
"""
from __future__ import annotations

import pytest

from arctis_sound_manager.clip_buffer import NANOSECONDS, ClipBuffer, Frame, Track


def _s(seconds: float) -> int:
    return int(seconds * NANOSECONDS)


def _fill_video(track: Track, duration_s: float, fps: int = 60, gop: int = 60,
                start_s: float = 0.0) -> None:
    """Push *duration_s* of frames, a keyframe every *gop* frames."""
    step = NANOSECONDS // fps
    for i in range(int(duration_s * fps)):
        pts = _s(start_s) + i * step
        track.push(Frame(pts=pts, payload=f"v{i}", keyframe=(i % gop == 0)))


# ── Track.start_for ────────────────────────────────────────────────────────────

def test_start_lands_on_a_keyframe():
    track = Track(name="video", window_ns=_s(90))
    _fill_video(track, 60.0)

    start = track.start_for(_s(30), track.latest_pts())

    match = next(f for f in track.frames if f.pts == start)
    assert match.keyframe, "clip would start on a delta frame"


def test_start_covers_the_whole_request():
    """The clip must never be short at the front — that is where the play is."""
    track = Track(name="video", window_ns=_s(90))
    _fill_video(track, 60.0)
    now = track.latest_pts()

    start = track.start_for(_s(30), now)

    assert now - start >= _s(30), "clip cut short at the front"


def test_overshoot_is_bounded_by_one_keyframe_interval():
    """Rounding back is allowed to overshoot, but only by one GOP — otherwise
    the clip quietly carries seconds the user never asked for."""
    gop_ns = _s(1)                       # gop=60 at 60 fps
    track = Track(name="video", window_ns=_s(90))
    _fill_video(track, 60.0, gop=60)
    now = track.latest_pts()

    start = track.start_for(_s(30), now)

    assert _s(30) <= now - start < _s(30) + gop_ns


def test_falls_back_to_oldest_keyframe_when_buffer_is_short():
    """Asking for more than the buffer holds yields a shorter clip, not None."""
    track = Track(name="video", window_ns=_s(90))
    _fill_video(track, 5.0)
    now = track.latest_pts()

    start = track.start_for(_s(30), now)

    assert start is not None
    assert now - start <= _s(5)
    assert next(f for f in track.frames if f.pts == start).keyframe


def test_empty_track_has_no_start():
    assert Track(name="video", window_ns=_s(90)).start_for(_s(30), 0) is None


# ── eviction ───────────────────────────────────────────────────────────────────

def test_eviction_keeps_the_keyframe_the_window_depends_on():
    """The oldest retained frame must still be decodable."""
    track = Track(name="video", window_ns=_s(10))
    _fill_video(track, 60.0)

    assert track.frames[0].keyframe, "window starts on an undecodable frame"


def test_eviction_bounds_memory():
    track = Track(name="video", window_ns=_s(10))
    _fill_video(track, 120.0, fps=60)

    # 10 s at 60 fps, plus at most one keyframe interval of lead-in.
    assert len(track.frames) <= 60 * 12


def test_out_of_order_frame_is_dropped():
    track = Track(name="video", window_ns=_s(90))
    track.push(Frame(pts=_s(10), payload="a"))
    track.push(Frame(pts=_s(5), payload="late"))

    assert [f.payload for f in track.frames] == ["a"]


# ── ClipBuffer ─────────────────────────────────────────────────────────────────

def _buffer_with_audio(video_s: float = 60.0, audio_s: float = 60.0) -> ClipBuffer:
    buf: ClipBuffer = ClipBuffer(window_s=90.0)
    _fill_video(buf.add_video(), video_s)
    game = buf.add_audio("game")
    for i in range(int(audio_s * 50)):          # 20 ms packets
        game.push(Frame(pts=i * _s(0.02), payload=f"a{i}"))
    return buf


def test_audio_is_cut_at_the_video_start():
    buf = _buffer_with_audio()

    frames, _ = buf.take(30.0)

    assert frames["video"][0].pts == frames["game"][0].pts or \
        frames["game"][0].pts >= frames["video"][0].pts
    # and no audio from before the video start leaked in
    assert min(f.pts for f in frames["game"]) >= frames["video"][0].pts


def test_take_reports_actual_length_not_requested():
    buf = _buffer_with_audio(video_s=5.0, audio_s=5.0)

    _, actual = buf.take(30.0)

    assert 0 < actual <= 5.0, "reported a length the buffer never held"


def test_take_on_empty_buffer_is_harmless():
    buf: ClipBuffer = ClipBuffer(window_s=90.0)
    buf.add_video()

    assert buf.take(30.0) == ({}, 0.0)


def test_ready_reflects_the_shortest_track():
    """A microphone that joined late caps the clip length — reporting the video
    span alone would promise history the audio cannot supply."""
    buf: ClipBuffer = ClipBuffer(window_s=90.0)
    _fill_video(buf.add_video(), 60.0)
    late = buf.add_audio("mic")
    for i in range(100):                        # 2 s of packets
        late.push(Frame(pts=_s(58) + i * _s(0.02), payload=f"m{i}"))

    assert buf.ready_s() == pytest.approx(2.0, abs=0.1)


def test_audio_only_buffer_honours_the_request_literally():
    """With no video there is no keyframe constraint to round back to."""
    buf: ClipBuffer = ClipBuffer(window_s=90.0)
    track = buf.add_audio("game")
    for i in range(3000):                       # 60 s
        track.push(Frame(pts=i * _s(0.02), payload=f"a{i}"))

    _, actual = buf.take(30.0)

    assert actual == pytest.approx(30.0, abs=0.05)


def test_clear_empties_every_track():
    buf = _buffer_with_audio()
    buf.clear()

    assert buf.take(30.0) == ({}, 0.0)
