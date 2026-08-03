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


# ── frame rate ─────────────────────────────────────────────────────────────────
#
# Capture cannot fix a rate: the screencast produces a frame when the screen
# changes and nothing when it does not, so a recording holds whatever it managed
# — which is why a status bar reading 20 fps can save a 12 fps clip. Export is
# where a constant rate can actually be produced, so that is where it is offered.

def test_a_chosen_rate_reaches_ffmpeg():
    cmd = build_command(_plan(target_mb=None, fps=30))

    assert "-r" in cmd and cmd[cmd.index("-r") + 1] == "30"


def test_no_rate_chosen_leaves_the_timing_alone():
    assert "-r" not in build_command(_plan(target_mb=None, fps=None))


def test_fixing_the_rate_re_encodes_rather_than_copying():
    """Measured against ffmpeg: `-c:v copy -r 30` on a 2 s / 20 frame clip wrote
    20 frames under a 30 fps header, so it played at triple speed. The frames
    have to be produced, which a stream copy cannot do."""
    cmd = build_command(_plan(target_mb=None, fps=60))

    assert "copy" not in cmd
    assert cmd[cmd.index("-c:v") + 1] == "libx264"


def test_a_rate_and_a_size_target_still_hit_the_size():
    """The size encode owns the bitrate; the rate only adds -r on top of it."""
    cmd = build_command(_plan(target_mb=10, fps=30))

    assert "-b:v" in cmd
    assert cmd[cmd.index("-r") + 1] == "30"
    assert cmd.count("-c:v") == 1


# ── the container an export lands in ───────────────────────────────────────────
#
# Reported from use: "Discord will not take the mkv". Recordings have to be
# Matroska — it is the container that holds a track per Sonar channel — but
# nothing an export is *for* can read one. Discord uploads a .mkv and then
# cannot play it, which for a share feature is the same as failing.

def test_every_export_lands_as_mp4():
    from arctis_sound_manager.clip_export import SHARE_SUFFIX

    assert SHARE_SUFFIX == ".mp4"


def test_a_real_export_produces_something_discord_can_play(tmp_path):
    """H.264 in MP4 with a single AAC track, from a Matroska with Opus tracks —
    end to end through the ffmpeg that is actually installed, because this is a
    claim about container and codec support, not about arithmetic."""
    import shutil as _shutil
    import subprocess as _sp

    if _shutil.which("ffmpeg") is None or _shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg not installed")

    from arctis_sound_manager.clip_export import export

    source = tmp_path / "clip.mkv"
    _sp.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "testsrc=d=3:r=15:s=320x240",
         "-f", "lavfi", "-i", "sine=f=440:r=48000",
         "-f", "lavfi", "-i", "sine=f=880:r=48000",
         "-t", "3", "-map", "0:v", "-map", "1:a", "-map", "2:a",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "libopus",
         str(source)],
        check=True, capture_output=True, timeout=60)

    destination = tmp_path / "clip_share.mp4"
    written = export(ExportPlan(source=source, destination=destination,
                                start_s=0.0, end_s=3.0,
                                tracks=[TrackMix("game"), TrackMix("chat")]))

    assert written == destination and destination.stat().st_size > 0
    probe = _sp.run(["ffprobe", "-v", "error", "-show_entries",
                     "stream=codec_name,codec_type", "-of", "csv=p=0",
                     str(destination)],
                    capture_output=True, text=True, timeout=30)
    streams = [line for line in probe.stdout.splitlines() if line]
    assert "h264,video" in streams
    # One audio stream, mixed: several tracks is what stops a browser or Discord
    # playing anything but the first.
    assert [s for s in streams if s.endswith("audio")] == ["aac,audio"]


# ── which channels actually hold anything ──────────────────────────────────────
#
# Reported from use: "there is no sound". The clip had four channels and every
# player, Qt's included, decodes exactly one and picks the first — so an empty
# game channel plays back as a clip with no audio at all, while the chat track
# next to it is fine. Being able to say which channels are empty is what turns
# that into a routing problem instead of a broken feature.

def _volumedetect_output(db: float) -> str:
    return (f"[Parsed_volumedetect_0 @ 0x0] mean_volume: {db - 10:.1f} dB\n"
            f"[Parsed_volumedetect_0 @ 0x0] max_volume: {db:.1f} dB\n")


def _fake_ffmpeg(monkeypatch, stderr: str):
    import subprocess as sp
    from types import SimpleNamespace

    from arctis_sound_manager import clip_export

    monkeypatch.setattr(clip_export.shutil, "which", lambda n: f"/usr/bin/{n}")
    monkeypatch.setattr(sp, "run",
                        lambda *a, **kw: SimpleNamespace(returncode=0, stdout="",
                                                         stderr=stderr))


def test_a_peak_is_read_off_the_log_not_the_output(monkeypatch):
    """volumedetect reports on stderr as a log line; nothing lands on stdout."""
    from arctis_sound_manager.clip_export import track_peak_db

    _fake_ffmpeg(monkeypatch, _volumedetect_output(-12.3))
    assert track_peak_db(Path("/clips/in.mkv"), 0) == pytest.approx(-12.3)


