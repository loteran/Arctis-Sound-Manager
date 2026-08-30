# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
PipeWire utilities for native (non-PulseAudio) stream management.
Used to detect and move apps like mpv/haruna that bypass PulseAudio.
"""
import json
import logging
import shutil
import subprocess
import time
from pathlib import Path

logger = logging.getLogger("pw_utils")

OVERRIDES_FILE = Path.home() / ".config" / "arctis_manager" / "routing_overrides.json"

# --- Safe subprocess spawning (issue #123) --------------------------------
# The daemon runs libusb device I/O and these PipeWire CLI spawns in the same
# asyncio thread pool. CPython's subprocess only takes the posix_spawn (vfork)
# path when the executable is an *absolute* path AND close_fds is False;
# otherwise it falls back to fork()+exec. fork() replays libusb's
# pthread_atfork handlers and COW-copies the whole address space while a
# sibling thread is parked inside libusb poll() — a documented, nondeterministic
# heap-corruption vector for multithreaded programs using libusb (random bogus
# TypeErrors + SIGSEGV in PyObject_IsTrue). posix_spawn/vfork skips both, so we
# pin every PipeWire spawn to it and the daemon never fork()s from its
# libusb-active process.
_ABS_EXE_CACHE: dict[str, str] = {}


def _abs_exe(name: str) -> str:
    """Resolve a CLI tool to its absolute path (cached), so subprocess can use
    the posix_spawn path. Falls back to the bare name if not on PATH."""
    if name not in _ABS_EXE_CACHE:
        _ABS_EXE_CACHE[name] = shutil.which(name) or name
    return _ABS_EXE_CACHE[name]


def _pw_run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
    """subprocess.run pinned to the posix_spawn path for PipeWire CLI tools.

    Resolves argv[0] to an absolute path and forces close_fds=False so the
    daemon never fork()s from its libusb-active process (issue #123).
    close_fds=False is safe here: PipeWire CLI tools are short-lived and every
    fd the daemon holds (libusb, D-Bus, sockets) is opened O_CLOEXEC, so nothing
    leaks past exec.
    """
    resolved = [_abs_exe(argv[0]), *argv[1:]]
    kwargs.setdefault("close_fds", False)
    return subprocess.run(resolved, **kwargs)

_LINK_DENIED = "not permitted"
# When each (out, in) pair was last repaired. Cleared on every daemon start.
#
# This used to be a set — one attempt per pair, for the lifetime of the daemon
# — which quietly ruled out the case it was written for. Ports keep their ids
# for as long as their loopback lives, so once the first attempt had been made
# a channel could never be repaired again: if the grant failed for a reason
# that passes (the session manager still starting, a transient pw-cli error),
# it stayed failed, and the link stayed refused for the rest of the session.
# Retry, but no more often than this, so a system where the grant genuinely
# cannot succeed is not hammered.
_PERM_REPAIR_RETRY_S = 60.0
_perm_repair_attempted: dict[tuple[int, int], float] = {}
# Same debounce, keyed by node.name, for the Props (set-param) repair below —
# ids are ephemeral across filter-chain restarts, the name is not.
_perm_repair_attempted_props: dict[str, float] = {}


# Node names ASM creates itself. Only the clients behind these are ever
# granted anything: raising permissions is not something to do on a client we
# do not own, even within the user's own session.
_ASM_OWNED_NODES = ("Arctis_", "effect_input.sonar-", "effect_output.sonar-",
                    "effect_input.virtual-surround", "effect_output.virtual-surround")


def _node_owner(node_id: int, dump: list | None = None) -> tuple[str | None, str]:
    """(owning client id, node name) for *node_id*.

    The client is None when the daemon owns the node, which also means the
    permission check does not apply to it.
    """
    data = dump if dump is not None else _pw_dump()
    for obj in data:
        if obj.get("type") != "PipeWire:Interface:Node" or obj.get("id") != node_id:
            continue
        props = (obj.get("info") or {}).get("props") or {}
        owner = props.get("client.id")
        return (str(owner) if owner is not None else None,
                props.get("node.name", ""))
    return None, ""


def _port_owner(port_id: int, dump: list | None = None) -> tuple[str | None, str]:
    """(owning client id, node name) for the node a port belongs to."""
    data = dump if dump is not None else _pw_dump()
    for obj in data:
        if obj.get("type") != "PipeWire:Interface:Port" or obj.get("id") != port_id:
            continue
        node_id = ((obj.get("info") or {}).get("props") or {}).get("node.id")
        if node_id is None:
            return None, ""
        try:
            return _node_owner(int(node_id), data)
        except (TypeError, ValueError):
            return None, ""
    return None, ""


def grant_link_permissions(out_port: int, in_port: int) -> bool:
    """Give the clients owning these two ports permission to link (#181).

    PipeWire refuses a link when the client owning one end cannot *see* the
    node at the other end. On most systems the session manager grants that and
    nobody notices, but where clients come up as ``access=restricted`` — seen
    on SteamOS — every link ASM needs is denied, the channels reach nothing,
    and there is no audio. The user hits the same wall running ``pw-link`` by
    hand, so it is not something they can work around either.

    ``pw-cli`` connects to the manager socket, which is unrestricted, so it can
    raise permissions for the clients we own. The ``l`` in ``rwxml`` is the
    flag that specifically allows linking and is *not* part of ``rwxm``:
    omitting it is why such a fix appears to do nothing.

    The ``--`` matters as much as the flags. ``-1`` means "every object", but
    pw-cli runs its arguments through getopt first, which reads it as an option
    and dies with ``invalid option -- '1'`` before the command is ever parsed.
    Without the separator this whole repair could not run at all, on any
    system: it failed instantly, in a debug-level log nobody sees, and the
    caller went on retrying a link that stayed refused (#181).

    Deliberately narrow. Only the two clients at the ends of a link ASM was
    already trying to make are touched, at most once a minute per port pair,
    and only after a refusal — never pre-emptively. Returns True when something
    was granted and the caller should retry the link.
    """
    key = (out_port, in_port)
    now = time.monotonic()
    last = _perm_repair_attempted.get(key)
    if last is not None and now - last < _PERM_REPAIR_RETRY_S:
        return False
    _perm_repair_attempted[key] = now

    if shutil.which("pw-cli") is None:
        return False
    dump = _pw_dump()

    # Only ever raise permissions for clients behind nodes ASM created. The
    # other end of a link is often the physical sink, owned by WirePlumber:
    # granting it anything would be an elevation on a client we do not own,
    # which is not ours to do even inside the user's own session.
    owners: set[str] = set()
    for port in (out_port, in_port):
        owner, node_name = _port_owner(port, dump)
        if owner is None:
            # Daemon-owned: exempt from the check, nothing to grant.
            continue
        if not node_name.startswith(_ASM_OWNED_NODES):
            logger.debug("not raising permissions for client %s (owns %r, not ours)",
                         owner, node_name)
            continue
        owners.add(owner)
    if not owners:
        return False

    granted = _grant_owners_rwxml(owners, context="link")
    if granted:
        logger.warning(
            "Link was refused by PipeWire; granted link permission to client(s) %s "
            "and retrying. This system starts our clients restricted (#181).",
            ", ".join(sorted(owners)),
        )
    return granted


def _grant_owners_rwxml(owners: set[str], context: str) -> bool:
    """Raise each of *owners* to ``rwxml`` on every object. Shared by
    :func:`grant_link_permissions` and :func:`grant_props_permissions`.

    Returns True if at least one owner was actually granted — pw-cli reports
    a failed grant on stderr while still exiting 0, so the exit status alone
    is not trusted (that previously made a refused grant look like a success
    and the caller would retry the link/set-param for nothing).
    """
    granted = False
    for owner in sorted(owners):
        try:
            r = _pw_run(["pw-cli", "--", "permissions", owner, "-1", "rwxml"],
                        check=False, timeout=5, capture_output=True)
        except Exception as exc:
            logger.warning("could not grant %s permissions to client %s: %r",
                           context, owner, exc)
            continue
        err = (r.stderr or b"").decode(errors="replace").strip()
        if r.returncode == 0 and not err:
            granted = True
        else:
            logger.warning("pw-cli permissions %s failed: %s", owner,
                           err or f"exit status {r.returncode}")
    return granted


def grant_props_permissions(node_name: str, dump: list | None = None) -> bool:
    """Give the client owning *node_name* permission to have its Props set (#181).

    Same failure family as :func:`grant_link_permissions`, on the same class
    of system (clients coming up ``access=restricted``, seen on SteamOS), but
    hit from a different call site: ``pw-cli set-param`` on a filter-chain
    node (the Sonar EQ live-apply path in ``set_filter_controls``) rather than
    ``pw-link``. Both are refused with the same "Operation not permitted",
    and the fix that unblocked linking — raising the *owning* client to
    ``rwxml`` on ``-1`` and retrying — is the one already confirmed against
    real hardware for the link case; nothing here has been confirmed for
    Props yet, so treat a persistent failure after this repair as new
    information, not as proof the repair itself is wrong.

    Only ever raises permissions for a client behind a node ASM created
    (same guard as the link case) and no more than once a minute per node
    name, so a system where the grant genuinely does not help is not
    hammered with retries.
    """
    now = time.monotonic()
    last = _perm_repair_attempted_props.get(node_name)
    if last is not None and now - last < _PERM_REPAIR_RETRY_S:
        return False
    _perm_repair_attempted_props[node_name] = now

    if shutil.which("pw-cli") is None:
        return False
    data = dump if dump is not None else _pw_dump()

    if not node_name.startswith(_ASM_OWNED_NODES):
        logger.debug("not raising Props permissions for %r (not ours)", node_name)
        return False

    node_id: int | None = None
    for obj in data:
        if obj.get("type") != "PipeWire:Interface:Node":
            continue
        if ((obj.get("info") or {}).get("props") or {}).get("node.name") == node_name:
            node_id = obj["id"]
            break
    if node_id is None:
        return False

    owner, _ = _node_owner(node_id, data)
    if owner is None:
        return False

    granted = _grant_owners_rwxml({owner}, context="props")
    if granted:
        logger.warning(
            "set-param on '%s' was refused by PipeWire; granted permission to "
            "client %s and retrying. This system starts our clients restricted "
            "(#181).", node_name, owner,
        )
    return granted


def apply_force_quantum(quantum: int) -> bool:
    """Set PipeWire's forced quantum (buffer size); 0 releases it.

    The HeSuVi surround chain runs 14 convolvers per sink, and with a large
    HRIR it can miss PipeWire's deadline under ordinary desktop load — audible
    as random crackling with nothing in the UI to explain it. A larger quantum
    gives each cycle more time and makes those xruns stop, at the cost of
    proportionally more latency (#183).

    This is a **global** PipeWire setting: it applies to every application on
    the system, not just ASM's chain. That is why the setting behind it
    defaults to off and is the user's call, and why 0 must be written back on
    the way out rather than left behind for the rest of the session.

    Returns True when the value was written (or when there was nothing to do).
    """
    if shutil.which("pw-metadata") is None:
        logger.debug("pw-metadata not found — cannot set clock.force-quantum")
        return False
    try:
        result = _pw_run(
            ["pw-metadata", "-n", "settings", "0", "clock.force-quantum", str(int(quantum))],
            capture_output=True, text=True, timeout=5,
        )
    except Exception as e:
        logger.warning("Could not set clock.force-quantum=%s: %r", quantum, e)
        return False
    if result.returncode != 0:
        logger.warning("pw-metadata clock.force-quantum=%s failed: %s",
                       quantum, (result.stderr or "").strip())
        return False
    if quantum:
        logger.info("Stability mode: forcing PipeWire quantum to %d (system-wide)", quantum)
    else:
        logger.info("Stability mode off: released PipeWire's forced quantum")
    return True


def get_xrun_counts(name_fragments: tuple[str, ...]) -> dict[str, int]:
    """Cumulative xrun counters for our own nodes, from ``pw-top -b -n 1``.

    An xrun is the graph missing its deadline: the convolvers did not finish in
    time and the user hears a click. The counter is monotonic for the lifetime
    of the node, so callers compare successive samples rather than absolute
    values.

    Only nodes whose name contains one of *name_fragments* are returned — we
    diagnose our own chain, and someone else's DAW dropping frames is not ours
    to report on. Returns {} when pw-top is unavailable or its output cannot be
    parsed; callers treat that as "no information", never as "no xruns".
    """
    if shutil.which("pw-top") is None:
        return {}
    try:
        # -b batch mode, one iteration. The first pass prints counters as they
        # stand, which is all we need since we diff across calls.
        result = _pw_run(["pw-top", "-b", "-n", "1"],
                         capture_output=True, text=True, timeout=10)
    except Exception as e:
        logger.debug("pw-top failed: %r", e)
        return {}
    if result.returncode != 0:
        return {}

    counts: dict[str, int] = {}
    for line in (result.stdout or "").splitlines():
        fields = line.split()
        # Layout: S ID QUANT RATE WAIT BUSY W/Q B/Q ERR FORMAT NAME…
        # ERR is the xrun counter, NAME is everything past the format column.
        if len(fields) < 10:
            continue
        name = fields[-1]
        if not any(frag in name for frag in name_fragments):
            continue
        try:
            counts[name] = int(fields[8])
        except (ValueError, IndexError):
            continue
    return counts


# effect_input sinks are internal filter-chain nodes — apps should never
# target them directly. Remap to the corresponding Arctis virtual sink so a
# stale override still lands the app on a real, user-facing destination.
_EFFECT_REMAP = {
    "effect_input.sonar-game-eq": "Arctis_Game",
    "effect_input.sonar-chat-eq": "Arctis_Chat",
    "effect_input.sonar-media-eq": "Arctis_Media",
}

# application.name is often a generic audio-engine label shared by several
# unrelated Electron apps (e.g. Vesktop and Pear Desktop both report
# "Chromium") rather than the actual program name. Keep in sync with
# gui/home_page.py HomePage._GENERIC_APP_NAMES, which drives the same
# disambiguation for the GUI's app tags.
_GENERIC_APP_NAMES = {
    "WEBRTC VoiceEngine", "AudioStream", "Playback", "audio stream",
    "Chromium", "cras", "libcanberra", "speech-dispatcher",
}


def app_override_key(name: str, binary: str) -> str:
    """Return the dict key used to index routing overrides for a stream.

    ``application.name`` alone is not a reliable identity for apps that
    report a generic audio-engine label (issue #108): two unrelated Electron
    apps (e.g. Vesktop and Pear Desktop) can both set ``application.name`` to
    "Chromium", so keying overrides on that name alone made the router treat
    them as a single app and bounce them between each other's targets.

    When *name* is one of those generic names and *binary* is known, return a
    composite ``"name|binary"`` key so each app gets its own override entry.
    Otherwise return *name* unchanged — this is also the legacy key format
    already used in ``routing_overrides.json`` for every non-generic app.
    """
    if name in _GENERIC_APP_NAMES and binary:
        return f"{name}|{binary}"
    return name


STEELSERIES_VENDOR_ID = "0x1038"


def is_external_output_sink(sink, allow_headset: bool = False) -> bool:
    """Return True if *sink* is a hardware output the user can route a channel to.

    A selectable external output is a real playback device that is not one of
    ASM's own virtual/EQ nodes. Previously only ``alsa_output.*`` sinks
    qualified, which silently hid every Bluetooth speaker/headphone — those
    appear as ``bluez_output.*`` (issue #134). Both node-name families are
    accepted here so the whole app (home page combos, tray routing menu, Sonar
    output override) lists Bluetooth devices consistently.

    *allow_headset* separates the two questions this predicate used to conflate:

    * **What may ASM pick on its own?** The headset must stay excluded. It is
      already the destination of the Game/Chat/Media chains, so auto-selecting
      it as the "external" output would silently duplicate a route the user
      never asked for. This is the default, hence ``allow_headset=False``.
    * **What may the user pick deliberately?** The headset belongs in the list.
      Routing the Output channel to it is a legitimate, requested setup: it
      gives a second path to the headset with its own (typically flat) EQ and
      no spatial processing — useful for video editing or music production
      without swapping EQ profiles (issue #139). The user's routing choice is
      sovereign; only the automatic default stays conservative.

    Accepts any object exposing ``.name`` and a ``.proplist`` mapping, so it
    works with ``pulsectl`` sink objects directly.
    """
    name = getattr(sink, "name", "") or ""
    if not (name.startswith("alsa_output") or name.startswith("bluez_output")):
        return False
    if allow_headset:
        return True
    if "SteelSeries" in name:
        return False
    proplist = getattr(sink, "proplist", None) or {}
    if proplist.get("device.vendor.id", "") == STEELSERIES_VENDOR_ID:
        return False
    return True


def _load_overrides() -> dict:
    if OVERRIDES_FILE.exists():
        try:
            return json.loads(OVERRIDES_FILE.read_text())
        except Exception:
            pass
    return {}


def reapply_routing_overrides(timeout_s: float = 6.0) -> int:
    """Re-apply saved routing overrides after a filter-chain restart.

    When the filter-chain service restarts (EQ preset / profile / mode change),
    the ``effect_input.sonar-*-eq`` nodes — and the ``Arctis_*`` pw-loopback
    sinks that feed them — disappear and reappear with new PipeWire IDs. Apps
    such as Discord (Electron) do not re-enumerate their sink when this happens
    and can fall silent or fall back to the physical output until manually
    reconnected.

    This walks ``routing_overrides.json`` ({app_name: sink_name}), waits (with
    retry up to *timeout_s*) for the target virtual sinks to reappear, then
    moves each app's live PulseAudio sink-input back onto its intended sink.

    It is idempotent and safe to call even when ``asm-router`` is also running:
    moving a stream that is already on the right sink is a no-op. Returns the
    number of streams that were moved.

    Errors (pulsectl missing, sink never returns, …) are logged and skipped so
    the caller is never broken by audio-routing issues.
    """
    overrides = _load_overrides()
    if not overrides:
        return 0

    try:
        import pulsectl  # type: ignore
    except Exception as exc:
        logger.debug("pulsectl unavailable, cannot reapply overrides: %s", exc)
        return 0

    # Resolve each override to the real sink name we want the app on, then wait
    # for those sinks to exist again before attempting any move.
    wanted_sinks = {_EFFECT_REMAP.get(name, name) for name in overrides.values()}

    moved = 0
    try:
        with pulsectl.Pulse("asm-reapply-overrides") as pulse:
            deadline = time.monotonic() + timeout_s
            sinks: list = []
            while True:
                sinks = pulse.sink_list()
                present = {s.name for s in sinks}
                # Only wait on Arctis virtual sinks; a physical/external target
                # that genuinely no longer exists must not block the retry loop.
                pending = {
                    n for n in wanted_sinks
                    if n.startswith("Arctis_") and n not in present
                }
                if not pending or time.monotonic() >= deadline:
                    if pending:
                        logger.warning(
                            "Virtual sinks did not reappear in %.1fs: %s",
                            timeout_s, ", ".join(sorted(pending)),
                        )
                    break
                time.sleep(0.2)

            name_to_index = {s.name: s.index for s in sinks}
            sink_inputs = pulse.sink_input_list()

            for app_key, sink_name in overrides.items():
                target_name = _EFFECT_REMAP.get(sink_name, sink_name)
                target_idx = name_to_index.get(target_name)
                if target_idx is None:
                    logger.warning(
                        "Override target '%s' for '%s' not found — skipping",
                        target_name, app_key,
                    )
                    continue
                # Composite key (issue #108): "name|binary" disambiguates apps
                # that share a generic application.name (e.g. two "Chromium"
                # Electron apps). Legacy keys (no "|") have no binary part.
                app_name, sep, app_binary = app_key.partition("|")
                for si in sink_inputs:
                    si_app = si.proplist.get("application.name", "")
                    si_binary = si.proplist.get("application.process.binary", "")
                    if sep:
                        if si_app != app_name or (app_binary and si_binary != app_binary):
                            continue
                    else:
                        # Match on application.name first; fall back to
                        # application.process.binary for Electron apps
                        # (Discord, Slack, …) that set application.name to
                        # their internal WebRTC node name rather than the
                        # product name.
                        if si_app != app_key and si_binary != app_key:
                            continue
                    if si.sink == target_idx:
                        continue
                    try:
                        pulse.sink_input_move(si.index, target_idx)
                        moved += 1
                        logger.info(
                            "Reapplied override: '%s' -> %s", app_key, target_name,
                        )
                    except Exception as exc:
                        logger.warning(
                            "Failed to move '%s' -> %s: %s",
                            app_key, target_name, exc,
                        )
    except Exception as exc:
        logger.warning("reapply_routing_overrides failed: %s", exc)

    return moved


def _parse_pw_dump_output(text: str) -> list | None:
    """Recover a usable dump from ``pw-dump`` output that failed a plain
    ``json.loads`` (issue: random ASM audio dropouts, diagnosed on PipeWire
    1.0.5, Aug 2026).

    A single non-monitor ``pw-dump`` invocation is documented as printing one
    JSON array, but in practice it can print **more than one**, concatenated
    back-to-back with no separator, when a registry object is added/removed
    while pw-dump is still enumerating the graph. The extra document observed
    here is a tiny one-element "tombstone" array like ``[{"id": N, "info":
    null}]`` — and it can appear *before or after* the real dump, so the
    real dump is not reliably "the first document".

    ``json.loads`` treats any trailing bytes after the first valid document
    as a hard parse error ("Extra data"), so ``_pw_dump()`` used to return
    ``[]`` on every one of these — an apparently *empty* PipeWire graph. The
    loopback watchdog (``core.py: _loopback_watchdog``) reads an empty dump
    as "every loopback/EQ target is gone" and escalates to recreating
    loopbacks and restarting the filter-chain service — tearing down and
    rebuilding the exact audio path that was actually fine. That churn is
    the mechanism behind ASM's random audio dropouts, not any real failure
    of PipeWire or the headset.

    This walks every concatenated JSON document in the output via
    ``JSONDecoder.raw_decode`` and returns the largest list (by object
    count) — the real dump always has hundreds of entries; a tombstone has
    exactly one. Returns ``None`` if nothing parseable was found at all.
    """
    decoder = json.JSONDecoder()
    idx = 0
    n = len(text)
    best: list | None = None
    while idx < n:
        while idx < n and text[idx] in " \t\r\n":
            idx += 1
        if idx >= n:
            break
        try:
            obj, end = decoder.raw_decode(text, idx)
        except Exception:
            break
        if isinstance(obj, list) and (best is None or len(obj) > len(best)):
            best = obj
        idx = end
    return best


def pw_dump_or_none() -> list | None:
    """Read the PipeWire graph, or ``None`` when it could not be read.

    "The graph is empty" and "I could not read the graph" are different
    facts, and conflating them is expensive (CHA-11). A pw-dump slower than
    the timeout — heavy load, a stalled session manager, a very large graph
    — used to come back as ``[]``, which the loopback watchdog reads as
    "every loopback and EQ target is gone" and answers by recreating the
    loopbacks and restarting the filter-chain: it tears down the audio path
    that was actually fine. That churn is the mechanism behind ASM's random
    audio dropouts, and one producer of an empty dump (concatenated JSON
    documents) was already fixed for exactly this reason — the timeout, the
    non-zero exit and a pw-dump missing from PATH were not.

    Callers that decide whether to intervene must use this function and treat
    ``None`` as "no information, do nothing this tick", the way
    ``get_xrun_counts`` already does. :func:`_pw_dump` keeps the old
    empty-list contract for read-only lookups, where a missing node is
    indistinguishable from an unreadable graph anyway.
    """
    try:
        r = _pw_run(["pw-dump"], capture_output=True, text=True, timeout=3)
        try:
            return json.loads(r.stdout)
        except json.JSONDecodeError:
            recovered = _parse_pw_dump_output(r.stdout)
            if recovered is not None:
                logger.info(
                    "pw-dump: output had concatenated JSON documents "
                    "(%d bytes) — recovered the real dump (%d objects) "
                    "instead of treating the graph as empty",
                    len(r.stdout), len(recovered),
                )
                return recovered
            raise
    except Exception as e:
        logger.warning("pw-dump failed: %s", e)
        return None


def _pw_dump() -> list:
    """:func:`pw_dump_or_none` with an unreadable graph flattened to ``[]``."""
    data = pw_dump_or_none()
    return data if data is not None else []


def quiesce_filter_chain() -> int:
    """Unlink ASM's filter-chain nodes so the graph stops processing audio.

    Call this right before stopping/restarting the filter-chain service. PipeWire
    1.6.7 segfaults when the filter-chain process is terminated *while it is
    processing a cycle* (issue #100): the crash is in the audio thread, not in the
    config. Dropping the links first parks the nodes, so the SIGTERM lands on an
    idle process.

    The restart tears these links down anyway — doing it ourselves, a moment
    earlier, only removes the race. ASM's loopback watchdog (ensure_loopback_link)
    rebuilds them once the new nodes appear.

    Returns the number of links destroyed.
    """
    data = _pw_dump()
    if not data:
        return 0

    fc_nodes = {
        o["id"]
        for o in data
        if o.get("type") == "PipeWire:Interface:Node"
        and str(
            (o.get("info", {}).get("props") or {}).get("node.name", "")
        ).startswith(("effect_input.", "effect_output."))
    }
    if not fc_nodes:
        return 0

    destroyed = 0
    for o in data:
        if o.get("type") != "PipeWire:Interface:Link":
            continue
        info = o.get("info", {}) or {}
        if info.get("output-node-id") in fc_nodes or info.get("input-node-id") in fc_nodes:
            try:
                _pw_run(["pw-cli", "destroy", str(o["id"])], capture_output=True, timeout=2)
                destroyed += 1
            except Exception as exc:  # link may already be gone — harmless
                logger.debug("quiesce_filter_chain: destroy %s failed: %s", o["id"], exc)

    if destroyed:
        logger.info("quiesce_filter_chain: unlinked %d link(s) before restart", destroyed)
    return destroyed


def set_filter_controls(node_name: str, controls: dict[str, float]) -> bool:
    """Live-apply filter-chain control values in one shot, no restart required.

    *node_name* is the ``node.name`` of the filter-chain's own outer node
    (e.g. ``"effect_input.sonar-game-eq"``) — the individual biquad/LADSPA
    nodes declared inside its ``filter.graph`` (``bq0``, ``macro_basses``,
    ``boost``, …) are NOT separate PipeWire objects with their own id; their
    controls are exposed as ``Props`` params on this one outer node, each
    addressed as ``"<internal-name>:<Control>"`` (e.g. ``"bq0:Gain"``,
    ``"macro_basses:Gain"``, ``"boost:Gain"``).

    Verified against a live Sonar EQ node on PipeWire 1.6.7::

        $ pw-cli enum-params <id> Props | grep bq0
              String "bq0:Freq" / "bq0:Q" / "bq0:Gain" ...
        $ pw-cli set-param <id> Props '{ params = [ "bq0:Freq" 31.25 "bq0:Q" 0.707 "bq0:Gain" 6.0 ] }'
        # readback via enum-params confirms every value took effect immediately

    This sidesteps the SIGTERM-during-DSP race that SEGVs filter-chain on
    PipeWire 1.6.7 when a control value change is instead applied by
    regenerating the config and restarting the service (issue #100/#88).

    The whole batch goes in a single ``set-param``, and the node is resolved
    once for it. That matters: a curve edit routinely moves several controls
    at a time (deleting a band shifts every band below it down a rack slot,
    three controls each, doubled on the stereo channels), and a dump plus a
    ``pw-cli`` spawn per control turned a 25 ms apply into a second and a
    half of "pending" — long enough for the next edit to land on top of it.

    Parameters
    ----------
    node_name:
        ``node.name`` of the filter-chain's capture-side node, resolved to a
        PipeWire object id via a fresh ``pw-dump`` (Correctif — issue #123:
        the lookup goes through ``_pw_dump()``/``_pw_run()``, never a raw
        subprocess spawn).
    controls:
        ``{control_key: value}``, e.g. ``{"bq3:Gain": -2.0, "bq3:Freq": 900.0}``.
        An empty mapping is a no-op and succeeds.

    Returns
    -------
    bool
        True when *node_name* was found in the graph and ``pw-cli set-param``
        exited 0 (after one permission-repair retry if the first attempt was
        refused — see :func:`grant_props_permissions`, #181). False when the
        node is not currently present (filter-chain not up yet, or these
        controls belong to a node that does not exist in the running graph)
        or the call still failed after that retry — the caller must treat
        this as a genuine failure, not silently report success, since the
        conf on disk and the running graph now disagree.
    """
    if not controls:
        return True

    data = _pw_dump()
    node_id: int | None = None
    for obj in data:
        if not obj.get("type", "").endswith("Node"):
            continue
        props = obj.get("info", {}).get("props", {})
        if props.get("node.name") == node_name:
            node_id = obj["id"]
            break

    if node_id is None:
        logger.debug("set_filter_controls: node '%s' not in graph", node_name)
        return False

    params = " ".join(f'"{key}" {value}' for key, value in controls.items())
    try:
        r = _pw_run(
            ["pw-cli", "set-param", str(node_id), "Props",
             f'{{ params = [ {params} ] }}'],
            capture_output=True, text=True, timeout=3,
        )
        err = (r.stderr or "").strip()

        # Same failure family as ensure_loopback_link (#181): on a system
        # that starts our clients restricted, PipeWire refuses set-param the
        # same way it refuses pw-link. Raise the owning client's permission
        # once and retry before giving up — see grant_props_permissions().
        if r.returncode != 0 and _LINK_DENIED in err.lower():
            if grant_props_permissions(node_name, data):
                r = _pw_run(
                    ["pw-cli", "set-param", str(node_id), "Props",
                     f'{{ params = [ {params} ] }}'],
                    capture_output=True, text=True, timeout=3,
                )
                err = (r.stderr or "").strip()

        if r.returncode != 0:
            logger.warning(
                "set_filter_controls: pw-cli set-param on '%s' (%s) failed: %s",
                node_name, params, err,
            )
            return False
        return True
    except Exception as exc:
        logger.warning(
            "set_filter_controls: exception setting '%s' (%s): %s",
            node_name, params, exc,
        )
        return False


def set_filter_gain(node_name: str, control: str, value: float) -> bool:
    """Live-apply one filter-chain control. See :func:`set_filter_controls`."""
    return set_filter_controls(node_name, {control: value})


def pw_node_exists(name: str, data: list | None = None) -> bool:
    """Return True if a PipeWire node with ``node.name == name`` is currently
    present in the graph.

    Used by the loopback watchdog (Correctif 3, issue #88) to detect when the
    filter-chain EQ nodes have disappeared (dead or crash-looping filter-chain
    service) so the watchdog can call ``ensure_filter_chain_healthy()`` instead
    of endlessly recreating orphan loopbacks to a non-existent target.

    Parameters
    ----------
    name:
        ``node.name`` to search for (e.g. ``"effect_input.sonar-game-eq"``).
    data:
        Optional pre-fetched ``pw-dump`` payload.  When *None*, a fresh
        ``pw-dump`` is executed.
    """
    if data is None:
        data = _pw_dump()
    for obj in data:
        if not obj.get("type", "").endswith("Node"):
            continue
        props = obj.get("info", {}).get("props", {})
        if props.get("node.name") == name:
            return True
    return False


def get_native_streams(data: list | None = None) -> list[dict]:
    """
    Return native PipeWire audio output streams (not PulseAudio clients).
    Each entry: {id, app_name, pid, sink_name, sink_id}
    """
    if data is None:
        data = _pw_dump()

    # Build maps
    sinks: dict[int, str] = {}          # node-id -> node.name
    streams: dict[int, dict] = {}       # node-id -> props

    for obj in data:
        info  = obj.get("info", {})
        props = info.get("props", {})
        oid   = obj.get("id", -1)
        mc    = props.get("media.class", "")

        if mc == "Audio/Sink":
            sinks[oid] = props.get("node.name", "")

        if mc == "Stream/Output/Audio":
            app = props.get("application.name", "")
            # Skip PulseAudio clients — pulsectl handles them
            if props.get("client.api") == "pipewire-pulse":
                continue
            if not app:
                continue
            streams[oid] = {
                "id":       oid,
                "app_name": app,
                "pid":      str(props.get("application.process.id", "0")),
                "props":    props,
            }

    # Resolve connected sink via links
    for obj in data:
        if obj.get("type") != "PipeWire:Interface:Link":
            continue
        info   = obj.get("info", {})
        src_id = info.get("output-node-id", -1)
        dst_id = info.get("input-node-id", -1)
        if src_id in streams and dst_id in sinks:
            streams[src_id]["sink_name"] = sinks[dst_id]
            streams[src_id]["sink_id"]   = dst_id

    for s in streams.values():
        s.setdefault("sink_name", None)
        s.setdefault("sink_id", None)

    return list(streams.values())


def loopback_link_target(playback_name: str, data: list | None = None) -> str | None:
    """Return the node.name of the node currently linked as the input of *playback_name*.

    In other words: given the ``node.name`` of the playback side of a
    ``pw-loopback`` (e.g. ``"Arctis_Game_sink_out"``), return the name of the
    downstream node that PipeWire has actually wired it to
    (e.g. ``"effect_input.sonar-game-eq"`` when correctly linked, or
    ``"alsa_output.usb-SteelSeries_..."`` when WirePlumber has mis-routed it).

    Parameters
    ----------
    playback_name:
        ``node.name`` of the loopback playback node to inspect.
    data:
        Optional pre-fetched ``pw-dump`` payload (list of objects).  When
        *None*, a fresh ``pw-dump`` is executed.

    Returns
    -------
    str | None
        The ``node.name`` of the linked input node, or *None* if the loopback
        is not currently linked to anything, *playback_name* is ambiguous
        (see below), or an error occurred.

    Ambiguity handling (CHA-1, read-only counterpart):
        Resolving *which* node produced a link matters just as much here as
        it does in :func:`ensure_loopback_link` — if two nodes share
        *playback_name*, iterating the dump and returning on the first
        matching Link answers about *whichever one happened to be found
        first*, not about the one the caller (the loopback watchdog, or a
        diagnostics view) actually means. That is silently misleading in
        exactly the way CHA-1 warns about, even though this function never
        touches the graph.

        So *playback_name* is resolved through the same
        :func:`_index_nodes_by_name` / :func:`_resolve_unique_node_id` path
        the write-side siblings use, and an ambiguous name returns *None*
        with a loud ``ERROR`` log — same sentinel as "not linked yet". This
        is deliberately *not* the same as those siblings' "refuse to act"
        behaviour: refusing to link protects a mutation, but a query has
        nothing to protect by refusing — it would only turn a transient,
        self-healing bit of uncertainty into a dead end for whoever reads
        the answer. The existing caller contract already treats *None* as
        "leave it alone, don't act on this tick" (see
        ``core.py``'s ``_loopback_watchdog``), which is exactly the right
        response to "I can't currently tell which node this is" — safer
        than guessing, and it resolves itself the moment the duplicate goes
        away.

        The downstream side (the node the link actually points *at*) needs
        no such treatment: once *playback_name* resolves to one concrete
        node id, the link is looked up by that id, and the reported input
        node is identified by its own id too — a duplicate name on that side
        cannot make this function point at the wrong node, it can only make
        the string it returns collide with another node's name (which is
        already true of node.name in general, and orthogonal to what this
        function is answering).
    """
    try:
        if data is None:
            data = _pw_dump()

        # Build id → node.name map for all Node objects (used to name the
        # downstream/input side of the link once found).
        node_names: dict[int, str] = {}
        for obj in data:
            obj_type = obj.get("type", "")
            if not obj_type.endswith("Node"):
                continue
            props = obj.get("info", {}).get("props", {})
            node_name = props.get("node.name", "")
            if node_name:
                node_names[obj["id"]] = node_name

        # Resolve playback_name to exactly one node id, refusing ambiguity
        # (CHA-1) instead of guessing which same-named node the caller meant.
        nodes_by_name = _index_nodes_by_name(data)
        playback_id = _resolve_unique_node_id(nodes_by_name, playback_name, "loopback_link_target")
        if playback_id is None:
            return None

        # Find the Link whose output node is playback_id.
        for obj in data:
            obj_type = obj.get("type", "")
            if not obj_type.endswith("Link"):
                continue
            props = obj.get("info", {}).get("props", {})
            output_node_id = props.get("link.output.node")
            input_node_id = props.get("link.input.node")
            if output_node_id is None or input_node_id is None:
                continue
            if output_node_id == playback_id:
                return node_names.get(input_node_id)

        # No link found for this playback node — orphan / not yet linked.
        return None
    except Exception as e:
        logger.warning("loopback_link_target failed: %s", e)
        return None


def relink_loopback_playback(playback_name: str, target_name: str, data: list | None = None) -> bool:
    """Relink the playback side of a pw-loopback to *target_name* via pw-metadata.

    Instructs WirePlumber to reconnect *playback_name* to *target_name* by
    writing ``target.node`` in PipeWire metadata — no process is killed or
    restarted.  This keeps the corresponding PA sink (e.g. ``Arctis_Chat``)
    alive in applications like Discord that enumerate devices once at startup.

    Returns True when the pw-metadata command succeeds, False if either node
    is not found or the command fails.
    """
    try:
        if data is None:
            data = _pw_dump()

        # Resolve both names through the ambiguity-refusing lookup (CHA-1).
        # A duplicate name matters more here than anywhere else: the second
        # pw-metadata write below stores target.object *by name*, so a node
        # impersonating the target would keep the hijack across
        # filter-chain restarts and reboots, not just for this tick.
        nodes_by_name = _index_nodes_by_name(data)
        playback_id = _resolve_unique_node_id(
            nodes_by_name, playback_name, "relink_loopback_playback")
        target_id = _resolve_unique_node_id(
            nodes_by_name, target_name, "relink_loopback_playback target")

        if playback_id is None:
            logger.warning("relink_loopback_playback: '%s' not found in pw-dump", playback_name)
            return False
        if target_id is None:
            logger.warning("relink_loopback_playback: target '%s' not found in pw-dump", target_name)
            return False

        _pw_run(
            ["pw-metadata", str(playback_id), "target.node", str(target_id)],
            check=True, timeout=3, capture_output=True,
        )
        # WirePlumber >= 0.5 resolves target.object (by node.name or
        # object.serial) with priority over the deprecated target.node (node ID).
        # Write target.object using the node name — WirePlumber accepts it and
        # it survives filter-chain restarts that change node IDs.
        # We do not use object.serial here because it requires an extra pw-dump
        # lookup and would add complexity without meaningful benefit: node.name
        # is stable within a PipeWire session and is already used by loopback_manager.
        _pw_run(
            ["pw-metadata", str(playback_id), "target.object", target_name],
            check=True, timeout=3, capture_output=True,
        )
        logger.info(
            "relink_loopback_playback: '%s' → '%s' (node %d → %d)",
            playback_name, target_name, playback_id, target_id,
        )
        return True
    except Exception as exc:
        logger.warning("relink_loopback_playback failed: %s", exc)
        return False


def _node_ports(data: list, node_id: int, direction: str) -> dict[str, int]:
    """Map ``audio.channel`` → global port id for the ports of *node_id*.

    *direction* is the PipeWire ``port.direction`` ("in" or "out"). Ports with
    no ``audio.channel`` (control/monitor ports) are skipped. When a node has
    two ports sharing a channel (should not happen for our loopbacks) the last
    one wins — the caller only needs one link per channel.
    """
    ports: dict[str, int] = {}
    for obj in data:
        if not obj.get("type", "").endswith("Port"):
            continue
        props = obj.get("info", {}).get("props", {})
        if props.get("node.id") != node_id:
            continue
        if props.get("port.direction") != direction:
            continue
        channel = props.get("audio.channel")
        if not channel or channel == "UNK":
            continue
        ports[channel] = obj["id"]
    return ports


# Canonical channel ordering used by the positional fallback below. Only the
# relative order matters (not the exact set of names PipeWire may ever emit).
_CANONICAL_CHANNEL_ORDER = ("FL", "FR", "FC", "LFE", "RL", "RR", "SL", "SR", "RC", "MONO")

#: Pairs whose positional fallback has already been reported, so it is said
#: once instead of on every watchdog pass.
#:
#: The fallback is a property of two nodes, not of a pass: an 8ch positioned
#: source into an AUX-named pro-audio sink resolves positionally the first
#: time and every time after. Logged unconditionally, it produced three or four
#: INFO lines every five seconds for as long as the daemon ran — on a Nova Pro
#: it is *every* loopback, since the headset's sink names its ports AUX0/AUX1.
#: `journalctl -n 100` then reaches about two minutes back, so bug reports from
#: exactly the devices that need investigating carried nothing else: no device
#: init, no HID error, nothing (#219).
_POSITIONAL_FALLBACK_SEEN: set[tuple[str, str]] = set()


def _log_positional_fallback(what: str, out_name: str, in_name: str,
                             out_ports, in_ports) -> None:
    """Say it the first time for this pair, then keep it at debug level."""
    key = (out_name, in_name)
    first = key not in _POSITIONAL_FALLBACK_SEEN
    _POSITIONAL_FALLBACK_SEEN.add(key)
    logger.log(
        logging.INFO if first else logging.DEBUG,
        "%s: positional fallback: '%s' -> '%s' (no shared channel names; out=%s in=%s)",
        what, out_name, in_name, list(out_ports), list(in_ports),
    )


def _channel_sort_key(channel: str) -> tuple:
    """Deterministic sort key for a PipeWire ``audio.channel`` name.

    Ranking (lower sorts first):

    0. Positioned channels from :data:`_CANONICAL_CHANNEL_ORDER`, in that
       canonical order (FL, FR, FC, LFE, RL, RR, SL, SR, RC, MONO).
    1. ``AUXn`` ports — the port names a PipeWire pro-audio profile exposes
       for a headset's physical output/input (issue #129) — ordered
       *numerically* by ``n`` (AUX0 < AUX1 < ... < AUX10), never
       lexicographically (which would sort AUX10 before AUX2).
    2. Anything else, ordered alphabetically as a last resort so the result
       stays fully deterministic even for unrecognised channel names.
    """
    if channel in _CANONICAL_CHANNEL_ORDER:
        return (0, _CANONICAL_CHANNEL_ORDER.index(channel), channel)
    if channel.startswith("AUX") and channel[3:].isdigit():
        return (1, int(channel[3:]), channel)
    return (2, 0, channel)


def _resolve_channel_pairs(
    out_ports: dict[str, int], in_ports: dict[str, int],
) -> list[tuple[int, int]]:
    """Resolve which output port links to which input port.

    Prefers channel-**name** matching (FL→FL, FR→FR, …) — this is what every
    shipped loopback/EQ/HeSuVi node uses today and must keep working exactly
    as before. Only when **no** channel name is shared between the two sides
    (e.g. a positioned 8ch source, like the Sonar Media EQ output, feeding a
    pro-audio headset sink whose ports are named ``AUX0``/``AUX1`` instead of
    ``FL``/``FR`` — issue #129) does this fall back to **positional**
    matching: source channels sorted into canonical order (FL, FR, FC, …)
    zipped against target ports in their natural order (AUX0, AUX1, …),
    paired up to the shorter side.

    Returns a list of ``(out_port_id, in_port_id)`` pairs. Returns an empty
    list if either side has no ports at all.
    """
    if not out_ports or not in_ports:
        return []

    common = [channel for channel in out_ports if channel in in_ports]
    if common:
        return [(out_ports[channel], in_ports[channel]) for channel in common]

    # No shared channel name — positional fallback (issue #129).
    src = [out_ports[channel] for channel in sorted(out_ports, key=_channel_sort_key)]
    dst = [in_ports[channel] for channel in sorted(in_ports, key=_channel_sort_key)]
    return list(zip(src, dst))


SONAR_OUTPUT_NODE = "effect_output.sonar-output-eq"


def retarget_output(target_name: str, data: list | None = None) -> bool:
    """Point the Sonar Output chain at *target_name* without restarting anything.

    Switching the Output channel's device used to go through a full
    filter-chain/loopback recreate, which kills the ``pw-loopback`` process —
    and that process *is* the virtual sink. Destroying it orphans every stream
    playing to it: PipeWire scatters them onto whatever sink it can find, a new
    sink appears with a new id, and ASM then has to chase the streams back. The
    audible result is exactly what switching between a Bluetooth headset and
    the Arctis produces — sound that does not come back, and channels that end
    up on the wrong device.

    Re-linking avoids all of it. The virtual sink never leaves the graph, so no
    stream is ever orphaned; only the link downstream of the equaliser moves.
    :func:`ensure_loopback_link` already tears down links to any node other
    than the requested target, so this is a complete switch rather than an
    additional destination.
    """
    if not target_name:
        return False
    return ensure_loopback_link(SONAR_OUTPUT_NODE, target_name, data=data)


def _index_nodes_by_name(data: list) -> dict[str, list[int]]:
    """Group every Node's id by its ``node.name``.

    PipeWire does not enforce uniqueness of ``node.name`` — nothing stops an
    unrelated (or actively hostile) process from creating a node that shares
    the name of one ASM owns (issue CHA-1, reproduced live: a
    ``pw-loopback`` started with
    ``node.name=effect_input.sonar-media-eq`` hijacked the real Sonar Media
    EQ's link). A plain ``dict[str, int]`` built by iterating the dump is
    last-writer-wins — whichever node happens to appear last silently
    becomes "the" node of that name. Returning every id per name instead
    lets the caller (:func:`_resolve_unique_node_id`) detect and refuse the
    ambiguous case rather than guess.
    """
    index: dict[str, list[int]] = {}
    for obj in data:
        if not obj.get("type", "").endswith("Node"):
            continue
        props = obj.get("info", {}).get("props", {})
        name = props.get("node.name", "")
        if name:
            index.setdefault(name, []).append(obj["id"])
    return index


def _resolve_unique_node_id(
    nodes_by_name: dict[str, list[int]], name: str, caller: str,
) -> int | None:
    """Resolve *name* to exactly one node id, refusing ambiguity outright.

    Returns ``None`` both when *name* is absent (the ordinary "not up yet"
    case, left to the caller to log at debug level) and when it is
    ambiguous. The ambiguous case is logged here, loudly, because it is
    never a benign race the way a missing node is: a duplicate ASM node
    name in the graph is always a fault — either a bug spawning the same
    node twice, or another process impersonating one of ours — and picking
    either candidate would silently link the wrong one.
    """
    ids = nodes_by_name.get(name, [])
    if len(ids) > 1:
        logger.error(
            "%s: refusing to link '%s' — %d nodes share this name in the "
            "graph (ids=%s); a duplicate node.name is always a fault, "
            "never a legitimate race — investigate what created it",
            caller, name, len(ids), sorted(ids),
        )
        return None
    return ids[0] if ids else None


def ensure_loopback_link(
    playback_name: str, target_name: str, data: list | None = None,
    outcome: dict | None = None,
) -> bool:
    """Ensure the playback side of a ``pw-loopback`` is linked to *target_name*.

    The loopbacks run with ``node.autoconnect=false`` (issue #100), so
    WirePlumber never links their playback node and no competing output device
    (a second USB DAC such as a Creative Pebble Nova, the physical headset, the
    user's default sink…) can ever steal it. ASM owns the link and creates it
    here, matched channel-for-channel (FL→FL, FR→FR, …). The operation is
    idempotent: correct links already present are left untouched, missing ones
    are created, and any link from the playback node to a node *other* than
    *target_name* is torn down.

    Parameters
    ----------
    playback_name:
        ``node.name`` of the loopback playback node (e.g. ``Arctis_Media_sink_out``).
    target_name:
        ``node.name`` of the downstream EQ input (e.g. ``effect_input.sonar-media-eq``).
    data:
        Optional pre-fetched ``pw-dump`` payload; a fresh dump is executed when
        *None*. May be reused across channels within one watchdog tick — links
        for different channels are independent, so slightly stale data is safe.
    outcome:
        Optional dict, filled in with ``created``, ``total`` and ``denied``
        (how many channels PipeWire refused on permissions). A caller that only
        knows "this did not link" cannot tell a refusal from a missing node,
        and those need opposite responses: recreating the loopback fixes the
        second and makes the first worse, since the new client starts just as
        restricted while the recreation drops whatever audio was flowing (#181).

    Returns
    -------
    bool
        True when every source channel that also exists on the target is linked
        to it. False when either node is absent from the graph (the loopback is
        not up yet, or the filter-chain that owns *target_name* is dead) or no
        channel could be matched, so the caller can retry or escalate.
    """
    try:
        if data is None:
            data = _pw_dump()

        nodes_by_name = _index_nodes_by_name(data)
        playback_id = _resolve_unique_node_id(nodes_by_name, playback_name, "ensure_loopback_link")
        target_id = _resolve_unique_node_id(nodes_by_name, target_name, "ensure_loopback_link")
        if playback_id is None:
            logger.debug(
                "ensure_loopback_link: playback '%s' not in graph (or "
                "ambiguous — see any error above)", playback_name,
            )
            return False
        if target_id is None:
            logger.debug(
                "ensure_loopback_link: target '%s' not in graph (or "
                "ambiguous — see any error above)", target_name,
            )
            return False

        out_ports = _node_ports(data, playback_id, "out")
        in_ports = _node_ports(data, target_id, "in")
        if not out_ports or not in_ports:
            logger.warning(
                "ensure_loopback_link: no matchable ports for '%s'→'%s' (out=%s in=%s)",
                playback_name, target_name, list(out_ports), list(in_ports),
            )
            return False

        # Index links whose OUTPUT node is the playback node: keep the ones that
        # already point at the target, and collect any stray links to remove.
        existing: set[tuple[int, int]] = set()
        stray: list[tuple[int, int]] = []
        for obj in data:
            if not obj.get("type", "").endswith("Link"):
                continue
            props = obj.get("info", {}).get("props", {})
            if props.get("link.output.node") != playback_id:
                continue
            pair = (props.get("link.output.port"), props.get("link.input.port"))
            if props.get("link.input.node") == target_id:
                existing.add(pair)
            else:
                stray.append(pair)

        # Tear down any link to a node other than the intended target. With
        # autoconnect=false there should be none, but this keeps the graph clean
        # if a stray link was created before the flag took effect or by a user.
        for out_port, in_port in stray:
            _pw_run(
                ["pw-link", "-d", str(out_port), str(in_port)],
                check=False, timeout=3, capture_output=True,
            )

        # Resolve which output port links to which input port: channel-NAME
        # matching first (FL→FL, FR→FR, …); only if no name is shared between
        # the two sides does this fall back to positional matching (e.g. an
        # 8ch positioned source into an AUX0/AUX1-named pro-audio sink —
        # issue #129).
        pairs = _resolve_channel_pairs(out_ports, in_ports)
        if not pairs:
            # A source channel the target does not expose at all (e.g. a 2ch
            # loopback into a hypothetical 8ch EQ would leave FC/LFE/… unfed).
            # All shipped EQ nodes are stereo or match 1:1, so this is
            # defensive only.
            return False
        if not any(channel in in_ports for channel in out_ports):
            _log_positional_fallback("ensure_loopback_link", playback_name,
                                     target_name, out_ports, in_ports)

        out_port_to_channel = {port_id: channel for channel, port_id in out_ports.items()}

        ok = True
        linked_any = False
        created = 0
        denied = 0
        for out_port, in_port in pairs:
            channel = out_port_to_channel.get(out_port, "?")
            if (out_port, in_port) in existing:
                linked_any = True
                continue
            r = _pw_run(
                ["pw-link", str(out_port), str(in_port)],
                check=False, timeout=3, capture_output=True,
            )
            err = (r.stderr or b"").decode(errors="replace").strip()

            # A refused link is not a broken graph: on systems that start our
            # clients restricted, PipeWire denies it on permissions alone and
            # every channel ends up reaching nothing (#181). Raise the
            # permission for the two clients involved and try once more, rather
            # than reporting a failure the user cannot act on.
            if r.returncode != 0 and _LINK_DENIED in err.lower():
                if grant_link_permissions(out_port, in_port):
                    r = _pw_run(
                        ["pw-link", str(out_port), str(in_port)],
                        check=False, timeout=3, capture_output=True,
                    )
                    err = (r.stderr or b"").decode(errors="replace").strip()

            if r.returncode == 0:
                linked_any = True
                created += 1
            else:
                ok = False
                if _LINK_DENIED in err.lower():
                    denied += 1
                logger.warning(
                    "ensure_loopback_link: pw-link %s→%s (%s) failed: %s",
                    out_port, in_port, channel, err,
                )

        if created:
            logger.info(
                "ensure_loopback_link: '%s' → '%s' (%d/%d channels linked)",
                playback_name, target_name, created, len(pairs),
            )
        if outcome is not None:
            outcome.update({"created": created, "total": len(pairs),
                            "denied": denied})
        return linked_any and ok
    except Exception as exc:
        logger.warning("ensure_loopback_link failed: %s", exc)
        return False


def ensure_capture_link(
    source_name: str, capture_name: str, data: list | None = None,
) -> bool:
    """Ensure the ASM-owned capture side of a filter-chain is fed by *source_name*.

    Input-side counterpart of :func:`ensure_loopback_link` (issue #127): the
    Sonar Micro EQ's capture node (``effect_input.sonar-micro-eq``) runs with
    ``node.autoconnect = false`` / ``state.restore-target = false`` (the same
    issue #100 "ASM owns this link" pattern, applied to the input side), so
    WirePlumber never links or moves it. Every filter-chain restart triggered
    by a micro EQ edit recreates this node with nothing linked into it, and
    without an explicit enforcement pass WirePlumber can (and does, per issue
    #127) route it to whatever it considers the current default source — a
    second microphone — instead of the Arctis. This function is what
    (re)establishes and enforces the Arctis → micro-EQ link, channel-matched,
    exactly like ``ensure_loopback_link`` does for playback.

    Teardown scope is intentionally **inverted** compared to
    ``ensure_loopback_link``: *source_name* here is a shared physical device
    (the Arctis microphone) that may legitimately feed other consumers too
    (a voice recorder, OBS, a second capture app, …) — tearing down "stray"
    links found on the *source*'s output ports would silently sever those.
    So only links landing on *capture_name*'s input ports are inspected: any
    such link whose upstream is not *source_name* is a mis-route (e.g. a
    competing mic WirePlumber wired in instead) and is torn down; every other
    link leaving *source_name* toward a different destination is left
    completely untouched.

    Parameters
    ----------
    source_name:
        ``node.name`` of the physical microphone source (e.g. the Arctis
        ALSA capture PCM returned by ``sonar_to_pipewire._get_physical_in()``).
    capture_name:
        ``node.name`` of the ASM-owned filter-chain capture node
        (``effect_input.sonar-micro-eq``).
    data:
        Optional pre-fetched ``pw-dump`` payload; a fresh dump is executed
        when *None*. May be reused across a watchdog tick alongside
        ``ensure_loopback_link``/``ensure_spatial_eq_links``.

    Returns
    -------
    bool
        True when every source channel that also exists on the capture node
        is linked to it. False when either node is absent from the graph
        (mic unplugged, filter-chain not up yet) or no channel could be
        matched, so the caller can retry later.
    """
    try:
        if data is None:
            data = _pw_dump()

        nodes_by_name = _index_nodes_by_name(data)
        source_id = _resolve_unique_node_id(nodes_by_name, source_name, "ensure_capture_link")
        capture_id = _resolve_unique_node_id(nodes_by_name, capture_name, "ensure_capture_link")
        if source_id is None:
            logger.debug(
                "ensure_capture_link: source '%s' not in graph (or ambiguous "
                "— see any error above)", source_name,
            )
            return False

        # A sink's "output" ports are its monitor: linking one here does not
        # feed the microphone chain with a microphone, it feeds it with
        # everything the user is listening to, and every app reading the ASM
        # mic then transmits their desktop audio. Refused outright rather than
        # trusted to the callers — this is the one mistake on the input side
        # that is inaudible to the person making it.
        #
        # Looked up by the already-resolved source_id, not by name: a second
        # name→media.class dict would carry the exact same last-writer-wins
        # hazard _resolve_unique_node_id exists to close (issue CHA-1).
        source_class = ""
        for obj in data:
            if obj.get("type", "").endswith("Node") and obj.get("id") == source_id:
                source_class = obj.get("info", {}).get("props", {}).get("media.class", "")
                break
        if source_class == "Audio/Sink":
            logger.error(
                "ensure_capture_link: refusing to feed '%s' from '%s' — that is "
                "an output, and its monitor would be transmitted as the "
                "microphone", capture_name, source_name)
            return False
        if capture_id is None:
            logger.debug(
                "ensure_capture_link: capture '%s' not in graph (or ambiguous "
                "— see any error above)", capture_name,
            )
            return False

        out_ports = _node_ports(data, source_id, "out")
        in_ports = _node_ports(data, capture_id, "in")
        if not out_ports or not in_ports:
            logger.warning(
                "ensure_capture_link: no matchable ports for '%s'->'%s' (out=%s in=%s)",
                source_name, capture_name, list(out_ports), list(in_ports),
            )
            return False

        # Index links whose INPUT node is the capture node (the ASM-owned
        # side): keep the ones that already originate from source_id, and
        # collect any stray link — one coming from some OTHER node, e.g. a
        # competing mic WirePlumber mis-routed here — for removal. We never
        # inspect, let alone tear down, links leaving source_id toward some
        # other destination: the physical mic may legitimately feed other
        # consumers and those must be left alone (see docstring).
        existing: set[tuple[int, int]] = set()
        stray: list[tuple[int, int]] = []
        for obj in data:
            if not obj.get("type", "").endswith("Link"):
                continue
            props = obj.get("info", {}).get("props", {})
            if props.get("link.input.node") != capture_id:
                continue
            pair = (props.get("link.output.port"), props.get("link.input.port"))
            if props.get("link.output.node") == source_id:
                existing.add(pair)
            else:
                stray.append(pair)

        # Tear down any link into the capture node that does not originate
        # from the intended source — this is the mis-route issue #127
        # reported (a competing mic stealing the micro EQ capture after a
        # config regen / filter-chain restart).
        for out_port, in_port in stray:
            _pw_run(
                ["pw-link", "-d", str(out_port), str(in_port)],
                check=False, timeout=3, capture_output=True,
            )

        # Resolve which output port links to which input port: channel-NAME
        # matching first; only if no name is shared between the two sides
        # does this fall back to positional matching (issue #129 fallback,
        # applied here for consistency with ensure_loopback_link even though
        # a positioned mic feeding an AUX-named capture is not the reported
        # scenario).
        pairs = _resolve_channel_pairs(out_ports, in_ports)
        if not pairs:
            # The capture node does not expose any of the source channels at
            # all (e.g. a stereo mic feeding a mono capture) — nothing to link.
            return False
        if not any(channel in in_ports for channel in out_ports):
            _log_positional_fallback("ensure_capture_link", source_name,
                                     capture_name, out_ports, in_ports)

        out_port_to_channel = {port_id: channel for channel, port_id in out_ports.items()}

        ok = True
        linked_any = False
        created = 0
        for out_port, in_port in pairs:
            channel = out_port_to_channel.get(out_port, "?")
            if (out_port, in_port) in existing:
                linked_any = True
                continue
            r = _pw_run(
                ["pw-link", str(out_port), str(in_port)],
                check=False, timeout=3, capture_output=True,
            )
            if r.returncode == 0:
                linked_any = True
                created += 1
            else:
                ok = False
                logger.warning(
                    "ensure_capture_link: pw-link %s->%s (%s) failed: %s",
                    out_port, in_port, channel,
                    (r.stderr or b"").decode(errors="replace").strip(),
                )

        if created:
            logger.info(
                "ensure_capture_link: '%s' -> '%s' (%d/%d channels linked)",
                source_name, capture_name, created, len(pairs),
            )
        return linked_any and ok
    except Exception as exc:
        logger.warning("ensure_capture_link failed: %s", exc)
        return False


def _is_asm_sink(name: str) -> bool:
    """Return True if *name* (a PulseAudio sink name) belongs to ASM.

    Covers both the virtual ``Arctis_*`` pw-loopback sinks created by ASM
    (Game/Chat/Media/…) and the physical headset sink itself, whose
    ``node.name`` on the ALSA card is something like
    ``alsa_output.usb-SteelSeries_Arctis_Nova_Pro_Wireless-...``.
    """
    if not name:
        return False
    if name.startswith("Arctis_"):
        return True
    return "SteelSeries" in name or "Arctis" in name


def reclaim_misrouted_streams() -> tuple[int, list[str]]:
    """Move application streams that are playing on a non-ASM output device
    (HDMI, S/PDIF, another DAC…) back onto the default ASM headset sink.

    Real-world trigger: an app (e.g. Firefox) ends up routed to a S/PDIF or
    HDMI output — silence, since the user is wearing the headset — and this
    one-shot brings it back without the user hunting through pavucontrol.

    Returns (count_moved, [app display names moved]). Never raises — logs and
    returns (0, []) on any failure (pulsectl missing, no ASM sink found, …).
    """
    try:
        import pulsectl  # type: ignore
    except Exception as exc:
        logger.debug("pulsectl unavailable, cannot reclaim streams: %s", exc)
        return 0, []

    moved = 0
    names: list[str] = []
    try:
        with pulsectl.Pulse("asm-reclaim") as pulse:
            sinks = pulse.sink_list()
            sink_inputs = pulse.sink_input_list()

            # Pick the target ASM sink: prefer the current default if it is
            # already an ASM sink, then the Game virtual sink, then any
            # physical headset sink.
            target = None
            default_name = pulse.server_info().default_sink_name
            if default_name and _is_asm_sink(default_name):
                target = next((s for s in sinks if s.name == default_name), None)
            if target is None:
                target = next((s for s in sinks if s.name == "Arctis_Game"), None)
            if target is None:
                target = next((s for s in sinks if _is_asm_sink(s.name)), None)

            if target is None:
                logger.warning("reclaim_misrouted_streams: no ASM sink found — skipping")
                return 0, []

            sinks_by_index = {s.index: s for s in sinks}

            for si in sink_inputs:
                props = si.proplist
                binary = props.get("application.process.binary", "")
                app_name = props.get("application.name", "")
                media_name = props.get("media.name", "")

                # Skip ASM's own internal nodes (filter-chain EQ, virtual
                # surround, Sonar loopbacks) — never move those.
                if not binary or binary in ("pipewire", "pw-loopback"):
                    continue
                if any(tag in media_name for tag in ("EQ output", "Virtual Surround", "Sonar")):
                    continue
                if not app_name:
                    continue

                current_sink = sinks_by_index.get(si.sink)
                if current_sink is None:
                    continue
                if _is_asm_sink(current_sink.name):
                    continue  # already on the headset

                try:
                    pulse.sink_input_move(si.index, target.index)
                    moved += 1
                    names.append(app_name or binary)
                    logger.info(
                        "reclaim_misrouted_streams: moved '%s' from '%s' to '%s'",
                        app_name or binary, current_sink.name, target.name,
                    )
                except Exception as exc:
                    logger.warning(
                        "reclaim_misrouted_streams: failed to move '%s': %s",
                        app_name or binary, exc,
                    )
    except Exception as exc:
        logger.warning("reclaim_misrouted_streams failed: %s", exc)
        return 0, []

    return moved, names


def move_native_stream(stream_node_id: int, target_sink_name: str, data: list | None = None) -> bool:
    """Move a native PipeWire stream to target_sink_name using pw-metadata."""
    if data is None:
        data = _pw_dump()

    # Find target sink node-id
    target_id = None
    for obj in data:
        props = obj.get("info", {}).get("props", {})
        if props.get("media.class") == "Audio/Sink" and props.get("node.name", "") == target_sink_name:
            target_id = obj["id"]
            break

    if target_id is None:
        logger.warning("Sink %s not found", target_sink_name)
        return False

    try:
        _pw_run(
            ["pw-metadata", str(stream_node_id), "target.node", str(target_id)],
            check=True, timeout=3, capture_output=True
        )
        logger.info("Moved native stream %d -> %s (id=%d)", stream_node_id, target_sink_name, target_id)
        return True
    except Exception as e:
        logger.warning("pw-metadata failed: %s", e)
        return False
