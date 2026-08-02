# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Rolling buffer of encoded media, so "save the last 30 seconds" can be answered.

This is the part of the clip feature that has to be right, and it is pure
Python on purpose: no Gst import, no portal, no pipeline. The GStreamer side
(:mod:`clip_capture`) feeds it opaque frames and asks it what to write, which
means the trimming rules below are testable without a screen, a GPU or a
compositor.

Two rules shape everything here:

* **A clip must start on a keyframe.** H.264 delta frames are meaningless
  without the keyframe they were predicted from, so cutting at an arbitrary
  timestamp yields a file that starts as a smear of artefacts (or refuses to
  decode). The buffer therefore never returns "the last N seconds" literally:
  it rounds the start *backwards* to the nearest keyframe, so a clip covers at
  least the requested span and overshoots it by less than one keyframe
  interval. Rounding forward instead would guarantee "never more than N", but
  at the cost of dropping up to a keyframe interval off the *front* — and the
  front is where the play the user wanted usually begins. Extra lead-in is
  harmless; a missing opening is the failure people actually notice.

* **Audio is cut to the video, not the other way round.** Audio packets are
  short and independently decodable, so once the video start is pinned to a
  keyframe every audio track is trimmed to that same instant. Doing it in the
  other order would drift each track by up to one keyframe interval and the
  channels would no longer line up with the picture.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Generic, Iterable, TypeVar

log = logging.getLogger(__name__)

# Encoded payload. The buffer never inspects it — it only ever moves it around.
T = TypeVar("T")

NANOSECONDS = 1_000_000_000


@dataclass(frozen=True, slots=True)
class Frame(Generic[T]):
    """One encoded unit, video or audio, with the timestamp it was captured at.

    *pts* is in nanoseconds on the pipeline clock, matching GStreamer's own
    unit so no conversion happens at the boundary. *keyframe* is meaningless
    for audio and is left True there: every audio packet is a valid entry
    point, which makes the trimming code below uniform across track types.
    """

    pts: int
    payload: T
    keyframe: bool = True
    duration: int = 0


@dataclass
class Track(Generic[T]):
    """A single stream's rolling window of frames.

    *window_ns* is how much history to keep. It is deliberately larger than the
    clip length the user asks for: a clip that must start on a keyframe needs
    frames from *before* the requested start to find one, and a buffer holding
    exactly 30 s could only ever produce a 30 s clip by luck.
    """

    name: str
    window_ns: int
    frames: deque[Frame[T]] = field(default_factory=deque)

    def push(self, frame: Frame[T]) -> None:
        """Append *frame* and drop whatever has aged out of the window.

        Frames arriving out of order would corrupt the window (a late frame
        with an old pts could evict everything), so they are dropped rather
        than trusted — encoders can reorder DTS but the PTS handed to us here
        is monotonic per track.
        """
        if self.frames and frame.pts < self.frames[-1].pts:
            log.debug("%s: dropping out-of-order frame (pts %d < %d)",
                      self.name, frame.pts, self.frames[-1].pts)
            return

        self.frames.append(frame)
        self._evict(frame.pts)

    def _evict(self, now_ns: int) -> None:
        """Drop frames older than the window, but never the keyframe still in use.

        Trimming purely by age would eventually evict the keyframe that the
        oldest retained delta frames depend on, leaving a window whose start is
        undecodable. So the cut stops at the last keyframe that is still older
        than the horizon — that keyframe is the earliest legal clip start.
        """
        horizon = now_ns - self.window_ns
        if not self.frames or self.frames[0].pts >= horizon:
            return

        keep_from = 0
        for i, frame in enumerate(self.frames):
            if frame.pts >= horizon:
                break
            if frame.keyframe:
                keep_from = i

        for _ in range(keep_from):
            self.frames.popleft()

    @property
    def span_ns(self) -> int:
        """How much history the track currently holds."""
        if len(self.frames) < 2:
            return 0
        return self.frames[-1].pts - self.frames[0].pts

    def latest_pts(self) -> int | None:
        return self.frames[-1].pts if self.frames else None

    def rate_hz(self, window_ns: int = 3 * NANOSECONDS) -> float:
        """Frames per second over the last *window_ns*, or 0.0 when unknown.

        Measured over a trailing window rather than the whole buffer so it
        reflects what capture is managing *now*: a rate averaged over ninety
        seconds hides a stall, and a stalled capture is exactly the thing worth
        seeing while it is happening.
        """
        if len(self.frames) < 2:
            return 0.0
        cutoff = self.frames[-1].pts - window_ns
        recent = [f for f in self.frames if f.pts >= cutoff]
        if len(recent) < 2:
            return 0.0
        span = recent[-1].pts - recent[0].pts
        if span <= 0:
            return 0.0
        return (len(recent) - 1) * NANOSECONDS / span

    def start_for(self, requested_ns: int, now_ns: int) -> int | None:
        """Pick the keyframe to start a *requested_ns* clip ending at *now_ns*.

        Returns the pts of the newest keyframe at or before the requested
        start, so the clip covers the whole request and overshoots by less than
        one keyframe interval (see the module docstring for why the overshoot
        is preferred to a short front).

        When every keyframe is newer than that — the buffer has not been
        running long enough, or the encoder has not emitted one yet — the
        oldest keyframe is used and the clip is simply shorter, which is the
        honest outcome and better than returning frames nothing can decode.
        """
        if not self.frames:
            return None

        target = now_ns - requested_ns
        best: int | None = None
        for frame in self.frames:
            if not frame.keyframe:
                continue
            if frame.pts <= target:
                best = frame.pts        # keep walking: we want the newest one
            elif best is None:
                return frame.pts        # nothing old enough — take the oldest
            else:
                break
        return best

    def since(self, start_pts: int) -> list[Frame[T]]:
        """Every frame from *start_pts* onwards, in order."""
        return [f for f in self.frames if f.pts >= start_pts]


