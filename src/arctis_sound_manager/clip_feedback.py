# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tell the user a clip was taken, without them having to look.

A global shortcut is pressed while something else has the screen — that is the
entire point of it. Saving in silence means the only way to find out whether it
worked is to alt-tab to the app and look at a status line, by which time the
moment being clipped is over. Every recorder that people trust makes a sound.

So: a shutter sound and a desktop notification, both fire-and-forget. Neither is
allowed to fail loudly — this runs on the path of a save that has already
succeeded, and an unavailable sound player is not a reason to report failure for
a clip that is sitting on disk.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

# The freedesktop sound theme's own shutter sample. Present wherever
# sound-theme-freedesktop is, which is a dependency of every desktop this app
# targets; when it is not, the players below simply find nothing to play.
SHUTTER_SOUND_ID = "camera-shutter"
_SOUND_PATHS = (
    Path("/usr/share/sounds/freedesktop/stereo/camera-shutter.oga"),
    Path("/usr/share/sounds/freedesktop/stereo/complete.oga"),
)

# Shown by the notification, and used to group ASM's notifications together.
APP_NAME = "Arctis Sound Manager"
ICON = "arctis-manager"


def sound_command() -> list[str] | None:
    """How to play the shutter on this machine, or None when nothing can.

    canberra plays the *theme's* sound, which is the one the user has chosen
    and the one other apps use for the same event; the file players are the
    fallback for desktops without libcanberra.
    """
    canberra = shutil.which("canberra-gtk-play")
    if canberra:
        return [canberra, "-i", SHUTTER_SOUND_ID]

    for player in ("pw-play", "paplay"):
        binary = shutil.which(player)
        if binary is None:
            continue
        for sound in _SOUND_PATHS:
            if sound.exists():
                return [binary, str(sound)]
    return None


def notify_command(summary: str, body: str = "") -> list[str] | None:
    """The notification to post, or None when the desktop offers no way to."""
    notify = shutil.which("notify-send")
    if notify is None:
        return None
    cmd = [notify, "--app-name", APP_NAME, "--icon", ICON,
           # Replaces the previous clip notification instead of stacking one
           # per clip during a session of heavy clipping.
           "--hint", "string:x-canonical-private-synchronous:asm-clip",
           summary]
    if body:
        cmd.append(body)
    return cmd


def clip_saved(path: Path, sound: bool = True, notify: bool = True) -> None:
    """Announce that *path* was written.

    Both halves are optional and independent: someone who wants the sound but
    not the pop-up (or the reverse) can have it, and a machine missing either
    tool still gets the other.
    """
    if sound:
        _spawn(sound_command())
    if notify:
        _spawn(notify_command(
            "Clip saved", f"{path.name} — open Clips to trim and share it."))


def _spawn(cmd: list[str] | None) -> None:
    """Fire and forget. Never raises, never waits.

    Waiting would put a sound player on the path between the shortcut and the
    next thing the user does; raising would turn a missing player into a failed
    clip, which it is not.
    """
    if not cmd:
        return
    try:
        subprocess.Popen(cmd,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    except OSError as exc:
        log.debug("could not run %s: %s", cmd[0], exc)
