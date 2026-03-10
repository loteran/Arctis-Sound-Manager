#!/usr/bin/env python3
"""
Route browsers and video players to Arctis_Media sink automatically.
Respects manual overrides written by the GUI (routing_overrides.json).
Detects manual moves done in KDE and saves them as persistent overrides.
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

# Tracks where the router last placed each app (PA sink index).
# Used to detect manual moves done outside the router (e.g. KDE audio mixer).
_pa_placed: dict[str, int] = {}

# Same for native PipeWire streams (sink node name).
_native_placed: dict[str, str] = {}


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


def save_overrides(overrides: dict) -> None:
    OVERRIDES_FILE.write_text(json.dumps(overrides, indent=2))


def _sink_name(sinks, index: int) -> str | None:
    s = next((s for s in sinks if s.index == index), None)
    return s.name if s else None


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

            # Si le default sink n'est pas un sink Arctis, le router s'efface
            # et laisse KDE gérer le routing librement.
            server_info = pulse.server_info()
            default_sink_name = server_info.default_sink_name or ""
            arctis_is_default = any(
                k in default_sink_name for k in ("Arctis_", "SteelSeries_Arctis", "effect_input")
            )
            if not arctis_is_default:
                # Déplace les video apps bloquées sur des sinks Arctis virtuels
                # vers le nouveau default sink choisi dans KDE.
                default_sink = next((s for s in sinks if s.name == default_sink_name), None)
                if default_sink:
                    arctis_virtual = {"Arctis_Game", "Arctis_Chat", "Arctis_Media", "Arctis_Video"}
                    idx_to_name = {s.index: s.name for s in sinks}
                    for si in pulse.sink_input_list():
                        app = si.proplist.get("application.name", "")
                        if not app or not is_video_app(app):
                            continue
                        # En mode non-Arctis, KDE a la priorité — ignorer les overrides Arctis.
                        # Déplacer les apps bloquées sur des sinks Arctis virtuels vers le default.
                        on_arctis = any(k in idx_to_name.get(si.sink, "") for k in arctis_virtual)
                        if on_arctis and si.sink != default_sink.index:
                            log.info("Default non-Arctis: déplacement '%s' -> %s", app, default_sink_name)
                            pulse.sink_input_move(si.index, default_sink.index)
                _pa_placed.clear()
                _native_placed.clear()
                time.sleep(POLL_INTERVAL)
                continue

            overrides = load_overrides()
            sink_inputs = pulse.sink_input_list()
            sink_map = {s.name: s.index for s in sinks}

            # ── PulseAudio streams ────────────────────────────────────────────
            for si in sink_inputs:
                app = si.proplist.get("application.name", "")
                if not app:
                    continue

                # Detect manual move: app was placed by router but is now elsewhere
                if app in _pa_placed and si.sink != _pa_placed[app]:
                    current_name = _sink_name(sinks, si.sink)
                    if current_name:
                        log.info("Manual move detected: '%s' -> %s (saving override)", app, current_name)
                        overrides[app] = current_name
                        save_overrides(overrides)
                    _pa_placed[app] = si.sink

                if app in overrides:
                    wanted_sink_name = overrides[app]
                    wanted_index = next(
                        (idx for name, idx in sink_map.items() if wanted_sink_name in name),
                        None
                    )
                    if wanted_index is not None and si.sink != wanted_index:
                        log.info("Override: moving '%s' -> %s", app, wanted_sink_name)
                        pulse.sink_input_move(si.index, wanted_index)
                        _pa_placed[app] = wanted_index
                    else:
                        _pa_placed[app] = si.sink
                    continue

                # Auto-route video apps to Arctis_Media
                if is_video_app(app):
                    if si.sink != video_sink.index:
                        log.info("Moving '%s' -> %s", app, TARGET_SINK)
                        pulse.sink_input_move(si.index, video_sink.index)
                        _pa_placed[app] = video_sink.index
                    else:
                        _pa_placed[app] = si.sink

            # ── Native PipeWire streams (mpv, haruna…) ────────────────────────
            native_streams = get_native_streams()
            for s in native_streams:
                app = s["app_name"]

                # Detect manual move for native streams
                if app in _native_placed:
                    placed = _native_placed[app]
                    current = s["sink_name"]
                    if current and placed not in current:
                        log.info("Manual move detected (native): '%s' -> %s (saving override)", app, current)
                        overrides[app] = current
                        save_overrides(overrides)
                        _native_placed[app] = current

                if app in overrides:
                    wanted = overrides[app]
                    if s["sink_name"] is None or wanted not in s["sink_name"]:
                        log.info("Override native: moving '%s' -> %s", app, wanted)
                        move_native_stream(s["id"], wanted)
                        _native_placed[app] = wanted
                    else:
                        _native_placed[app] = s["sink_name"]
                    continue

                if is_video_app(app) and (s["sink_name"] is None or TARGET_SINK not in s["sink_name"]):
                    log.info("Moving native '%s' -> %s", app, TARGET_SINK)
                    move_native_stream(s["id"], TARGET_SINK)
                    _native_placed[app] = TARGET_SINK
                elif is_video_app(app):
                    _native_placed[app] = s["sink_name"]

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