class ClipBuffer(Generic[T]):
    """The video track plus any number of audio tracks, trimmed together.

    The video track is what a clip is anchored to; audio tracks follow it.
    A buffer with no video track is still valid — an audio-only clip is a
    legitimate degraded mode when screen capture is unavailable — and then the
    request is honoured literally, since every audio packet is a valid start.
    """

    def __init__(self, window_s: float = 90.0):
        self.window_ns = int(window_s * NANOSECONDS)
        self.video: Track[T] | None = None
        self.audio: dict[str, Track[T]] = {}

    def add_video(self, name: str = "video") -> Track[T]:
        self.video = Track(name=name, window_ns=self.window_ns)
        return self.video

    def add_audio(self, name: str) -> Track[T]:
        track = Track(name=name, window_ns=self.window_ns)
        self.audio[name] = track
        return track

    @property
    def tracks(self) -> Iterable[Track[T]]:
        if self.video is not None:
            yield self.video
        yield from self.audio.values()

    def now_ns(self) -> int:
        """The most recent timestamp seen on any track."""
        seen = [t.latest_pts() for t in self.tracks]
        return max((p for p in seen if p is not None), default=0)

    def ready_s(self) -> float:
        """Seconds of history available, i.e. the shortest track's span.

        The clip can only be as long as the *least* filled track — reporting
        the video span alone would promise 30 s while a microphone that joined
        late has 3 s, and the resulting clip would be silent at the front.
        """
        spans = [t.span_ns for t in self.tracks if t.frames]
        return min(spans, default=0) / NANOSECONDS

    def take(self, seconds: float) -> tuple[dict[str, list[Frame[T]]], float]:
        """Cut a clip of at most *seconds*, ending now.

        Returns ``({track name: frames}, actual_seconds)``. The actual length
        is reported rather than assumed: it is what the caller should put in
        the clip's metadata, and it will be shorter than requested whenever the
        buffer has not been running long enough or the nearest keyframe is
        newer than the requested start.
        """
        requested_ns = int(seconds * NANOSECONDS)
        now = self.now_ns()
        if now == 0:
            return {}, 0.0

        if self.video is not None and self.video.frames:
            start = self.video.start_for(requested_ns, now)
        else:
            start = max(now - requested_ns, 0)

        if start is None:
            return {}, 0.0

        out: dict[str, list[Frame[T]]] = {}
        for track in self.tracks:
            frames = track.since(start)
            if frames:
                out[track.name] = frames

        return out, (now - start) / NANOSECONDS

    def clear(self) -> None:
        for track in self.tracks:
            track.frames.clear()
