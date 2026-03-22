#!/usr/bin/env python3
"""
Apply manual routing overrides for audio streams.
Respects manual overrides written by the GUI (routing_overrides.json).
Detects manual moves done in KDE and saves them as persistent overrides.
"""
import json
import logging
import time
from pathlib import Path

import pulsectl

from arctis_sound_manager.pw_utils import get_native_streams, move_native_stream

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("video_router")

# Wake up on PulseAudio events; fall back to periodic check for native PW streams
EVENT_TIMEOUT    = 5.0   # seconds to wait for a PA event before forced re-check
EVENT_DEBOUNCE   = 0.15  # seconds to let rapid event bursts settle
NATIVE_INTERVAL  = 5.0   # seconds between pw-dump calls (expensive subprocess)
OVERRIDES_FILE = Path.home() / ".config" / "arctis_manager" / "routing_overrides.json"

# Tracks where the router last placed each app (PA sink index).
# Used to detect manual moves done outside the router (e.g. KDE audio mixer).
_pa_placed: dict[str, int] = {}

# Same for native PipeWire streams (sink node name).
_native_placed: dict[str, str] = {}


def load_overrides() -> dict:
    if OVERRIDES_FILE.exists():
        try:
            return json.loads(OVERRIDES_FILE.read_text())
        except Exception:
            pass
    return {}


def save_overrides(overrides: dict) -> None:
    # Atomic write: write to tmp then rename to avoid corruption if both
    # gui and video_router write simultaneously
    tmp = OVERRIDES_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(overrides, indent=2))
    tmp.replace(OVERRIDES_FILE)


def _sink_name(sinks, index: int) -> str | None:
    s = next((s for s in sinks if s.index == index), None)
    return s.name if s else None


def _subscribe(pulse: pulsectl.Pulse) -> None:
    """Subscribe to sink and sink-input events; stop the loop on any event."""
    pulse.event_mask_set('sink', 'sink_input')
    pulse.event_callback_set(lambda _e: pulse.event_listen_stop())


def main():
    log.info("Starting routing override daemon")
    pulse = pulsectl.Pulse("arctis-video-router")
    _subscribe(pulse)

    last_native_check = 0.0

    while True:
        try:
            # Block until a PA sink/sink-input event or EVENT_TIMEOUT seconds
            try:
                pulse.event_listen(timeout=EVENT_TIMEOUT)
                # Event occurred — wait briefly for burst to settle
                time.sleep(EVENT_DEBOUNCE)
            except pulsectl.PulseLoopStop:
                # Raised by event_listen_stop callback: re-arm and continue
                _subscribe(pulse)

            sinks = pulse.sink_list()

            # Si le default sink n'est pas un sink Arctis, le router s'efface
            # et laisse KDE gérer le routing librement.
            server_info = pulse.server_info()
            default_sink_name = server_info.default_sink_name or ""
            arctis_is_default = any(
                k in default_sink_name for k in ("Arctis_", "SteelSeries_Arctis", "effect_input")
            )
            if not arctis_is_default:
                # Déplace les apps bloquées sur des sinks Arctis virtuels
                # vers le nouveau default sink choisi dans KDE.
                default_sink = next((s for s in sinks if s.name == default_sink_name), None)
                if default_sink:
                    arctis_virtual = {"Arctis_Game", "Arctis_Chat", "effect_input.sonar"}
                    idx_to_name = {s.index: s.name for s in sinks}
                    for si in pulse.sink_input_list():
                        app = si.proplist.get("application.name", "")
                        if not app:
                            continue
                        on_arctis = any(k in idx_to_name.get(si.sink, "") for k in arctis_virtual)
                        if on_arctis and si.sink != default_sink.index:
                            log.info("Default non-Arctis: déplacement '%s' -> %s", app, default_sink_name)
                            pulse.sink_input_move(si.index, default_sink.index)
                _pa_placed.clear()
                _native_placed.clear()
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
                    wanted_index = sink_map.get(wanted_sink_name)
                    if wanted_index is not None and si.sink != wanted_index:
                        log.info("Override: moving '%s' -> %s", app, wanted_sink_name)
                        pulse.sink_input_move(si.index, wanted_index)
                        _pa_placed[app] = wanted_index
                    else:
                        _pa_placed[app] = si.sink

            # ── Native PipeWire streams (mpv, haruna…) ────────────────────────
            # pw-dump is expensive — only run every NATIVE_INTERVAL seconds
            now = time.monotonic()
            if now - last_native_check < NATIVE_INTERVAL:
                time.sleep(0)
                continue
            last_native_check = now
            native_streams = get_native_streams()
            for s in native_streams:
                app = s["app_name"]

                # Detect manual move for native streams
                if app in _native_placed:
                    placed = _native_placed[app]
                    current = s["sink_name"]
                    if current and current != placed:
                        log.info("Manual move detected (native): '%s' -> %s (saving override)", app, current)
                        overrides[app] = current
                        save_overrides(overrides)
                        _native_placed[app] = current

                if app in overrides:
                    wanted = overrides[app]
                    if s["sink_name"] is None or s["sink_name"] != wanted:
                        log.info("Override native: moving '%s' -> %s", app, wanted)
                        move_native_stream(s["id"], wanted)
                        _native_placed[app] = wanted
                    else:
                        _native_placed[app] = s["sink_name"]
                    continue


        except pulsectl.PulseDisconnected:
            log.warning("PulseAudio disconnected, reconnecting...")
            try:
                pulse.close()
            except Exception:
                pass
            time.sleep(2)
            pulse = pulsectl.Pulse("arctis-video-router")
            _subscribe(pulse)
            last_native_check = 0.0
            _pa_placed.clear()
            _native_placed.clear()
        except Exception as e:
            log.error("Error: %s", e)
            time.sleep(1)


if __name__ == "__main__":
    main()
