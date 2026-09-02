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

from arctis_sound_manager import audio_reconfig
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

# Arctis virtual sinks the router repatriates from when the headset is off,
# and treats interchangeably wherever "is this app on an Arctis channel?" is
# asked. Arctis_Media used to be missing here (only Game/Chat were listed),
# which made repatriation asymmetric between channels.
ARCTIS_VIRTUAL_SINKS = {"Arctis_Game", "Arctis_Chat", "Arctis_Media", "effect_input.sonar"}

def _is_asm_channel(sink_name: str) -> bool:
    """True when *sink_name* is one of ASM's channels, not the hardware.

    Sitting on the headset's own sink is not the same as sitting on a
    channel. A stream there gets no EQ, no per-channel volume, no ChatMix
    and never appears in the mixer, because it never enters ASM's graph at
    all. It is also where a stream lands by default whenever the system
    default sink is the headset itself, so it carries no intent: it is
    where audio goes when nobody has decided anything.

    The two are easy to conflate because the hardware sink is named
    ``alsa_output.usb-SteelSeries_Arctis_Nova_Pro_Wireless-00.analog-stereo``
    — it contains ``Arctis_``, so a fragment test written to mean "is this
    app on one of our channels?" answers yes for the bare device. That is
    what left applications stranded on the hardware, outside every channel
    and absent from the mixer (reported on Discord by autune). The
    distinction already existed as :func:`_is_physical_arctis`; this is the
    same rule, stated from the other side.
    """
    if not sink_name or _is_physical_arctis(sink_name):
        return False
    return (any(k in sink_name for k in ARCTIS_VIRTUAL_SINKS)
            or sink_name.startswith("effect_input."))

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


# The virtual sink each channel's applications sit on. Defined here because
# the internal-node remap below is derived from it rather than hand-listed.
_CHANNEL_SINKS = {"game": "Arctis_Game", "chat": "Arctis_Chat",
                  "media": "Arctis_Media"}


# effect_input sinks are internal filter-chain nodes — apps should never
# target them directly. When one is seen as a manual move, save the channel
# the user actually meant, not the internal node.
#
# Derived rather than listed: the hand-written table only ever covered game
# and chat, so a manual move onto Media's EQ, Media's HeSuVi stage, the Output
# EQ or the micro EQ was saved verbatim — which is how effect_input.* values
# got into routing_overrides.json in the first place, the entries the prune
# below then has to clean up. The comment said "never save effect_input
# sinks"; the table only made that true for two of them.
_INTERNAL_SINK_REMAP = {
    **{f"effect_input.sonar-{channel}-eq": sink
       for channel, sink in _CHANNEL_SINKS.items()},
    # Each channel's HeSuVi stage sits downstream of its own channel sink.
    "effect_input.virtual-surround-7.1-hesuvi": "Arctis_Game",
    "effect_input.virtual-surround-7.1-hesuvi-media": "Arctis_Media",
}

# Internal nodes with no application-facing equivalent: the Output channel
# feeds an external device rather than a channel apps sit on, and the micro EQ
# is a capture node, not a sink. There is no honest override to save for
# either, so a manual move onto one is not recorded at all — better than
# inventing a target the user never chose.
_INTERNAL_SINKS_NOT_SAVEABLE = frozenset({
    "effect_input.sonar-output-eq",
    "effect_input.sonar-micro-eq",
})


def _override_target(sink_name: str) -> str | None:
    """The value to save for a manual move onto *sink_name*, or None.

    None means "do not record this move". A foreign ``effect_input.*`` — a
    filter-chain the user runs themselves — is returned unchanged: it is a
    legitimate destination, and the prune above keeps it for as long as it is
    in the graph.
    """
    if sink_name in _INTERNAL_SINKS_NOT_SAVEABLE:
        return None
    return _INTERNAL_SINK_REMAP.get(sink_name, sink_name)

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

    # ASM is rebuilding the audio graph right now (EQ change, profile switch,
    # Sonar toggle, "restart the audio engine"). The Arctis_* sinks go away for
    # several seconds and PipeWire parks whatever was on them elsewhere, which
    # looks exactly like a deliberate move and comfortably outlasts
    # _STABILITY_DELAY. Persisting it overwrote the user's channel with the
    # accident, and the reapply pass that follows the restart then enforced the
    # wrong channel for good: a momentary flicker turned into a permanent
    # reassignment. Drop the candidate rather than hold it, so the stability
    # timer restarts from zero once the window closes and a move the user
    # really did make during it still lands on the next tick.
    if audio_reconfig.in_progress():
        log.debug(
            "Audio reconfiguration in progress: ignoring move of '%s' -> %s",
            app, save_name,
        )
        _pending_moves.pop(key, None)
        return False

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


