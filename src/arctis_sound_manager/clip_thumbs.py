# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Poster frames for the clip library.

A row of filenames tells you nothing about which clip is the one you meant to
share — the names differ only by a timestamp, and the only way to tell them
apart is to open each in turn. A frame from the clip identifies it at a glance,
which is the whole reason the library is a grid of cards rather than a list.

The frame is taken from *near the end* rather than the middle: the buffer ends
at the moment the shortcut was pressed, so the thing worth keeping is at the
tail — the same reasoning that makes the editor open on the last seconds
(:data:`gui.trim_band.DEFAULT_TAIL_S`). A poster frame from the middle of a
30-second clip usually shows the thirty seconds of nothing that preceded it.

Frames are cached on disk, keyed by the clip's identity *and* its mtime, so a
library of fifty clips costs fifty ffmpeg runs once rather than on every visit,
and a file replaced in place is not shown by its stale frame forever.

No Qt here: the cache layout and the command line are decidable without a
display, and this module is what the tests exercise.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

# Wide enough to stay sharp on a HiDPI card without storing a second copy of
# the video: the grid draws these at roughly half this size.
THUMB_WIDTH = 480

# How far back from the end the frame is taken. Inside the span the editor
# opens with selected, so the card shows what an immediate Export would send.
TAIL_OFFSET_S = 5.0

# ffmpeg gets a short leash: a poster frame is a nicety, and a clip whose
# header is damaged must not hang the library behind a stuck decoder.
TIMEOUT_S = 20.0


def cache_dir() -> Path:
    """Where poster frames live.

    Resolved per call rather than at import: the cache root is a user path, and
    a module-level constant would freeze whatever ``$XDG_CACHE_HOME`` happened
    to be when this module was first imported.
    """
    root = os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")
    return Path(root) / "arctis-sound-manager" / "clip-thumbs"


def cache_path(clip: Path, width: int = THUMB_WIDTH) -> Path:
    """Cache file for *clip*'s poster frame at *width*.

    The name carries the clip's mtime, so replacing a file in place produces a
    different cache entry instead of quietly reusing a frame from the video
    that used to be there. The path is hashed rather than embedded: clip names
    hold game titles, which hold spaces, slashes and anything else a window
    title can contain.
    """
    try:
        stamp = clip.stat().st_mtime_ns
    except OSError:
        stamp = 0
    key = f"{clip.resolve()}|{stamp}|{width}"
    digest = hashlib.sha256(key.encode("utf-8", "surrogateescape")).hexdigest()[:32]
    return cache_dir() / f"{digest}.jpg"


def build_command(clip: Path, dest: Path, width: int = THUMB_WIDTH,
                  offset_s: float = TAIL_OFFSET_S) -> list[str]:
    """The ffmpeg invocation that writes *clip*'s poster frame to *dest*.

    ``-sseof`` seeks relative to the end of the file, which is what makes this
    one command rather than two: asking for "5 seconds before the end" needs no
    ffprobe pass for the duration. An *offset_s* of zero or less means "no
    seek" — the first decodable frame — which is the fallback for clips whose
    tail cannot be seeked into (see :func:`thumbnail`).

    ``-an`` matters more than it looks: these files carry one audio track per
    Sonar channel, and without it ffmpeg spends the run decoding all of them to
    produce a single still image.

    ``format=yuvj420p`` is not cosmetic. The capture encodes limited-range
    YUV, and the JPEG encoder refuses it outright ("Non full-range YUV is
    non-standard") — without the conversion every card on this machine falls
    back to the placeholder.
    """
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
    if offset_s > 0:
        cmd += ["-sseof", f"-{offset_s:.3f}"]
    cmd += [
        "-i", str(clip),
        "-an",
        "-frames:v", "1",
        # -2 keeps the height even, which 4:2:0 requires, and preserves the
        # aspect ratio whatever the captured screen was.
        "-vf", f"scale={int(width)}:-2,format=yuvj420p",
        "-q:v", "4",
        str(dest),
    ]
    return cmd


def thumbnail(clip: Path, width: int = THUMB_WIDTH,
              timeout: float = TIMEOUT_S) -> Path | None:
    """Poster frame for *clip*, generating it if the cache has none.

    Returns None when no frame could be made — no ffmpeg, an unreadable clip, a
    decode that timed out. The library shows its placeholder in that case; a
    missing picture is a cosmetic loss and must never be raised at the caller,
    which is drawing a page.
    """
    dest = cache_path(clip, width)
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    if not clip.exists():
        return None
    if shutil.which("ffmpeg") is None:
        log.debug("no ffmpeg — clip cards will use the placeholder")
        return None

    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log.debug("cannot create the thumbnail cache: %s", exc)
        return None

    # Preferred frame first, then the first frame of the clip. The tail seek
    # comes back empty on clips the mux did not finish cleanly — the index is
    # short, ffmpeg lands past the last frame and exits 0 having written
    # nothing — and those are still clips the user wants to recognise.
    for offset in (TAIL_OFFSET_S, 0.0):
        if _extract(clip, dest, width, offset, timeout):
            return dest
    log.debug("no frame could be extracted from %s", clip.name)
    return None


def _extract(clip: Path, dest: Path, width: int, offset_s: float,
             timeout: float) -> bool:
    """One ffmpeg run. True when it actually produced a frame.

    The return code is not the test: ffmpeg exits 0 after encoding nothing when
    a seek lands past the end, so the file it was asked for is what decides.
    """
    try:
        result = subprocess.run(build_command(clip, dest, width, offset_s),
                                capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.debug("thumbnail for %s failed to run: %s", clip.name, exc)
        dest.unlink(missing_ok=True)
        return False

    if result.returncode != 0 or not dest.exists() or dest.stat().st_size == 0:
        log.debug("no frame from %s at offset %.1fs: %s", clip.name, offset_s,
                  (result.stderr or "").strip()[:200])
        dest.unlink(missing_ok=True)
        return False
    return True


def prune(current: list[Path], width: int = THUMB_WIDTH) -> int:
    """Delete cached frames that no longer belong to any clip in *current*.

    Without this the cache grows by one file per clip *and* one per edit, since
    an mtime change makes a new key rather than replacing the old one. Returns
    how many were removed.
    """
    directory = cache_dir()
    if not directory.exists():
        return 0
    keep = {cache_path(clip, width).name for clip in current}
    removed = 0
    for entry in directory.glob("*.jpg"):
        if entry.name not in keep:
            try:
                entry.unlink()
                removed += 1
            except OSError:
                pass
    return removed
