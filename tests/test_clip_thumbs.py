# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the clip library's poster frames.

The library is a grid of cards, and a card without a picture is a filename with
extra spacing around it — so the cache has to actually hit (or the page pays an
ffmpeg run per clip, every time it is opened) and it has to miss when the file
behind it changed (or a re-encoded clip is shown by the frame of the video that
used to be there).
"""
from __future__ import annotations

import subprocess

import pytest

from arctis_sound_manager import clip_thumbs


@pytest.fixture(autouse=True)
def _private_cache(tmp_path, monkeypatch):
    """A cache of this test's own.

    cache_dir() reads $XDG_CACHE_HOME on every call precisely so this works;
    without it the tests share one directory and prune() counts each other's
    leftovers.
    """
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))


@pytest.fixture
def clip(tmp_path):
    path = tmp_path / "clip_2026-07-30_23-15-02_Half_Life.mkv"
    path.write_bytes(b"not really a video")
    return path


# ── cache identity ────────────────────────────────────────────────────────────

def test_cache_path_is_stable_for_an_unchanged_clip(clip):
    assert clip_thumbs.cache_path(clip) == clip_thumbs.cache_path(clip)


def test_cache_path_changes_when_the_clip_is_replaced(clip):
    before = clip_thumbs.cache_path(clip)
    import os
    stat = clip.stat()
    os.utime(clip, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    assert clip_thumbs.cache_path(clip) != before


def test_cache_path_differs_per_width(clip):
    assert clip_thumbs.cache_path(clip, 480) != clip_thumbs.cache_path(clip, 240)


def test_cache_path_survives_a_game_name_with_awkward_characters(tmp_path):
    """Game titles come from window titles and can hold anything; the cache
    name is a hash for exactly that reason."""
    path = tmp_path / "clip_2026-07-30_23-15-02_S.T.A.L.K.E.R. 2 — Heart.mkv"
    path.write_bytes(b"x")
    name = clip_thumbs.cache_path(path).name
    assert name.endswith(".jpg")
    assert "/" not in name and " " not in name


def test_cache_path_does_not_need_the_clip_to_exist(tmp_path):
    """Called while painting a row whose file may have just been deleted."""
    assert clip_thumbs.cache_path(tmp_path / "gone.mkv").name.endswith(".jpg")


# ── the ffmpeg command ────────────────────────────────────────────────────────

def test_command_seeks_from_the_end_not_the_start(clip, tmp_path):
    """The moment worth sharing is at the tail — a frame from the middle of a
    30 s clip is usually the 30 s of nothing that preceded it."""
    cmd = clip_thumbs.build_command(clip, tmp_path / "t.jpg")
    assert "-sseof" in cmd
    assert cmd[cmd.index("-sseof") + 1].startswith("-")


def test_command_skips_audio(clip, tmp_path):
    """These files carry one track per Sonar channel; decoding them all to make
    one still image is pure waste."""
    assert "-an" in clip_thumbs.build_command(clip, tmp_path / "t.jpg")


def test_command_asks_for_exactly_one_frame(clip, tmp_path):
    cmd = clip_thumbs.build_command(clip, tmp_path / "t.jpg")
    assert cmd[cmd.index("-frames:v") + 1] == "1"


def test_command_scales_to_an_even_height(clip, tmp_path):
    """-2 keeps 4:2:0 encodable whatever the captured screen's aspect was."""
    cmd = clip_thumbs.build_command(clip, tmp_path / "t.jpg", width=480)
    assert cmd[cmd.index("-vf") + 1].startswith("scale=480:-2")


def test_command_converts_to_full_range_yuv(clip, tmp_path):
    """The capture writes limited-range YUV and the JPEG encoder refuses it
    ("Non full-range YUV is non-standard"). Without the conversion every card
    on a real library falls back to the placeholder."""
    cmd = clip_thumbs.build_command(clip, tmp_path / "t.jpg")
    assert "format=yuvj420p" in cmd[cmd.index("-vf") + 1]


def test_zero_offset_means_no_seek_at_all(clip, tmp_path):
    """The fallback for a clip whose tail cannot be seeked into."""
    cmd = clip_thumbs.build_command(clip, tmp_path / "t.jpg", offset_s=0.0)
    assert "-sseof" not in cmd


def test_command_writes_to_the_requested_destination(clip, tmp_path):
    dest = tmp_path / "poster.jpg"
    assert clip_thumbs.build_command(clip, dest)[-1] == str(dest)