# routing_overrides.json accumulates entries for ever unless something prunes
# them: _reachable() (below) stops a dead override being *applied*, but
# nothing ever removed one, so the file only grows and every entry is walked
# on every replay (see the "Not restored" section of RAPPORT-CHAOS-ASM.md,
# which found "pw-cat": "effect_input.chaos-nan" left behind by a probe node
# that no longer existed).
#
# The obvious-looking prune — "the target sink isn't in the graph right now,
# drop it" — is wrong: a Bluetooth speaker that's off, a monitor that's
# unplugged, or the headset sitting in its case are all exactly the cases an
# override exists to remember, and they are absent from the graph just as
# often as something genuinely dead is. Presence right now says nothing
# about whether the entry is safe to forget.
#
# What *can* be judged from the value alone, with no live graph needed: every
# effect_input.* node this codebase ever creates comes from
# sonar_to_pipewire.py, and every one of them starts with one of the two
# prefixes below (checked against the whole tree, not just that file — see
# the grep in this fix's report). effect_input.* is also documented as
# internal-only immediately above (_EFFECT_REMAP): apps are never meant to
# target it directly, so a saved override that does is already an anomaly.
# A value in that namespace that matches neither prefix cannot be a node ASM
# itself would ever create — it can only be a leftover from some external
# process (a probe, a test tool, an impostor) that registered a node under
# that name for the lifetime of that one process. Unlike a Bluetooth
# speaker's MAC-derived name or a monitor's ALSA card path, that name is not
# a persistent device identity: nothing "comes back online" under it, so
# there is nothing to forget by dropping it. This is deliberately narrow —
# it says nothing about whether a *legitimate* effect_input.* target
# (sonar-media-eq mid filter-chain-restart, say) is safe to prune while
# briefly absent, and it doesn't try to: it is left alone, same as any other
# device-shaped value.
_ASM_EFFECT_PREFIXES = ("effect_input.sonar-", "effect_input.virtual-surround-")


def _is_dead_override_target(target, present_sinks) -> bool:
    """True when *target* is a filter-chain node that is gone for good.

    Two conditions, and both are needed.

    Structural: the value must be in the ``effect_input.*`` namespace.
    Everything else — Arctis_* channels, alsa_output.*/bluez_output.*
    hardware, other apps' virtual sinks — names a device identity that can be
    absent right now and come back: a Bluetooth speaker that is off, a monitor
    unplugged, a headset in its case. Those are exactly what an override
    exists to remember, and they are never pruned, however long they have been
    gone.

    Live: the node must also be missing from *present_sinks*. The structural
    test alone is not enough, because ``effect_input.<name>`` is the naming
    convention of PipeWire filter-chains in general, not just ASM's — a user
    running their own chain and routing an app onto it through ASM's UI would
    otherwise have that choice silently deleted on the next tick. A chain that
    is loaded is present in the graph; the probe nodes this prune exists for
    (``effect_input.chaos-nan`` and friends) are not, and never will be again.

    ASM's own nodes are exempt from the live half: they legitimately vanish
    while the filter-chain restarts, and the watchdog puts them back.
    """
    if not isinstance(target, str) or not target.startswith("effect_input."):
        return False
    if target.startswith(_ASM_EFFECT_PREFIXES):
        return False
    return target not in present_sinks


def _prune_dead_overrides(overrides: dict, present_sinks=()) -> tuple[dict, list[str]]:
    """Return (overrides with dead entries removed, keys that were dropped).

    Does not mutate *overrides*. *present_sinks* is the set of sink names in
    the graph this tick; see :func:`_is_dead_override_target` for why an entry
    needs to fail both a structural and a liveness test before it is dropped.
    """
    present = set(present_sinks)
    dead_keys = [key for key, target in overrides.items()
                if _is_dead_override_target(target, present)]
    if not dead_keys:
        return overrides, []
    pruned = {key: target for key, target in overrides.items()
             if key not in dead_keys}
    return pruned, dead_keys


def _sink_name(sinks, index: int) -> str | None:
    s = next((s for s in sinks if s.index == index), None)
    return s.name if s else None


