# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the capture rate a clip reports and records.

Measured on real recordings: the files claimed 239 fps — the portal stream's
`max-framerate` — while holding 24 to 37 frames per second of actual video.
Players believe the header, so the clips stuttered and looked frozen when the
frames themselves were fine. Two things follow from that: the rate has to be
measured from the frames, and it has to be visible while capturing rather than
discovered afterwards.
"""
from __future__ import annotations

import pytest

from arctis_sound_manager.clip_buffer import NANOSECONDS, ClipBuffer, Frame, Track


def _s(seconds: float) -> int:
    return int(seconds * NANOSECONDS)


def _fill(track: Track, fps: float, seconds: float, start_s: float = 0.0) -> None:
    step = int(NANOSECONDS / fps)
    for i in range(int(seconds * fps)):
        track.push(Frame(pts=_s(start_s) + i * step, payload=f"f{i}"))


# ── measured rate ──────────────────────────────────────────────────────────────

def test_rate_matches_the_frames_pushed():
    track = Track(name="video", window_ns=_s(90))
    _fill(track, fps=60, seconds=10)

    assert track.rate_hz() == pytest.approx(60, rel=0.05)


def test_a_slow_capture_reports_its_real_rate():
    """The case that shipped: 24 fps of frames in a file labelled 239."""
    track = Track(name="video", window_ns=_s(90))
    _fill(track, fps=24, seconds=10)

    assert track.rate_hz() == pytest.approx(24, rel=0.05)


def test_rate_reflects_now_not_the_whole_buffer():
    """A rate averaged over the entire history hides a stall, which is exactly
    the thing worth seeing while it is happening."""
    track = Track(name="video", window_ns=_s(90))
    _fill(track, fps=60, seconds=20)
    _fill(track, fps=10, seconds=4, start_s=20.0)

    assert track.rate_hz() == pytest.approx(10, rel=0.2)


def test_too_few_frames_reports_nothing_rather_than_guessing():
    track = Track(name="video", window_ns=_s(90))
    assert track.rate_hz() == 0.0

    track.push(Frame(pts=0, payload="only"))
    assert track.rate_hz() == 0.0


def test_frames_all_at_one_instant_do_not_divide_by_zero():
    track = Track(name="video", window_ns=_s(90))
    for i in range(5):
        track.push(Frame(pts=0, payload=f"f{i}"))

    assert track.rate_hz() == 0.0


def test_buffer_reports_the_video_rate():
    buf: ClipBuffer = ClipBuffer(window_s=90.0)
    _fill(buf.add_video(), fps=48, seconds=8)

    assert buf.video is not None
    assert buf.video.rate_hz() == pytest.approx(48, rel=0.05)


# ── what gets written into the clip ────────────────────────────────────────────

# The stub below mirrors the *serialised* caps API — Gst.Caps.from_string() and
# caps.to_string() — and nothing else.
#
# Its predecessor invented `Gst.Fraction(num, den)` and `caps.set_value()`. Both
# read like the GStreamer API and neither exists in PyGObject 3.56: every save
# died with "Fraction() takes no arguments" while these tests stayed green. A
# stub that answers calls the real library refuses is worse than no test, so
# what it stands in for is kept to the two calls verified against the real
# library — and where GStreamer is installed, the real one is used instead.

_SOURCE_CAPS = ("video/x-raw, width=(int)1920, height=(int)1080, "
                "framerate=(fraction)0/1, max-framerate=(fraction)239/1")


class _StringCaps:
    def __init__(self, text):
        self.text = text

    def to_string(self):
        return self.text


class _StringGst:
    class Caps:
        @staticmethod
        def from_string(text):
            return _StringCaps(text)


def _real_gst():
    """The installed GStreamer, or None. Preferred over the stub so the API
    these tests assume cannot drift away from the API that ships."""
    try:
        import gi

        gi.require_version("Gst", "1.0")
        from gi.repository import Gst

        Gst.init(None)
        return Gst
    except Exception:
        return None


def _stamp(frames, caps_text: str = _SOURCE_CAPS):
    """Drive ClipCapture._with_measured_framerate without a live pipeline."""
    from arctis_sound_manager.clip_capture import ClipCapture

    gst = _real_gst() or _StringGst
    capture = ClipCapture.__new__(ClipCapture)
    capture._Gst = gst
    caps = gst.Caps.from_string(caps_text)
    return ClipCapture._with_measured_framerate(capture, caps, frames)


def _framerate(caps) -> float:
    """The framerate the caps would put in the file's header."""
    import re

    # The leading boundary matters: max-framerate ends in "framerate=..." too,
    # and it is the field that made these clips claim 239 fps in the first place.
    matches = re.findall(r"(?:^|[\s,])framerate=\(fraction\)(\d+)/(\d+)",
                         caps.to_string())
    assert matches, f"no framerate in {caps.to_string()!r}"
    num, den = matches[-1]          # a later assignment overrides an earlier one
    return int(num) / int(den)


def _frames(fps: float, count: int) -> list[Frame]:
    step = int(NANOSECONDS / fps)
    return [Frame(pts=i * step, payload=b"") for i in range(count)]


def test_written_framerate_is_the_measured_one():
    """Not the stream's max-framerate — that is what made the files claim 239."""
    assert _framerate(_stamp(_frames(fps=30, count=300))) == pytest.approx(30, rel=0.02)


def test_a_low_capture_rate_is_recorded_honestly():
    assert _framerate(_stamp(_frames(fps=24, count=240))) == pytest.approx(24, rel=0.02)


def test_stamping_the_rate_does_not_raise(caplog):
    """The regression that shipped: this call raised TypeError, and it is on the
    path of every single save — button and shortcut alike — so no clip could be
    written at all. Any exception here is total loss of the feature."""
    caps = _stamp(_frames(fps=30, count=120))
    assert "video/x-raw" in caps.to_string()


def test_the_rest_of_the_caps_survives_the_restamp():
    """Resolution is not the framerate's to lose."""
    text = _stamp(_frames(fps=30, count=120)).to_string()
    assert "1920" in text and "1080" in text


def test_single_frame_leaves_the_caps_untouched():
    caps = _stamp(_frames(fps=30, count=1))

    assert _framerate(caps) == 0.0


def test_absurd_measurement_is_not_written():
    """A stalled capture or a single burst must not produce a header worse than
    the one it replaces."""
    caps = _stamp([Frame(pts=0, payload=b""), Frame(pts=1, payload=b"")])

    assert _framerate(caps) == 0.0
