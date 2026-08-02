# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Trim a clip, mix its tracks down, and hit a target file size.

Sharing is where a clip is actually used, and every place worth sharing it has
a size limit — so "make this fit in 10 MB" is the operation, not "encode at
some bitrate". The bitrate is therefore derived from the target and the trimmed
duration rather than chosen: overshoot means the upload is rejected, and
undershoot throws away quality for nothing.

Pure computation and an ffmpeg command line; no Qt, so the arithmetic that
decides quality can be tested without a display or a GPU.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# Audio is encoded at a fixed rate and subtracted from the budget before video
# gets what is left — sizing video first and hoping the rest fits is what makes
# an export land just over the limit.
AUDIO_KBPS = 128

# Room for container overhead and rate-control drift. Muxing, keyframes and the
# encoder's own tolerance all push the real file slightly past the arithmetic,
# and a clip that misses a 10 MB limit by 40 KB is as rejected as one that
# misses it by 5 MB.
SIZE_HEADROOM = 0.94

# Below this a clip is unwatchable, and a size target that demands less is
# better refused than silently honoured with a smear.
MIN_VIDEO_KBPS = 300


@dataclass
class TrackMix:
    """How one audio track should be treated in the export."""

    name: str
    volume: float = 1.0
    muted: bool = False

    @property
    def gain(self) -> float:
        return 0.0 if self.muted else max(0.0, self.volume)


@dataclass
class ExportPlan:
    """Everything the export needs, resolved from the user's choices."""

    source: Path
    destination: Path
    start_s: float = 0.0
    end_s: float = 0.0
    target_mb: float | None = None
    tracks: list[TrackMix] = field(default_factory=list)

    @property
    def duration_s(self) -> float:
        return max(0.0, self.end_s - self.start_s)


def video_bitrate_kbps(target_mb: float, duration_s: float,
                       track_count: int = 1) -> int:
    """Bitrate that lands *duration_s* of video inside *target_mb*.

    Raises ValueError when the target cannot be met at a watchable quality —
    the honest answer, since the alternative is handing back a file that either
    misses the limit or is too degraded to be worth sharing.
    """
    if duration_s <= 0:
        raise ValueError("clip has no duration")
    if target_mb <= 0:
        raise ValueError("target size must be positive")

    total_kbits = target_mb * 8 * 1024 * SIZE_HEADROOM
    audio_kbits = AUDIO_KBPS * duration_s * max(1, track_count)
    video_kbps = int((total_kbits - audio_kbits) / duration_s)

    if video_kbps < MIN_VIDEO_KBPS:
        raise ValueError(
            f"{target_mb:g} MB is not enough for {duration_s:.0f}s "
            f"({video_kbps} kbit/s of video) — trim it shorter or pick a "
            f"larger size")
    return video_kbps


def build_command(plan: ExportPlan) -> list[str]:
    """The ffmpeg invocation for *plan*.

    Tracks are mixed to stereo here rather than kept separate: the clip is
    being shared, and a multi-track file plays back as the first track alone in
    most players and web uploads. The per-track gains are what the editor's
    sliders set, so muting the microphone before sharing actually removes it
    from what other people hear.
    """
    if not plan.tracks:
        raise ValueError("no audio tracks selected")

    audible = [t for t in plan.tracks if t.gain > 0]
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"

    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
    # Seeking before -i is the fast path (keyframe granularity); the clip was
    # encoded with a keyframe every second, so this is accurate enough and far
    # quicker than decoding from the start.
    if plan.start_s > 0:
        cmd += ["-ss", f"{plan.start_s:.3f}"]
    cmd += ["-i", str(plan.source)]
    if plan.duration_s > 0:
        cmd += ["-t", f"{plan.duration_s:.3f}"]

    if audible:
        parts = [f"[0:a:{i}]volume={t.gain:.3f}[a{i}]"
                 for i, t in enumerate(plan.tracks) if t.gain > 0]
        inputs = "".join(f"[a{i}]" for i, t in enumerate(plan.tracks) if t.gain > 0)
        parts.append(f"{inputs}amix=inputs={len(audible)}:normalize=0[aout]")
        cmd += ["-filter_complex", ";".join(parts), "-map", "0:v:0", "-map", "[aout]"]
    else:
        # Every track muted is a deliberate choice: ship a silent clip rather
        # than quietly restoring audio the user turned off.
        cmd += ["-map", "0:v:0", "-an"]

    if plan.target_mb:
        kbps = video_bitrate_kbps(plan.target_mb, plan.duration_s, len(audible) or 1)
        cmd += ["-c:v", "libx264", "-preset", "medium", "-b:v", f"{kbps}k",
                "-maxrate", f"{int(kbps * 1.2)}k", "-bufsize", f"{kbps * 2}k"]
    else:
        cmd += ["-c:v", "copy"]

    if audible:
        cmd += ["-c:a", "aac", "-b:a", f"{AUDIO_KBPS}k"]
    cmd += ["-movflags", "+faststart", str(plan.destination)]
    return cmd


def export(plan: ExportPlan, timeout: float = 300.0) -> Path | None:
    """Run the export. Returns the file written, or None on failure."""
    try:
        cmd = build_command(plan)
    except ValueError as exc:
        log.warning("cannot export: %s", exc)
        raise

    log.info("exporting %s → %s", plan.source.name, plan.destination.name)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.error("export failed to run: %s", exc)
        return None

    if result.returncode != 0:
        log.error("ffmpeg failed: %s", (result.stderr or "").strip()[:400])
        plan.destination.unlink(missing_ok=True)
        return None
    if not plan.destination.exists() or plan.destination.stat().st_size == 0:
        log.error("export produced no file")
        plan.destination.unlink(missing_ok=True)
        return None
    return plan.destination


def probe_tracks(path: Path) -> list[str]:
    """Names of the audio tracks in *path*, in order.

    Falls back to positional labels when ffprobe is unavailable or the file
    carries no titles — the editor still needs one row per track.
    """
    # ASM writes the real channel names beside the clip when it saves one;
    # the container itself has no titles, so ffprobe alone can only ever answer
    # "Audio" for every track.
    sidecar = path.with_suffix(".tracks.json")
    try:
        import json
        names = json.loads(sidecar.read_text()).get("tracks")
        if isinstance(names, list) and names:
            return [str(n) for n in names if n != "video"]
    except (OSError, ValueError):
        pass

    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        return []
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=index:stream_tags=title",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []

    names: list[str] = []
    for n, line in enumerate(result.stdout.splitlines()):
        title = line.split(",", 1)[1].strip() if "," in line else ""
        names.append(title or f"Track {n + 1}")
    return names


def duration_s(path: Path) -> float:
    """Length of *path* in seconds, or 0.0 when it cannot be read."""
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        return 0.0
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=15)
        return float((result.stdout or "0").strip() or 0.0)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return 0.0