CHANNEL_OUTPUTS_FILE = (Path.home() / ".config" / "arctis_manager"
                        / "channel_output_devices.json")

def live_channel_sinks(present_sinks: set[str]) -> set[str]:
    """The channel sinks that still reach a device while the headset is off.

    "Headset off means every Arctis channel is dead" was true when a channel
    could only ever come out of the headset. It stopped being true when
    channels got their own output devices: a Game channel pointed at a pair of
    earbuds plays perfectly well with the headset powered down, and the router
    treating it as dead is why a game launched in that state was left on the
    default sink, out of its channel and out of the mixer.

    Read from the same file the GUI writes and checked against the sinks that
    are actually in the graph, because a channel pointed at earbuds that are
    switched off is dead again — and so is one pointed back at the headset.
    """
    try:
        prefs = json.loads(CHANNEL_OUTPUTS_FILE.read_text())
    except (OSError, ValueError):
        return set()

    live = set()
    for channel, target in (prefs or {}).items():
        sink = _CHANNEL_SINKS.get(channel)
        if not sink or not target or _is_physical_arctis(target):
            continue
        if target in present_sinks:
            live.add(sink)
    return live


def _is_physical_arctis(sink_name: str) -> bool:
    """Return True for the physical Arctis hardware output.

    WirePlumber may restore a stream's target.object preference back to the
    physical output after our router moves it to a virtual channel.  We must
    not treat that as a deliberate user action and save it as an override.
    """
    return "SteelSeries_Arctis" in sink_name and not sink_name.startswith("Arctis_")


