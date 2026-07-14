#!/usr/bin/env python3
# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Apply manual routing overrides for audio streams.
Respects manual overrides written by the GUI (routing_overrides.json).
Detects manual moves done in KDE and saves them as persistent overrides.
"""
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

import pulsectl

from arctis_sound_manager.constants import (DBUS_BUS_NAME,
                                            DBUS_STATUS_INTERFACE_NAME,
                                            DBUS_STATUS_OBJECT_PATH)
from arctis_sound_manager.power_status import HeadsetPower, extract_power_status
from arctis_sound_manager.pw_utils import app_override_key, get_native_streams, move_native_stream

from arctis_sound_manager.log_setup import configure_logging
configure_logging(default=logging.INFO, fmt="[%(levelname)s] %(message)s")
log = logging.getLogger("video_router")

# Wake up on PulseAudio events; fall back to periodic check for native PW streams
EVENT_TIMEOUT    = 5.0   # seconds to wait for a PA event before forced re-check
EVENT_DEBOUNCE   = 0.15  # seconds to let rapid event bursts settle
NATIVE_INTERVAL  = 5.0   # seconds between pw-dump calls (expensive subprocess)
OVERRIDES_FILE = Path.home() / ".config" / "arctis_manager" / "routing_overrides.json"
CHANNEL_OUTPUTS_FILE = Path.home() / ".config" / "arctis_manager" / "channel_output_devices.json"

# Arctis virtual sinks the router repatriates from when the headset is off,
# and treats interchangeably wherever "is this app on an Arctis channel?" is
# asked. Arctis_Media used to be missing here (only Game/Chat were listed),
# which made repatriation asymmetric between channels.
ARCTIS_VIRTUAL_SINKS = {"Arctis_Game", "Arctis_Chat", "Arctis_Media", "effect_input.sonar"}

# D-Bus query for the daemon's headset power status (fix for the sovereignty
# bug: routing decisions must key off online/offline, not off the current
# default sink). Cached briefly so the router doesn't hit D-Bus every tick;
# any failure (daemon down, no reply, malformed payload) resolves to UNKNOWN,
# and callers must treat UNKNOWN as "do not touch routing" (R3 fail-safe).
_POWER_DBUS_TIMEOUT = 2.0   # seconds
_POWER_CACHE_TTL    = 3.0   # seconds
_power_cache: tuple[float, HeadsetPower] = (0.0, HeadsetPower.UNKNOWN)


async def _fetch_headset_power_async() -> HeadsetPower:
    from dbus_next.aio.message_bus import MessageBus
    from dbus_next.constants import MessageType
    from dbus_next.message import Message

    bus = await MessageBus().connect()
    try:
        reply = await bus.call(Message(
            destination=DBUS_BUS_NAME,
            path=DBUS_STATUS_OBJECT_PATH,
            interface=DBUS_STATUS_INTERFACE_NAME,
            member='GetStatus',
            message_type=MessageType.METHOD_CALL,
        ))
        if reply is None or reply.message_type == MessageType.ERROR:
            return HeadsetPower.UNKNOWN
        return extract_power_status(json.loads(reply.body[0]) or {})
    finally:
        bus.disconnect()


def get_headset_power(force: bool = False) -> HeadsetPower:
    """Query the daemon's headset power status, short-cached.

    Synchronous wrapper around the async dbus_next call, since the router's
    main loop is a plain blocking pulsectl loop, not asyncio. Guarded by an
    overall timeout so an unreachable daemon (D-Bus muet) cannot stall the
    router — it just resolves to UNKNOWN for that tick (R3 fail-safe).
    """
    global _power_cache
    now = time.monotonic()
    cached_at, cached_value = _power_cache
    if not force and (now - cached_at) < _POWER_CACHE_TTL:
        return cached_value

    try:
        power = asyncio.run(asyncio.wait_for(_fetch_headset_power_async(), timeout=_POWER_DBUS_TIMEOUT))
    except Exception as e:
        log.debug("Could not query headset power status: %s", e)
        power = HeadsetPower.UNKNOWN

    _power_cache = (now, power)
    return power


def _load_channel_outputs() -> dict:
    if CHANNEL_OUTPUTS_FILE.exists():
        try:
            return json.loads(CHANNEL_OUTPUTS_FILE.read_text())
        except Exception:
            pass
    return {}


# effect_input sinks are internal filter-chain nodes — apps should never
# target them directly.  Remap to the corresponding Arctis virtual sink.
_EFFECT_REMAP = {
    "effect_input.sonar-game-eq": "Arctis_Game",
    "effect_input.sonar-chat-eq": "Arctis_Chat",
}

# Auto-routing: binaries that indicate a game (wine/proton/gamescope)
_GAME_BINARIES = {"wine64-preloader", "wine-preloader", "wine", "wine64",
                   "proton", "gamescope", "reaper"}

# Auto-routing: known browser application names → Media channel
_BROWSER_APPS = {"Firefox", "Chromium", "Google Chrome", "Brave", "Vivaldi",
                 "Opera", "Microsoft Edge", "Zen Browser", "LibreWolf",
                 "Waterfox", "Tor Browser", "Floorp", "Mullvad Browser",
                 "Thorium", "Chrome", "Ungoogled Chromium"}

# Known VoIP / chat apps → Chat channel
_CHAT_APPS = {"WEBRTC VoiceEngine", "Discord", "TeamSpeak", "Mumble",
              "Element", "Signal"}


def _auto_route(app: str, proplist: dict) -> str | None:
    """Return an Arctis sink name for an app based on heuristics, or None."""
    binary = proplist.get("application.process.binary", "")
    if binary in _GAME_BINARIES:
        return "Arctis_Game"
    if app in _BROWSER_APPS:
        return "Arctis_Media"
    if app in _CHAT_APPS:
        return "Arctis_Chat"
    return None

# Tracks where the router last placed each app (PA sink index), keyed by
# app_override_key() (issue #108: composite "name|binary" for apps that
# share a generic application.name, plain name otherwise).
# Used to detect manual moves done outside the router (e.g. KDE audio mixer).
_pa_placed: dict[str, int] = {}

# Same for native PipeWire streams (sink node name), same keying.
_native_placed: dict[str, str] = {}

# Anti-flap guard (issue #102): WirePlumber can bounce a stream between the
# Arctis virtual sinks (Game<->Chat<->Media). Those induced moves must not be
# saved as user overrides. If the same app changes target >= _FLAP_THRESHOLD
# times within _FLAP_WINDOW seconds, treat the move as WirePlumber-induced.
# 30 s window: the native-PW path is only polled every NATIVE_INTERVAL (5 s),
# so a shorter window could never accumulate 3 detections there.
_FLAP_WINDOW = 30.0     # seconds
_FLAP_THRESHOLD = 3     # detected moves within the window
_move_times: dict[str, list[float]] = {}


def _is_flapping(app: str, now: float | None = None) -> bool:
    """Record a detected external move for *app*; True when it exceeds the
    anti-flap threshold (WirePlumber-induced — do not save an override)."""
    if now is None:
        now = time.monotonic()
    times = [t for t in _move_times.get(app, []) if now - t < _FLAP_WINDOW]
    times.append(now)
    _move_times[app] = times
    return len(times) >= _FLAP_THRESHOLD


# Stability gate (issue #102, residual gap): _FLAP_THRESHOLD only catches a
# move once it has recurred >= 3 times within _FLAP_WINDOW, so the first one
# or two WirePlumber-induced flips in a burst used to be saved as overrides
# immediately, before the anti-flap guard had a chance to arm. A detected
# move is now only persisted once it has remained on the same target for
# _STABILITY_DELAY seconds without being displaced again.
_STABILITY_DELAY = 2.0  # seconds
_pending_moves: dict[str, tuple[str, float]] = {}


def _confirm_manual_move(
    key: str, app: str, save_name: str, overrides: dict, now: float | None = None,
) -> bool:
    """Handle a detected drift of *app* away from its last known placement.

    Returns True when the caller should treat *key*'s tracked placement as
    settled at the new sink — either because the override was just written,
    or because the move was classified as noise (WirePlumber restoring its
    own preference, or flapping) and should not be re-evaluated every tick.
    Returns False when the move is still an unconfirmed candidate: the
    caller must leave its placement tracking untouched so the discrepancy is
    detected again on the next tick and re-checked for stability.
    """
    if now is None:
        now = time.monotonic()

    pending = _pending_moves.get(key)
    if pending is not None and pending[0] == save_name:
        # Same candidate as the previous tick(s) — not a new move, just
        # check whether it has now been stable long enough to persist.
        if now - pending[1] >= _STABILITY_DELAY:
            log.info("Manual move detected: '%s' -> %s (saving override)", app, save_name)
            overrides[key] = save_name
            save_overrides(overrides)
            _pending_moves.pop(key, None)
            return True
        return False

    # A genuinely new move (no pending candidate, or a different target).
    if _is_physical_arctis(save_name):
        # WirePlumber restoring its target.object preference — not a user
        # move, do not save and drop any stale pending candidate.
        log.debug("Ignoring WirePlumber move of '%s' -> %s", app, save_name)
        _pending_moves.pop(key, None)
        return True
    if _is_flapping(key, now=now):
        # WirePlumber bouncing the stream between sinks — keep the existing
        # override, the enforcement pass below will move the stream back.
        log.info("Anti-flap: ignoring move of '%s' -> %s (override kept)", app, save_name)
        _pending_moves.pop(key, None)
        return True
    log.debug("Manual move candidate: '%s' -> %s (awaiting stability)", app, save_name)
    _pending_moves[key] = (save_name, now)
    return False


def _lookup_override(overrides: dict, key: str, app: str) -> str | None:
    """Look up a saved override target: composite key first (issue #108),
    then the legacy (name-only) key for entries written before that fix.

    For non-generic apps *key* and *app* are identical, so the legacy lookup
    is a harmless no-op.
    """
    if key in overrides:
        return overrides[key]
    if app != key:
        return overrides.get(app)
    return None


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


def _is_physical_arctis(sink_name: str) -> bool:
    """Return True for the physical Arctis hardware output.

    WirePlumber may restore a stream's target.object preference back to the
    physical output after our router moves it to a virtual channel.  We must
    not treat that as a deliberate user action and save it as an override.
    """
    return "SteelSeries_Arctis" in sink_name and not sink_name.startswith("Arctis_")


def _subscribe(pulse: pulsectl.Pulse) -> None:
    """Subscribe to sink and sink-input events; stop the loop on any event."""
    pulse.event_mask_set('sink', 'sink_input')
    pulse.event_callback_set(lambda _e: pulse.event_listen_stop())


_PID_FILE = Path.home() / ".config" / "arctis_manager" / "video_router.pid"


def _acquire_singleton() -> bool:
    """Return True if we are the sole running instance, False otherwise."""
    if _PID_FILE.exists():
        try:
            old_pid = int(_PID_FILE.read_text().strip())
            # Check if that PID is still alive
            os.kill(old_pid, 0)
            log.warning(
                "Another asm-router instance (PID %d) is already running — exiting.", old_pid
            )
            return False
        except (ValueError, ProcessLookupError, PermissionError):
            pass  # stale PID file — take over
    _PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    _PID_FILE.write_text(str(os.getpid()))
    return True


def _release_singleton() -> None:
    try:
        _PID_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def main():
    if not _acquire_singleton():
        sys.exit(0)
    try:
        _main_loop()
    finally:
        _release_singleton()


# Native-stream re-check cadence tracking (pw-dump is expensive — throttled
# to NATIVE_INTERVAL). Module-level so _process_tick() can be called directly
# per-tick, from _main_loop() or from tests, without threading this through
# as a parameter.
_last_native_check = 0.0


def _process_tick(pulse: pulsectl.Pulse) -> None:
    """Run one routing reconciliation pass.

    Split out from _main_loop() so it can be exercised directly (with a
    mocked pulsectl.Pulse) instead of needing to fake the blocking
    event_listen()/PulseLoopStop machinery that wraps it. A bare `return`
    below ends the tick early — equivalent to the old `continue` back to the
    top of _main_loop()'s `while True`.
    """
    global _last_native_check

    sinks = pulse.sink_list()

    server_info = pulse.server_info()
    default_sink_name = server_info.default_sink_name or ""
    default_sink = next((s for s in sinks if s.name == default_sink_name), None)
    arctis_is_default = any(
        k in default_sink_name for k in ("Arctis_", "SteelSeries_Arctis")
    )

    # Repatriation is keyed on the headset's actual power state (R2), never
    # on which sink happens to be default: a saved override is sovereign
    # (R1) and must be enforced even while e.g. HDMI is the default sink. An
    # UNKNOWN power state (daemon down, D-Bus unreachable) fails safe to
    # "touch nothing" (R3).
    headset_power = get_headset_power()
    if headset_power == HeadsetPower.OFF:
        # Headset is off: its virtual sinks are effectively dead, so pull
        # any stream still parked on one of them onto the current default
        # sink. This is a transient move, not a user choice — never
        # persisted as an override (R5). When the headset comes back online
        # the normal enforcement pass below reapplies the saved override and
        # brings the app back.
        if default_sink:
            idx_to_name = {s.index: s.name for s in sinks}
            for si in pulse.sink_input_list():
                app = si.proplist.get("application.name", "")
                if not app:
                    continue
                on_arctis = any(
                    k in idx_to_name.get(si.sink, "") for k in ARCTIS_VIRTUAL_SINKS
                )
                if on_arctis and si.sink != default_sink.index:
                    log.info("Headset off: déplacement '%s' -> %s", app, default_sink_name)
                    pulse.sink_input_move(si.index, default_sink.index)
        _pa_placed.clear()
        _native_placed.clear()
        _move_times.clear()
        _pending_moves.clear()
        return

    overrides = load_overrides()
    sink_inputs = pulse.sink_input_list()
    sink_map = {s.name: s.index for s in sinks}
    sink_idx_to_name = {s.index: s.name for s in sinks}

    # ── PulseAudio streams ────────────────────────────────────────────────
    pa_now = time.monotonic()
    for si in sink_inputs:
        app = si.proplist.get("application.name", "")
        if not app:
            continue
        # Composite key (issue #108): disambiguates apps that share a
        # generic application.name such as "Chromium".
        key = app_override_key(app, si.proplist.get("application.process.binary", ""))

        # Detect manual move: app was placed by router but is now elsewhere
        if key in _pa_placed and si.sink != _pa_placed[key]:
            current_name = _sink_name(sinks, si.sink)
            if current_name:
                # Never save effect_input sinks as overrides
                save_name = _EFFECT_REMAP.get(current_name, current_name)
                if _confirm_manual_move(key, app, save_name, overrides, now=pa_now):
                    _pa_placed[key] = si.sink
                # else: still an unconfirmed candidate (issue #102) —
                # leave the tracked placement stale so the next tick
                # re-checks it for stability.
            else:
                _pa_placed[key] = si.sink
        elif key in _pa_placed:
            # No drift this tick — invalidate any stale pending candidate.
            _pending_moves.pop(key, None)

        if key in _pending_moves:
            # Awaiting stability confirmation (issue #102): do not let
            # enforcement or auto-route fight the pending manual move.
            continue

        wanted = _lookup_override(overrides, key, app)

        # Auto-route new apps that have no override yet. Only while Arctis
        # is the default sink: the router must not impose itself on apps
        # the user hasn't explicitly placed when they've chosen another
        # default output (e.g. HDMI/TV). Existing overrides are enforced
        # unconditionally below (R1).
        if wanted is None and arctis_is_default:
            auto = _auto_route(app, si.proplist)
            if not auto:
                # Fallback adoption (issue #20): when Arctis is default
                # but a stream still plays through another physical
                # output (Logitech, internal speakers, etc.), pull it
                # onto Arctis_Media so the user actually hears it in
                # the headset. Skipped if the stream is already on any
                # Arctis sink (virtual or filter-chain) so manual moves
                # are preserved.
                current_name = sink_idx_to_name.get(si.sink, "")
                on_arctis = any(
                    k in current_name
                    for k in ("Arctis_", "SteelSeries_Arctis", "effect_input.sonar")
                )
                if not on_arctis:
                    auto = "Arctis_Media"
                    log.info(
                        "Adopt: '%s' was on '%s' while Arctis is default — moving to %s",
                        app, current_name, auto,
                    )
            if auto:
                log.info("Auto-route: '%s' -> %s", app, auto)
                overrides[key] = auto
                save_overrides(overrides)
                wanted = auto

        if wanted is not None:
            wanted_index = sink_map.get(wanted)
            if wanted_index is not None and si.sink != wanted_index:
                log.info("Override: moving '%s' -> %s", app, wanted)
                pulse.sink_input_move(si.index, wanted_index)
                _pa_placed[key] = wanted_index
            else:
                _pa_placed[key] = si.sink
        else:
            # App we neither auto-route nor have an override for (e.g. a
            # browser not in _BROWSER_APPS that already sits on an Arctis
            # sink). Still record where it currently is, so a later manual
            # move (KDE mixer → another channel) is detected next tick and
            # saved as an override. Without this the move is never seen and
            # the channel choice is "forgotten" (issue #64).
            _pa_placed[key] = si.sink

    # ── Native PipeWire streams (mpv, haruna…) ──────────────────────────────
    # pw-dump is expensive — only run every NATIVE_INTERVAL seconds
    now = time.monotonic()
    if now - _last_native_check < NATIVE_INTERVAL:
        time.sleep(0)
        return
    _last_native_check = now
    native_streams = get_native_streams()
    for s in native_streams:
        app = s["app_name"]
        binary = s.get("props", {}).get("application.process.binary", "")
        key = app_override_key(app, binary)

        # Detect manual move for native streams
        if key in _native_placed:
            placed = _native_placed[key]
            current = s["sink_name"]
            if current and current != placed:
                # Never save effect_input sinks as overrides
                save_name = _EFFECT_REMAP.get(current, current)
                if _confirm_manual_move(key, app, save_name, overrides, now=now):
                    _native_placed[key] = current
                # else: still an unconfirmed candidate (issue #102) —
                # leave the tracked placement stale so the next check
                # re-evaluates it for stability.
            else:
                # No drift this tick — invalidate any stale pending candidate.
                _pending_moves.pop(key, None)

        if key in _pending_moves:
            # Awaiting stability confirmation (issue #102): do not let
            # enforcement or auto-route fight the pending manual move.
            continue

        wanted = _lookup_override(overrides, key, app)

        # Auto-route new native apps that have no override yet — same
        # "only when Arctis is default" rule as the PA path above.
        if app and wanted is None and arctis_is_default:
            auto = _auto_route(app, s.get("props", {}))
            if not auto:
                # Same adoption fallback as for PA streams (issue #20):
                # native PW stream playing on a non-Arctis sink while
                # Arctis is default → move to Arctis_Media. Skip when
                # the stream is already on an Arctis target (manual
                # placement preserved).
                current = s.get("sink_name") or ""
                on_arctis = any(
                    k in current
                    for k in ("Arctis_", "SteelSeries_Arctis", "effect_input.sonar")
                )
                if current and not on_arctis:
                    auto = "Arctis_Media"
                    log.info(
                        "Adopt (native): '%s' was on '%s' while Arctis is default — moving to %s",
                        app, current, auto,
                    )
            if auto:
                log.info("Auto-route (native): '%s' -> %s", app, auto)
                overrides[key] = auto
                save_overrides(overrides)
                wanted = auto

        if wanted is not None:
            if s["sink_name"] is None or s["sink_name"] != wanted:
                log.info("Override native: moving '%s' -> %s", app, wanted)
                move_native_stream(s["id"], wanted)
                _native_placed[key] = wanted
            else:
                _native_placed[key] = s["sink_name"]
            continue

    # ── Per-channel output device enforcement ───────────────────────────────
    channel_outputs = _load_channel_outputs()
    if channel_outputs:
        _ch_virtual = {"game": "Arctis_Game", "chat": "Arctis_Chat", "media": "Arctis_Media"}
        for _ch, _target_name in channel_outputs.items():
            _virtual_frag = _ch_virtual.get(_ch)
            if not _virtual_frag:
                continue
            _target_idx = sink_map.get(_target_name)
            if _target_idx is None:
                continue
            for _si in sink_inputs:
                _app = _si.proplist.get("application.name", "")
                if not _app:
                    continue
                _key = app_override_key(_app, _si.proplist.get("application.process.binary", ""))
                _current_name = sink_idx_to_name.get(_si.sink, "")
                if _virtual_frag in _current_name and _si.sink != _target_idx:
                    log.info("Channel output: '%s' %s -> %s", _app, _current_name, _target_name)
                    try:
                        pulse.sink_input_move(_si.index, _target_idx)
                        _pa_placed[_key] = _target_idx
                    except Exception:
                        pass


def _main_loop():
    global _last_native_check

    log.info("Starting routing override daemon")
    pulse = pulsectl.Pulse("arctis-video-router")
    _subscribe(pulse)

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

            _process_tick(pulse)

        except pulsectl.PulseDisconnected:
            log.warning("PulseAudio disconnected, reconnecting...")
            try:
                pulse.close()
            except Exception:
                pass
            time.sleep(2)
            pulse = pulsectl.Pulse("arctis-video-router")
            _subscribe(pulse)
            _last_native_check = 0.0
            _pa_placed.clear()
            _native_placed.clear()
            _move_times.clear()
            _pending_moves.clear()
        except Exception as e:
            log.error("Error: %s", e)
            time.sleep(1)


if __name__ == "__main__":
    main()
