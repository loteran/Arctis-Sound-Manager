# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Which of a clip's channels a player picks when you just open the file.

Reported from use as "the clips have no sound — it failed to capture the
audio". The capture was fine: the reported clip held five Opus tracks and two
of them were loud. What was wrong is that all five said they were the default
one. Matroska's FlagDefault is 1 when it is not written and matroskamux never
writes it, so every player was choosing among five defaults by its own
tie-break — and since most channels in a real session are empty (nothing
routed to Chat, microphone muted), what it kept choosing was silence.

The channel to keep is decided by encoded size, which the buffer already
knows. Opus compresses silence to nearly nothing: in the reported clip the
three empty channels were ~15 kB each against 30 kB and 151 kB for the real
ones, so the answer is free and needs no decode.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from arctis_sound_manager.clip_buffer import Frame
from arctis_sound_manager.clip_capture import _mark_default_audio_track


def _frames(sizes: dict[str, int]) -> dict[str, list[Frame]]:
    """A stand-in for the buffer save_clip holds, sized per channel."""
    return {name: [Frame(pts=0, payload=b"\0" * size)]
            for name, size in sizes.items()}


def _clip(tmp_path: Path, channels: int) -> Path:
    """A Matroska with *channels* Opus tracks, in the state capture leaves them.

    Built with ffmpeg because GStreamer is optional here — but ffmpeg writes
    FlagDefault properly and marks only the first track, so every track is
    flagged explicitly to reproduce what matroskamux actually produces. That
    state was read off a real capture: a five-channel clip whose audio
    dispositions were `1,1,1,1,1`. Without this the fixture would start from
    the answer and the tests below would pass on a file that never existed.
    """
    path = tmp_path / "clip.mkv"
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
           "-f", "lavfi", "-i", "testsrc=d=1:r=10:s=160x120"]
    for index in range(channels):
        cmd += ["-f", "lavfi", "-i", f"sine=f={220 * (index + 1)}:r=48000"]
    cmd += ["-t", "1", "-map", "0:v"]
    for index in range(channels):
        cmd += ["-map", f"{index + 1}:a"]
    cmd += ["-c:v", "libx264", "-c:a", "libopus", "-disposition:a", "default",
            str(path)]
    subprocess.run(cmd, check=True, capture_output=True, timeout=60)
    return path


def _defaults(path: Path) -> list[str]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream_disposition=default", "-of", "csv=p=0",
         str(path)], capture_output=True, text=True, timeout=30)
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg not installed")


@needs_ffmpeg
def test_the_fixture_starts_from_the_broken_state(tmp_path):
    """Everything below is worthless if the input already has one default —
    which is what ffmpeg writes by itself, and is not what capture produces."""
    assert _defaults(_clip(tmp_path, 3)) == ["1", "1", "1"]


@needs_ffmpeg
def test_exactly_one_channel_is_left_as_the_default(tmp_path):
    clip = _clip(tmp_path, 3)
    _mark_default_audio_track(
        clip, ["video", "game", "chat", "mic"],
        _frames({"game": 30_000, "chat": 15_000, "mic": 15_000}))

    assert _defaults(clip).count("1") == 1


@needs_ffmpeg
def test_the_channel_that_holds_audio_is_the_one_kept(tmp_path):
    """The reported clip's Game channel was empty — the user played through the
    headset directly — while a browser tab was the loudest thing in it. Keeping
    the first channel would have defaulted that clip to silence."""
    clip = _clip(tmp_path, 5)
    _mark_default_audio_track(
        clip, ["video", "game", "chat", "media", "google_chrome", "mic"],
        _frames({"game": 29_851, "chat": 14_999, "media": 15_008,
                 "google_chrome": 151_249, "mic": 14_981}))

    assert _defaults(clip) == ["0", "0", "0", "1", "0"]


@needs_ffmpeg
def test_every_channel_survives_the_remux(tmp_path):
    """It is a stream copy and a flag change; losing a channel here would cost
    the whole reason the clip is multi-track."""
    clip = _clip(tmp_path, 4)
    before = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_name",
         "-of", "csv=p=0", str(clip)], capture_output=True, text=True).stdout
    _mark_default_audio_track(
        clip, ["video", "a", "b", "c", "d"],
        _frames({"a": 1, "b": 2, "c": 3, "d": 4}))
    after = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_name",
         "-of", "csv=p=0", str(clip)], capture_output=True, text=True).stdout

    assert after == before


@needs_ffmpeg
def test_a_single_channel_clip_is_left_alone(tmp_path):
    """Nothing to disambiguate, so nothing is worth a remux for."""
    clip = _clip(tmp_path, 1)
    mtime = clip.stat().st_mtime_ns
    _mark_default_audio_track(clip, ["video", "game"], _frames({"game": 999}))

    assert clip.stat().st_mtime_ns == mtime


def test_no_ffmpeg_leaves_the_clip_exactly_as_written(tmp_path, monkeypatch):
    """A clip with an awkward default flag is worth immeasurably more than no
    clip, so every failure here is silent and non-destructive."""
    from arctis_sound_manager import clip_capture

    clip = tmp_path / "clip.mkv"
    clip.write_bytes(b"not really a matroska")
    monkeypatch.setattr(clip_capture.shutil, "which", lambda name: None)

    _mark_default_audio_track(clip, ["video", "game", "chat"],
                              _frames({"game": 10, "chat": 20}))

    assert clip.read_bytes() == b"not really a matroska"


@needs_ffmpeg
def test_a_failed_remux_does_not_destroy_the_clip(tmp_path):
    """ffmpeg refusing the file must not leave a truncated one behind — the
    clip is already on disk and is the thing being protected."""
    clip = tmp_path / "clip.mkv"
    clip.write_bytes(b"not really a matroska")

    _mark_default_audio_track(clip, ["video", "game", "chat"],
                              _frames({"game": 10, "chat": 20}))

    assert clip.read_bytes() == b"not really a matroska"
    assert not list(tmp_path.glob("*.default.mkv")), "left a temp file behind"