def _explicit_pin_target(props: dict, sink_map: dict) -> str | None:
    """Return the foreign virtual sink a stream is explicitly pinned to, or None.

    A stream that sets ``target.object`` / ``node.target`` to a *virtual*
    sink that is not one of ASM's own nodes is part of another app's routing
    graph — e.g. a soundboard feeding its mic-injection stream into a
    virtual-microphone sink (SoundDeck). Adopting or overriding such a
    stream doesn't just change where it is heard, it breaks the other app's
    feature entirely, so the router must leave it alone (and put it back if
    it was moved before this guard existed).

    Deliberately narrow: pins to hardware outputs (``alsa_output.*`` /
    ``bluez_output.*``) are NOT exempt — pulling audible streams from other
    physical outputs onto the headset is the router's advertised adoption
    behavior (issue #20). Pins to ASM's own sinks follow normal routing.
    A target that names no currently-present sink is ignored.

    Not exhaustive: ``target.object`` also accepts an ``object.serial``, and
    this only matches ``node.name``, so a stream pinned by serial is not
    recognised and is adopted as before. Resolving those would mean mapping
    PipeWire serials to nodes (the PulseAudio sink index is a different
    number), which is more machinery than the case seen in the wild warrants.
    Do not read this guard as covering every possible pin.
    """
    target = props.get("target.object") or props.get("node.target") or ""
    if not isinstance(target, str) or target not in sink_map:
        return None
    if target.startswith(("Arctis_", "effect_input.", "alsa_output.", "bluez_output.")):
        return None
    if _is_physical_arctis(target):
        return None
    return target


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
    # Which channels still lead somewhere audible with the headset down. A
    # channel with its own output device is not dead just because the headset
    # is, and everything below has to stop assuming otherwise.
    live_sinks = (live_channel_sinks({s.name for s in sinks})
                  if headset_power == HeadsetPower.OFF else set())
    if headset_power == HeadsetPower.OFF and not live_sinks:
        # Headset is off and no channel has an output of its own to fall back
        # on: its virtual sinks are effectively dead, so pull any stream still
        # parked on one of them onto the current default sink. This is a
        # transient move, not a user choice — never persisted as an override
        # (R5). When the headset comes back online the normal enforcement pass
        # below reapplies the saved override and brings the app back.
        #
        # Skip entirely when the default sink is ITSELF an Arctis sink — a
        # virtual channel (ARCTIS_VIRTUAL_SINKS) or the physical SteelSeries
        # output. Every Arctis channel is equally silent while the headset
        # is off, so "repatriating" to another Arctis channel goes nowhere
        # useful and only undoes the user's channel placement (regression:
        # a stream parked on Arctis_Media got bounced to Arctis_Game just
        # because that happened to be the system default).
        default_is_arctis = (
            any(k in default_sink_name for k in ARCTIS_VIRTUAL_SINKS)
            or _is_physical_arctis(default_sink_name)
        )
        if default_sink and not default_is_arctis:
            idx_to_name = {s.index: s.name for s in sinks}
            name_to_idx = {s.name: s.index for s in sinks}
            for si in pulse.sink_input_list():
                app = si.proplist.get("application.name", "")
                if not app:
                    continue
                if _explicit_pin_target(si.proplist, name_to_idx) is not None:
                    # Pinned to a foreign virtual sink (e.g. a virtual-mic
                    # feed) — unaffected by the headset being off.
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
    overrides, pruned_keys = _prune_dead_overrides(
        overrides, {s.name for s in sinks})
    if pruned_keys:
        log.warning(
            "Pruned %d routing override(s) pointing at filter-chain nodes that "
            "are not in the graph and are not ASM's: %s. Devices are never "
            "pruned — only a chain that is gone.",
            len(pruned_keys), ", ".join(pruned_keys),
        )
        save_overrides(overrides)
    sink_inputs = pulse.sink_input_list()
    sink_map = {s.name: s.index for s in sinks}
    sink_idx_to_name = {s.index: s.name for s in sinks}

    def _reachable(sink_name: str) -> bool:
        """Whether putting a stream on *sink_name* would be audible right now.

        Only ever False with the headset off, and only for the channels that
        have nowhere else to go: with a device of their own they are as alive
        as the headset ever made them. Enforcing an override onto a channel
        that leads to a powered-down headset would be moving audio into
        silence, which is what the whole headset-off branch exists to avoid.
        """
        if headset_power != HeadsetPower.OFF:
            return True
        if sink_name in live_sinks:
            return True
        return not (sink_name in ARCTIS_VIRTUAL_SINKS
                    or _is_physical_arctis(sink_name))

    if (headset_power == HeadsetPower.OFF and default_sink
            and _reachable(default_sink_name)):
        # Some channels are alive and some are not. Clear out only the dead
        # ones, and leave everything on a live channel exactly where it is.
        # Nothing to clear them onto when the default sink is itself the
        # silent headset, which is the state this machine is usually in.
        for si in sink_inputs:
            current = sink_idx_to_name.get(si.sink, "")
            if not si.proplist.get("application.name") or not current:
                continue
            if current in live_sinks or _reachable(current):
                continue
            if si.sink != default_sink.index:
                log.info("Headset off: '%s' -> %s (its channel leads nowhere)",
                         si.proplist.get("application.name"), default_sink_name)
                pulse.sink_input_move(si.index, default_sink.index)

    # ── PulseAudio streams ────────────────────────────────────────────────
    pa_now = time.monotonic()
    for si in sink_inputs:
        app = si.proplist.get("application.name", "")
        if not app:
            continue
        # Composite key (issue #108): disambiguates apps that share a
        # generic application.name such as "Chromium".
        key = app_override_key(app, si.proplist.get("application.process.binary", ""))

        # A stream explicitly pinned to a foreign virtual sink is off-limits
        # to every pass below — restore it if something already moved it.
        # Skipped before any _pa_placed bookkeeping so a same-named sibling
        # stream (e.g. SoundDeck's monitor stream) keeps its own tracking.
        pinned = _explicit_pin_target(si.proplist, sink_map)
        if pinned is not None:
            pinned_index = sink_map[pinned]
            # Undo only *our* displacement. Sitting on an ASM sink is the
            # evidence that the router put it there (before this guard
            # existed); anywhere else and the user moved it themselves from a
            # mixer, which is not ours to drag back. Without this test the
            # router would hold these streams tighter than any other, undoing
            # a deliberate manual move within a tick and offering no way out.
            current_name = sink_idx_to_name.get(si.sink, "")
            displaced_by_us = (
                any(k in current_name for k in ARCTIS_VIRTUAL_SINKS)
                or current_name.startswith("effect_input.")
            )
            if si.sink != pinned_index and displaced_by_us:
                log.info("Pinned stream: '%s' back -> %s", app, pinned)
                pulse.sink_input_move(si.index, pinned_index)
            continue

        # Detect manual move: app was placed by router but is now elsewhere
        if key in _pa_placed and si.sink != _pa_placed[key]:
            current_name = _sink_name(sinks, si.sink)
            if current_name:
                # Never save an internal filter-chain node as an override
                save_name = _override_target(current_name)
                if save_name is None:
                    # Nothing meaningful to remember (Output EQ, micro EQ):
                    # accept the placement so it stops being re-checked, but
                    # write no override.
                    _pa_placed[key] = si.sink
                elif _confirm_manual_move(key, app, save_name, overrides, now=pa_now):
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
                # the headset. Skipped if the stream is already on one of
                # ASM's channels (virtual or filter-chain) so manual moves
                # are preserved — the headset's own sink is NOT one of
                # them (see _is_asm_channel), which is what used to leave
                # everything the heuristics don't name stranded on the
                # hardware, outside every channel and absent from the mixer.
                # A deliberate placement there is still safe: it is saved as
                # an override by _confirm_manual_move, and this branch only
                # runs for streams that have none.
                current_name = sink_idx_to_name.get(si.sink, "")
                if current_name and not _is_asm_channel(current_name):
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

        if wanted is not None and not _reachable(wanted):
            # The override names a channel that leads to a headset which is
            # off. Leave the stream where it is rather than moving it into
            # silence; the next tick with the headset on puts it back.
            _pa_placed[key] = si.sink
        elif wanted is not None:
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

        # Same foreign-virtual-sink pin guard as the PA pass above, including
        # the "only undo our own displacement" rule.
        pinned = _explicit_pin_target(s.get("props", {}), sink_map)
        if pinned is not None:
            current_name = s["sink_name"] or ""
            displaced_by_us = (
                any(k in current_name for k in ARCTIS_VIRTUAL_SINKS)
                or current_name.startswith("effect_input.")
            )
            if current_name != pinned and displaced_by_us:
                log.info("Pinned native stream: '%s' back -> %s", app, pinned)
                move_native_stream(s["id"], pinned)
            continue

        # Detect manual move for native streams
        if key in _native_placed:
            placed = _native_placed[key]
            current = s["sink_name"]
            if current and current != placed:
                # Never save an internal filter-chain node as an override
                save_name = _override_target(current)
                if save_name is None:
                    _native_placed[key] = current
                elif _confirm_manual_move(key, app, save_name, overrides, now=now):
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
                # native PW stream playing outside ASM's channels while
                # Arctis is default → move to Arctis_Media. Skip when the
                # stream is already on one of our channels (manual
                # placement preserved). Same correction as above: the
                # headset's own sink is a device, not a channel.
                current = s.get("sink_name") or ""
                if current and not _is_asm_channel(current):
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

        if wanted is not None and not _reachable(wanted):
            # Same as the PA path: a channel that leads to a headset which is
            # off is not somewhere to move audio to.
            _native_placed[key] = s["sink_name"]
        elif wanted is not None:
            if s["sink_name"] is None or s["sink_name"] != wanted:
                log.info("Override native: moving '%s' -> %s", app, wanted)
                move_native_stream(s["id"], wanted)
                _native_placed[key] = wanted
            else:
                _native_placed[key] = s["sink_name"]
            continue

    # A channel's output device is *not* enforced here, deliberately.
    #
    # This used to walk the streams sitting on a channel's virtual sink and move
    # them onto the chosen device. Two things were wrong with it. The first is
    # that it fought the block above in the same tick: the override says "Chrome
    # belongs on Arctis_Media", this said "anything on Arctis_Media belongs on
    # the earbuds", and each undid the other on every pass — the log showed the
    # pair one second apart, and the device menu appeared to do nothing at all.
    #
    # The second is that winning was no better than losing. Dragging a stream
    # off Arctis_Media takes it out of that channel entirely, past the Sonar EQ
    # and the HeSuVi stage, which is the whole reason the channel exists.
    #
    # Sending a channel somewhere means moving the *channel's* last link, not
    # its applications: `sonar_to_pipewire.ensure_physical_output_links` points
    # each channel's HeSuVi output at `channel_destination(channel)`, and Media
    # has its own HeSuVi instance so it can differ from Game. That is where this
    # belongs and where it already works.
    #
    # #177 landed on develop while this branch was open and reached for the
    # other half of the same problem: it made the override pass resolve through
    # the channel-output mapping too, so the two passes agree instead of
    # fighting. That does stop the oscillation — but both passes then agree on
    # dragging the stream off its channel, which is the second problem above.
    # The link-level fix below covers the oscillation as well, so the resolve
    # step #177 added has nothing left to do here.


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