def test_digital_silence_is_reported_as_silent(monkeypatch):
    from arctis_sound_manager.clip_export import silent_tracks

    _fake_ffmpeg(monkeypatch, _volumedetect_output(-91.0))
    assert silent_tracks(Path("/clips/in.mkv"), 2) == [True, True]


def test_quiet_is_not_silent(monkeypatch):
    """A channel someone was talking quietly on must not be labelled empty."""
    from arctis_sound_manager.clip_export import silent_tracks

    _fake_ffmpeg(monkeypatch, _volumedetect_output(-40.0))
    assert silent_tracks(Path("/clips/in.mkv"), 1) == [False]


def test_an_unreadable_track_is_not_called_silent(monkeypatch):
    """A missing ffmpeg or an unparseable log must not put a "silent" label on
    audio that is really there — the label would send the user to fix routing
    that was never broken."""
    from arctis_sound_manager.clip_export import silent_tracks, track_peak_db

    _fake_ffmpeg(monkeypatch, "no volume information here")
    assert track_peak_db(Path("/clips/in.mkv"), 0) is None
    assert silent_tracks(Path("/clips/in.mkv"), 2) == [False, False]


def test_channels_split_into_one_file_each(tmp_path):
    """What makes the editor's mixer possible: a player can only decode one
    audio track, so each channel is given a file of its own and a player each.
    Stream-copied, so this is a demux — it must not re-encode."""
    import shutil as _shutil
    import subprocess as _sp

    if _shutil.which("ffmpeg") is None or _shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg not installed")

    from arctis_sound_manager.clip_export import split_tracks

    source = tmp_path / "clip.mkv"
    _sp.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "testsrc=d=2:r=10:s=160x120",
         "-f", "lavfi", "-i", "sine=f=440:r=48000",
         "-f", "lavfi", "-i", "sine=f=880:r=48000",
         "-t", "2", "-map", "0:v", "-map", "1:a", "-map", "2:a",
         "-c:v", "libx264", "-c:a", "libopus", str(source)],
        check=True, capture_output=True, timeout=60)

    out = tmp_path / "split"
    out.mkdir()
    files = split_tracks(source, 2, out)

    assert len(files) == 2 and all(f.stat().st_size > 0 for f in files)
    for path in files:
        probe = _sp.run(["ffprobe", "-v", "error", "-show_entries",
                         "stream=codec_name,codec_type", "-of", "csv=p=0",
                         str(path)], capture_output=True, text=True, timeout=30)
        # One audio stream, still Opus — a transcode here would mean the split
        # costs a full decode of every channel every time a clip is opened.
        assert [s for s in probe.stdout.splitlines() if s] == ["opus,audio"]


def test_a_partial_split_is_reported_as_no_split(tmp_path):
    """Asking for more channels than the clip has must not hand back a short
    list — the editor would map row 3's slider onto row 2's audio."""
    import shutil as _shutil
    import subprocess as _sp

    if _shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")

    from arctis_sound_manager.clip_export import split_tracks

    source = tmp_path / "one.mkv"
    _sp.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "testsrc=d=1:r=10:s=160x120",
         "-f", "lavfi", "-i", "sine=f=440:r=48000", "-t", "1",
         "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-c:a", "libopus",
         str(source)],
        check=True, capture_output=True, timeout=60)

    out = tmp_path / "split"
    out.mkdir()
    assert split_tracks(source, 3, out) == []


def test_no_ffmpeg_is_not_an_error(monkeypatch):
    from arctis_sound_manager import clip_export

    monkeypatch.setattr(clip_export.shutil, "which", lambda n: None)
    assert clip_export.track_peak_db(Path("/clips/in.mkv"), 0) is None


def test_silence_is_measured_against_a_real_file(tmp_path):
    """The parsing above is mocked; this is the part that has to agree with the
    ffmpeg actually installed — the units, the flag names and the track order."""
    import shutil as _shutil
    import subprocess as _sp

    if _shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")

    from arctis_sound_manager.clip_export import silent_tracks

    clip = tmp_path / "two_tracks.mkv"
    _sp.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "testsrc=d=1:r=10:s=160x120",
         "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
         "-f", "lavfi", "-i", "sine=f=440:r=48000",
         "-t", "1", "-map", "0:v", "-map", "1:a", "-map", "2:a",
         "-c:v", "libx264", "-c:a", "libopus", str(clip)],
        check=True, capture_output=True, timeout=60)

    assert silent_tracks(clip, 2) == [True, False]


def test_the_offered_rates_are_the_ones_capture_offers():
    """Two lists that mean the same thing to a user, in two modules."""
    from arctis_sound_manager.clip_capture import FPS_CHOICES as CAPTURE_CHOICES
    from arctis_sound_manager.clip_export import FPS_CHOICES as EXPORT_CHOICES

    assert EXPORT_CHOICES == CAPTURE_CHOICES
