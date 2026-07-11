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


def _pw_dump() -> list:
    try:
        r = _pw_run(["pw-dump"], capture_output=True, text=True, timeout=3)
        return json.loads(r.stdout)
    except Exception as e:
        logger.warning("pw-dump failed: %s", e)
        return []


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
        is not currently linked to anything or an error occurred.
    """
    try:
        if data is None:
            data = _pw_dump()

        # Build id → node.name map for all Node objects.
        node_names: dict[int, str] = {}
        for obj in data:
            obj_type = obj.get("type", "")
            if not obj_type.endswith("Node"):
                continue
            props = obj.get("info", {}).get("props", {})
            node_name = props.get("node.name", "")
            if node_name:
                node_names[obj["id"]] = node_name

        # Find the first Link whose output node is playback_name.
        for obj in data:
            obj_type = obj.get("type", "")
            if not obj_type.endswith("Link"):
                continue
            props = obj.get("info", {}).get("props", {})
            output_node_id = props.get("link.output.node")
            input_node_id = props.get("link.input.node")
            if output_node_id is None or input_node_id is None:
                continue
            if node_names.get(output_node_id) == playback_name:
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

        playback_id: int | None = None
        target_id: int | None = None

        for obj in data:
            if not obj.get("type", "").endswith("Node"):
                continue
            props = obj.get("info", {}).get("props", {})
            name = props.get("node.name", "")
            if name == playback_name:
                playback_id = obj["id"]
            elif name == target_name:
                target_id = obj["id"]

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


def ensure_loopback_link(
    playback_name: str, target_name: str, data: list | None = None,
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

        node_ids: dict[str, int] = {}
        for obj in data:
            if not obj.get("type", "").endswith("Node"):
                continue
            props = obj.get("info", {}).get("props", {})
            name = props.get("node.name", "")
            if name:
                node_ids[name] = obj["id"]

        playback_id = node_ids.get(playback_name)
        target_id = node_ids.get(target_name)
        if playback_id is None:
            logger.debug("ensure_loopback_link: playback '%s' not in graph", playback_name)
            return False
        if target_id is None:
            logger.debug("ensure_loopback_link: target '%s' not in graph", target_name)
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

        # Create the missing channel-matched links.
        ok = True
        linked_any = False
        created = 0
        for channel, out_port in out_ports.items():
            in_port = in_ports.get(channel)
            if in_port is None:
                # A source channel the target does not expose (e.g. a 2ch loopback
                # into a hypothetical 8ch EQ would leave FC/LFE/… unfed). All
                # shipped EQ nodes are stereo, so this branch is defensive only.
                continue
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
                    "ensure_loopback_link: pw-link %s→%s (%s) failed: %s",
                    out_port, in_port, channel,
                    (r.stderr or b"").decode(errors="replace").strip(),
                )

        if created:
            logger.info(
                "ensure_loopback_link: '%s' → '%s' (%d/%d channels linked)",
                playback_name, target_name, created, len(out_ports),
            )
        return linked_any and ok
    except Exception as exc:
        logger.warning("ensure_loopback_link failed: %s", exc)
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
