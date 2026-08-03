# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""What the clip library holds, where exports go, and how clips are removed.

Three things live here rather than in the Qt page, because each of them is a
decision that can be got wrong invisibly:

*Exports are not clips.* An export is a copy of a clip, trimmed and re-encoded
for somewhere with a size limit. Left beside the original it came back as a
second card in the library — and exporting that produced ``_share_share``. They
go to a subfolder instead, so the library keeps showing recordings and a clip
stays one item however many times it is shared.

*A trim is worth keeping.* Reopening a clip after trimming it used to start
again from the default tail, throwing away the choice that had just been made.
The span is written beside the clip and restored when it is opened.

*Deleting is destructive.* The system trash is tried first, so a mis-click is
recoverable; only when no trash is available does this unlink. Either way the
clip's sidecars go with it rather than accumulating forever.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

CLIP_DIR = Path.home() / "Videos" / "ASM Clips"

# Exports live under the library rather than beside the recordings.
SHARE_DIR_NAME = "Shared"

# Extensions the library treats as a recording. Exports may be .mp4; the
# library never lists them, so this is about what the capture writes.
CLIP_SUFFIXES = (".mkv",)

# Written next to a clip by the editor.
TRIM_SUFFIX = ".trim.json"

# Per-channel levels, also written by the editor. Kept for the same reason the
# trim is: deciding that the microphone is too loud in a clip and that the chat
# channel should be off is work, and closing the editor used to throw all of it
# away. It is stored per clip rather than as a global default because it is a
# judgement about *this* recording — the mic was hot in this one, not always.
MIX_SUFFIX = ".mix.json"

# Written next to a clip by the capture (channel names per audio track).
_SIDECAR_SUFFIXES = (TRIM_SUFFIX, MIX_SUFFIX, ".tracks.json")


def clip_dir() -> Path:
    return CLIP_DIR


def share_dir() -> Path:
    """Where exports are written."""
    return CLIP_DIR / SHARE_DIR_NAME


def is_export(path: Path) -> bool:
    """True for a file this app produced by exporting a clip.

    Only needed for exports written before they moved to ``Shared/`` — they sit
    beside the recordings and would otherwise keep showing up as clips of their
    own, which is the complaint this whole arrangement exists to answer.
    """
    stem = path.stem
    if stem.endswith("_share"):
        return True
    head, sep, tail = stem.rpartition("_share-")
    return bool(sep) and tail.isdigit()


def list_clips(directory: Path | None = None) -> list[Path]:
    """Recordings in *directory*, newest first.

    Non-recursive on purpose: it is what keeps today's exports, which live in
    ``Shared/``, out of the library without having to recognise them by name.
    Older ones did land here, so those are filtered by name as well.
    """
    root = directory or clip_dir()
    if not root.exists():
        return []
    clips = [p for p in root.iterdir()
             if p.is_file() and p.suffix.lower() in CLIP_SUFFIXES
             and not is_export(p)]
    return sorted(clips, key=lambda p: p.stat().st_mtime, reverse=True)


def export_destination(source: Path, suffix: str = ".mp4",
                       directory: Path | None = None) -> Path:
    """Where an export of *source* should be written.

    Named after the clip, not after the previous export: exporting twice used
    to give ``clip_..._share_share.mkv`` because the editor had been reopened
    on its own output. A second export of the same clip is numbered instead,
    so neither copy silently overwrites the other.
    """
    root = directory if directory is not None else share_dir()
    stem = source.stem
    candidate = root / f"{stem}_share{suffix}"
    index = 2
    while candidate.exists():
        candidate = root / f"{stem}_share-{index}{suffix}"
        index += 1
    return candidate


def sidecars(clip: Path) -> list[Path]:
    """Every file that belongs to *clip* rather than standing on its own.

    One list, so a clip that is deleted or renamed takes its track names and
    its remembered trim with it instead of leaving them to rot beside a file
    that no longer exists.
    """
    return [clip.with_suffix(suffix) for suffix in _SIDECAR_SUFFIXES]


# ── remembered trim ───────────────────────────────────────────────────────────

def trim_sidecar(clip: Path) -> Path:
    """``clip_….mkv`` → ``clip_….trim.json``.

    Built with ``with_suffix`` to match the convention the capture and the
    exporter already use for ``.tracks.json``: one rule for sidecars means
    deleting or renaming a clip finds all of them.
    """
    return clip.with_suffix(TRIM_SUFFIX)


