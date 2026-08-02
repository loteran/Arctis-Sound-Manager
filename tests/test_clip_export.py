# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for clip trimming, track mixing and size-targeted export.

The arithmetic here decides whether a shared clip is accepted or rejected, so
it is worth pinning exactly: audio is subtracted from the budget before video
is sized, headroom covers container overhead, and a target that cannot be met
at a watchable quality is refused rather than honoured with a smear.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from arctis_sound_manager.clip_export import (AUDIO_KBPS, MIN_VIDEO_KBPS,
                                              SIZE_HEADROOM, ExportPlan,
                                              TrackMix, build_command,
                                              video_bitrate_kbps)


# ── bitrate arithmetic ─────────────────────────────────────────────────────────

def test_bitrate_fits_inside_the_target():
    kbps = video_bitrate_kbps(target_mb=10, duration_s=30, track_count=1)

    total_kbits = kbps * 30 + AUDIO_KBPS * 30
    assert total_kbits <= 10 * 8 * 1024, "export would exceed the requested size"


def test_headroom_is_left_for_container_overhead():
    """Filling the budget exactly lands just over the limit once muxing and
    rate-control drift are added, and a clip 40 KB over 10 MB is as rejected as
    one 5 MB over."""
    kbps = video_bitrate_kbps(target_mb=10, duration_s=30)

    used = (kbps * 30 + AUDIO_KBPS * 30) / (10 * 8 * 1024)
    assert used <= SIZE_HEADROOM + 0.01


def test_audio_is_subtracted_before_video_is_sized():
    one = video_bitrate_kbps(target_mb=50, duration_s=60, track_count=1)
    three = video_bitrate_kbps(target_mb=50, duration_s=60, track_count=3)

    assert three < one
    assert one - three == pytest.approx(AUDIO_KBPS * 2, rel=0.02)


def test_longer_clip_gets_less_bitrate_for_the_same_size():
    assert (video_bitrate_kbps(target_mb=10, duration_s=60)
            < video_bitrate_kbps(target_mb=10, duration_s=30))


def test_impossible_target_is_refused():
    """Better to say it cannot be done than to return an unwatchable file."""
    with pytest.raises(ValueError, match="not enough"):
        video_bitrate_kbps(target_mb=1, duration_s=300)


def test_refusal_threshold_is_the_documented_floor():
    with pytest.raises(ValueError):
        video_bitrate_kbps(target_mb=(MIN_VIDEO_KBPS * 10) / (8 * 1024) * 0.5,
                           duration_s=10)


@pytest.mark.parametrize("duration,target", [(0, 10), (-5, 10), (30, 0), (30, -1)])
def test_nonsense_inputs_are_rejected(duration, target):
    with pytest.raises(ValueError):
        video_bitrate_kbps(target_mb=target, duration_s=duration)


# ── command construction ───────────────────────────────────────────────────────

def _plan(**kw) -> ExportPlan:
    base = dict(source=Path("/clips/in.mkv"), destination=Path("/clips/out.mp4"),
                start_s=5.0, end_s=20.0,
                tracks=[TrackMix("game"), TrackMix("mic")])
    base.update(kw)
    return ExportPlan(**base)


def test_trim_is_applied_as_seek_and_duration():
    cmd = build_command(_plan())

    assert "-ss" in cmd and cmd[cmd.index("-ss") + 1] == "5.000"
    assert "-t" in cmd and cmd[cmd.index("-t") + 1] == "15.000"


def test_seek_is_omitted_when_starting_at_zero():
    assert "-ss" not in build_command(_plan(start_s=0.0))


def test_tracks_are_mixed_with_their_gains():
    cmd = build_command(_plan(tracks=[TrackMix("game", volume=0.5),
                                      TrackMix("mic", volume=1.0)]))
    graph = cmd[cmd.index("-filter_complex") + 1]

    assert "volume=0.500" in graph
    assert "amix=inputs=2" in graph


def test_muted_track_is_left_out_of_the_mix():
    """Muting the microphone before sharing has to remove it from what other
    people hear, not just from the preview."""
    cmd = build_command(_plan(tracks=[TrackMix("game"), TrackMix("mic", muted=True)]))
    graph = cmd[cmd.index("-filter_complex") + 1]

    assert "amix=inputs=1" in graph
    assert "[0:a:1]" not in graph


def test_all_tracks_muted_produces_a_silent_clip():
    """A deliberate choice, not a mistake to correct."""
    cmd = build_command(_plan(tracks=[TrackMix("game", muted=True)]))

    assert "-an" in cmd
    assert "-filter_complex" not in cmd


def test_size_target_switches_to_a_real_encode():
    cmd = build_command(_plan(target_mb=10))

    assert "libx264" in cmd
    assert "-b:v" in cmd
    assert "copy" not in cmd


def test_no_size_target_copies_the_video_untouched():
    """Trimming alone must not re-encode — it costs time and quality for nothing."""
    cmd = build_command(_plan(target_mb=None))

    assert cmd[cmd.index("-c:v") + 1] == "copy"


def test_impossible_target_propagates_from_the_command_builder():
    with pytest.raises(ValueError):
        build_command(_plan(start_s=0, end_s=600, target_mb=1))


def test_no_tracks_at_all_is_an_error():
    with pytest.raises(ValueError, match="no audio tracks"):
        build_command(_plan(tracks=[]))
