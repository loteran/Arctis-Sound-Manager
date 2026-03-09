#!/usr/bin/env python3
"""
Route browsers and video players to Arctis_Media sink automatically.
Respects manual overrides written by the GUI (routing_overrides.json).
"""
import json
import logging
import time
from pathlib import Path

import pulsectl

from linux_arctis_manager.pw_utils import get_native_streams, move_native_stream

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("video_router")

VIDEO_APPS = {
    "firefox",
    "chromium",
    "google-chrome",
    "brave",
    "opera",
    "vivaldi",
    "vlc",
    "mpv",
    "haruna",
    "totem",
    "celluloid",
    "dragon",
    "kaffeine",
    "gwenview",
    "clementine",
}

TARGET_SINK = "Arctis_Media"
POLL_INTERVAL = 1.0
OVERRIDES_FILE = Path.home() / ".config" / "arctis_manager" / "routing_overrides.json"


def is_video_app(app_name: str) -> bool:
    return app_name.lower() in VIDEO_APPS or any(
        v in app_name.lower() for v in VIDEO_APPS
    )


def load_overrides() -> dict:
    if OVERRIDES_FILE.exists():
        try:
            return json.loads(OVERRIDES_FILE.read_text())
        except Exception:
            pass
    return {}


def main():
    log.info("Starting video router — target sink: %s", TARGET_SINK)
    pulse = pulsectl.Pulse("arctis-video-router")

    while True:
        try:
            sinks = pulse.sink_list()
            video_sink = next((s for s in sinks if TARGET_SINK in s.name), None)

            if video_sink is None:
                time.sleep(POLL_INTERVAL)
                continue

            overrides = load_overrides()
            sink_inputs = pulse.sink_input_list()

            # Build a name->sink index map for override targets
            sink_map = {s.name: s.index for s in sinks}

            for si in sink_inputs:
                app = si.proplist.get("application.name", "")

                # If the user manually placed this app, respect it (persists across restarts)
                if app in overrides:
                    wanted_sink_name = overrides[app]
                    wanted_index = next(
                        (idx for name, idx in sink_map.items() if wanted_sink_name in name),
                        None
                    )
                    if wanted_index is not None and si.sink != wanted_index:
                        log.info("Override: moving '%s' -> %s", app, wanted_sink_name)
                        pulse.sink_input_move(si.index, wanted_index)
                    continue

                # Auto-route video apps to Arctis_Media
                if is_video_app(app) and si.sink != video_sink.index:
                    log.info("Moving '%s' -> %s", app, TARGET_SINK)
                    pulse.sink_input_move(si.index, video_sink.index)

            # ── Native PipeWire streams (mpv, haruna…) ───────────────────────
            for s in get_native_streams():
                app = s["app_name"]

                if app in overrides:
                    wanted = overrides[app]
                    if s["sink_name"] is None or wanted not in s["sink_name"]:
                        log.info("Override native: moving '%s' -> %s", app, wanted)
                        move_native_stream(s["id"], wanted)
                    continue

                if is_video_app(app) and (s["sink_name"] is None or TARGET_SINK not in s["sink_name"]):
                    log.info("Moving native '%s' -> %s", app, TARGET_SINK)
                    move_native_stream(s["id"], TARGET_SINK)

        except pulsectl.PulseDisconnected:
            log.warning("PulseAudio disconnected, reconnecting...")
            try:
                pulse.close()
            except Exception:
                pass
            time.sleep(2)
            pulse = pulsectl.Pulse("arctis-video-router")
        except Exception as e:
            log.error("Error: %s", e)
            time.sleep(POLL_INTERVAL)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