def read_trim(clip: Path) -> tuple[float, float] | None:
    """The span last chosen for *clip*, or None when there is none to restore.

    Anything unreadable, malformed or nonsensical (reversed, negative) is
    treated as absent: the editor then opens on its default tail, which is
    always a usable answer.
    """
    try:
        raw = json.loads(trim_sidecar(clip).read_text())
        start = float(raw["start_s"])
        end = float(raw["end_s"])
    except (OSError, ValueError, TypeError, KeyError):
        return None
    if start < 0 or end <= start:
        return None
    return start, end


def write_trim(clip: Path, start_s: float, end_s: float) -> bool:
    """Remember *clip*'s span. False when it could not be written.

    A failure here costs the user nothing but the memory of one trim, so it is
    logged and swallowed rather than raised into an export they are waiting on.
    """
    if end_s <= start_s:
        return False
    try:
        trim_sidecar(clip).write_text(json.dumps(
            {"start_s": round(float(start_s), 3), "end_s": round(float(end_s), 3)}))
        return True
    except OSError as exc:
        log.debug("could not remember the trim for %s: %s", clip.name, exc)
        return False


# ── remembered channel levels ─────────────────────────────────────────────────

def mix_sidecar(clip: Path) -> Path:
    return clip.with_suffix(MIX_SUFFIX)


def read_mix(clip: Path) -> dict[str, tuple[float, bool]]:
    """Per-channel ``{name: (volume, muted)}`` last chosen for *clip*.

    Keyed by channel name rather than by position: a clip's track order is
    fixed, but reading it back by index would silently apply the microphone's
    settings to the game channel if the sidecar were ever written by a
    different version. An unreadable or malformed file is treated as absent,
    which leaves every channel at its default and is always a usable answer.
    """
    try:
        raw = json.loads(mix_sidecar(clip).read_text())
    except (OSError, ValueError):
        return {}
    out: dict[str, tuple[float, bool]] = {}
    for name, entry in (raw.get("tracks") or {}).items():
        try:
            volume = float(entry["volume"])
            muted = bool(entry["muted"])
        except (TypeError, ValueError, KeyError):
            continue
        out[str(name)] = (min(max(volume, 0.0), 1.5), muted)
    return out


def write_mix(clip: Path, mix: dict[str, tuple[float, bool]]) -> bool:
    """Remember *clip*'s channel levels. False when it could not be written."""
    try:
        mix_sidecar(clip).write_text(json.dumps({"tracks": {
            name: {"volume": round(float(volume), 3), "muted": bool(muted)}
            for name, (volume, muted) in mix.items()
        }}, indent=2))
        return True
    except (OSError, TypeError, ValueError) as exc:
        log.debug("could not remember the mix for %s: %s", clip.name, exc)
        return False


# ── removal ───────────────────────────────────────────────────────────────────

def delete_clip(clip: Path) -> bool:
    """Remove *clip* and its sidecars. True when the clip itself is gone.

    The system trash is preferred: this is a one-click action on a grid where
    the wrong card is easy to hit, and a recording that took a game session to
    produce should not be unrecoverable because of a mis-click. ``gio trash``
    is what puts a file where the desktop's own "restore" can find it.
    """
    ok = _trash(clip)
    if not ok:
        try:
            clip.unlink()
            ok = True
        except OSError as exc:
            log.warning("could not delete %s: %s", clip.name, exc)
            return False

    for sidecar in sidecars(clip):
        if sidecar.exists() and not _trash(sidecar):
            try:
                sidecar.unlink()
            except OSError:
                pass
    return ok


def delete_clips(clips: list[Path]) -> tuple[int, list[Path]]:
    """Delete several. Returns (how many went, which ones refused).

    One failure does not stop the rest: a locked or already-removed file in a
    multi-selection must not leave half the request unperformed and unreported.
    """
    gone, failed = 0, []
    for clip in clips:
        if delete_clip(clip):
            gone += 1
        else:
            failed.append(clip)
    return gone, failed


def _trash(path: Path) -> bool:
    """Move *path* to the desktop trash. False when that is not possible."""
    gio = shutil.which("gio")
    if gio is None:
        return False
    try:
        result = subprocess.run([gio, "trash", "--", str(path)],
                                capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.debug("gio trash failed to run for %s: %s", path.name, exc)
        return False
    if result.returncode != 0:
        log.debug("gio trash refused %s: %s", path.name,
                  (result.stderr or "").strip()[:200])
        return False
    return True
