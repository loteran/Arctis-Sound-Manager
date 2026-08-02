#!/usr/bin/env python3
# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""asm-clipd — keeps a rolling capture running so the last N seconds can be saved.

Run it in the background and send it SIGUSR1 — from a desktop keybinding, or
by hand — and the preceding seconds land in ~/Videos/ASM Clips as an .mkv with
one audio track per Sonar channel. (The GUI's Clips page does not need this
daemon: it runs a capture of its own and binds the portal shortcut itself.)

    asm-clipd                 # capture, save on SIGUSR1
    asm-clipd --save-after 15 # capture, save once after 15 s, exit (a self-test)
    asm-clipd --forget        # drop the saved screen choice and pick again

The screen picker appears the first time only: Wayland requires that consent
once and offers no way around it, so the choice is persisted and restored
silently on every later run.
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys

from arctis_sound_manager.log_setup import configure_logging


def main() -> int:
    # Safe this early: clip_capture keeps GStreamer behind _require_gst(), so
    # importing it for two constants does not pull gi in on a machine without it.
    from arctis_sound_manager.clip_capture import DEFAULT_FPS

    parser = argparse.ArgumentParser(prog="asm-clipd", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seconds", type=float, default=30.0,
                        help="clip length to save (default: 30)")
    parser.add_argument("--history", type=float, default=90.0,
                        help="seconds of history to keep buffered (default: 90)")
    parser.add_argument("--fps", type=int, default=DEFAULT_FPS,
                        help=f"capture rate ceiling (default: {DEFAULT_FPS}); "
                             "the screencast decides the real rate")
    parser.add_argument("--bitrate", type=int, default=20000, help="kbit/s")
    parser.add_argument("--window", action="store_true",
                        help="capture a single window instead of a screen")
    parser.add_argument("--save-after", type=float, metavar="S",
                        help="save one clip after S seconds, then exit")
    parser.add_argument("--forget", action="store_true",
                        help="forget the saved screen choice and exit")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    configure_logging(default=logging.DEBUG if args.verbose else logging.INFO,
                      fmt="[%(levelname)s] %(message)s")
    log = logging.getLogger("clipd")

    from arctis_sound_manager.clip_capture import (ClipCapture,
                                                   ClipCaptureUnavailable,
                                                   ScreenCastPortal, detect_game)

    if args.forget:
        ScreenCastPortal.forget(ScreenCastPortal)  # type: ignore[arg-type]
        log.info("Saved screen choice forgotten — the picker will appear next run.")
        return 0

    try:
        capture = ClipCapture(history_s=args.history, fps=args.fps,
                              bitrate_kbps=args.bitrate, window=args.window)
        capture.start()
    except ClipCaptureUnavailable as exc:
        log.error("Clip capture unavailable: %s", exc)
        return 1

    game = detect_game()
    log.info("Capturing%s — keeping %.0fs of history.",
             f" ({game})" if game else "", args.history)

    from gi.repository import GLib
    loop = GLib.MainLoop()

    def save(*_):
        path = capture.save_clip(args.seconds)
        if path is None:
            log.warning("Buffer still filling — %.1fs available.", capture.ready_s)
        return True

    if args.save_after:
        log.info("Saving one clip in %.0fs…", args.save_after)

        def once():
            save()
            loop.quit()
            return False

        GLib.timeout_add_seconds(int(args.save_after), once)
    else:
        # SIGUSR1 is the trigger a desktop shortcut can reach without ASM
        # needing a global-hotkey grab of its own.
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR1, save)
        log.info("Send SIGUSR1 to save the last %.0fs:  kill -USR1 %d",
                 args.seconds, __import__("os").getpid())

    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT,
                         lambda *_: (loop.quit(), False)[1])
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM,
                         lambda *_: (loop.quit(), False)[1])

    try:
        loop.run()
    finally:
        capture.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