# ── generation ────────────────────────────────────────────────────────────────

def test_cached_frame_is_returned_without_running_ffmpeg(clip, monkeypatch):
    dest = clip_thumbs.cache_path(clip)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"jpeg")

    def explode(*a, **kw):                     # pragma: no cover - must not run
        raise AssertionError("cache hit still spawned ffmpeg")

    monkeypatch.setattr(subprocess, "run", explode)
    assert clip_thumbs.thumbnail(clip) == dest


def test_empty_cache_file_is_not_treated_as_a_hit(clip, monkeypatch):
    """A run killed mid-write leaves a zero-byte file; showing it forever is
    worse than paying for one more ffmpeg."""
    dest = clip_thumbs.cache_path(clip)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"")
    monkeypatch.setattr(clip_thumbs.shutil, "which", lambda name: "/usr/bin/ffmpeg")

    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        dest.write_bytes(b"jpeg")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert clip_thumbs.thumbnail(clip) == dest
    assert calls


def test_missing_ffmpeg_is_not_an_error(clip, monkeypatch):
    """The page is drawing when it calls this; a missing picture is cosmetic."""
    monkeypatch.setattr(clip_thumbs.shutil, "which", lambda name: None)
    assert clip_thumbs.thumbnail(clip) is None


def test_unseekable_tail_falls_back_to_the_first_frame(clip, monkeypatch):
    """A clip whose mux did not finish cleanly has a short index: ffmpeg lands
    past the last frame, writes nothing and still exits 0. Those clips are the
    ones a user most wants to recognise in the grid, so a second run without
    the seek is what stands between them and a blank card."""
    monkeypatch.setattr(clip_thumbs.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    dest = clip_thumbs.cache_path(clip)
    seen = []

    def fake_run(cmd, **kw):
        seen.append(cmd)
        if "-sseof" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "", "")   # nothing written
        dest.write_bytes(b"jpeg")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert clip_thumbs.thumbnail(clip) == dest
    assert len(seen) == 2 and "-sseof" not in seen[1]


def test_a_frame_from_the_tail_stops_there(clip, monkeypatch):
    """The fallback is a fallback: a clip that gave up its tail frame must not
    pay for a second decode from the start."""
    monkeypatch.setattr(clip_thumbs.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    dest = clip_thumbs.cache_path(clip)
    runs = []

    def fake_run(cmd, **kw):
        runs.append(cmd)
        dest.write_bytes(b"jpeg")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    clip_thumbs.thumbnail(clip)
    assert len(runs) == 1


def test_failed_extraction_leaves_no_cache_entry(clip, monkeypatch):
    """Otherwise the failure is cached and the card can never recover."""
    monkeypatch.setattr(clip_thumbs.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    dest = clip_thumbs.cache_path(clip)

    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, "", "moov atom not found")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert clip_thumbs.thumbnail(clip) is None
    assert not dest.exists()


def test_timeout_is_not_raised_at_the_caller(clip, monkeypatch):
    monkeypatch.setattr(clip_thumbs.shutil, "which", lambda name: "/usr/bin/ffmpeg")

    def fake_run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 20)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert clip_thumbs.thumbnail(clip) is None


def test_deleted_clip_is_not_handed_to_ffmpeg(tmp_path, monkeypatch):
    def explode(*a, **kw):                     # pragma: no cover - must not run
        raise AssertionError("ran ffmpeg on a file that is not there")

    monkeypatch.setattr(subprocess, "run", explode)
    assert clip_thumbs.thumbnail(tmp_path / "gone.mkv") is None


# ── cache upkeep ──────────────────────────────────────────────────────────────

def test_prune_keeps_frames_of_current_clips_and_drops_the_rest(clip):
    keep = clip_thumbs.cache_path(clip)
    keep.parent.mkdir(parents=True, exist_ok=True)
    keep.write_bytes(b"jpeg")
    stale = keep.parent / "deadbeef.jpg"
    stale.write_bytes(b"jpeg")

    assert clip_thumbs.prune([clip]) == 1
    assert keep.exists()
    assert not stale.exists()


def test_prune_on_a_cold_cache_is_a_no_op(clip, monkeypatch, tmp_path):
    monkeypatch.setattr(clip_thumbs, "cache_dir", lambda: tmp_path / "nothing-here")
    assert clip_thumbs.prune([clip]) == 0
