# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
PipeWire utilities for native (non-PulseAudio) stream management.
Used to detect and move apps like mpv/haruna that bypass PulseAudio.
"""
import json
import logging
import subprocess
import time
from pathlib import Path

logger = logging.getLogger("pw_utils")

OVERRIDES_FILE = Path.home() / ".config" / "arctis_manager" / "routing_overrides.json"

# effect_input sinks are internal filter-chain nodes — apps should never
# target them directly. Remap to the corresponding Arctis virtual sink so a
# stale override still lands the app on a real, user-facing destination.
_EFFECT_REMAP = {
    "effect_input.sonar-game-eq": "Arctis_Game",
    "effect_input.sonar-chat-eq": "Arctis_Chat",
    "effect_input.sonar-media-eq": "Arctis_Media",
}


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

            for app_name, sink_name in overrides.items():
                target_name = _EFFECT_REMAP.get(sink_name, sink_name)
                target_idx = name_to_index.get(target_name)
                if target_idx is None:
                    logger.warning(
                        "Override target '%s' for '%s' not found — skipping",
                        target_name, app_name,
                    )
                    continue
                for si in sink_inputs:
                    si_app = si.proplist.get("application.name", "")
                    if si_app != app_name:
                        continue
                    if si.sink == target_idx:
                        continue
                    try:
                        pulse.sink_input_move(si.index, target_idx)
                        moved += 1
                        logger.info(
                            "Reapplied override: '%s' -> %s", app_name, target_name,
                        )
                    except Exception as exc:
                        logger.warning(
                            "Failed to move '%s' -> %s: %s",
                            app_name, target_name, exc,
                        )
    except Exception as exc:
        logger.warning("reapply_routing_overrides failed: %s", exc)

    return moved


def _pw_dump() -> list:
    try:
        r = subprocess.run(["pw-dump"], capture_output=True, text=True, timeout=3)
        return json.loads(r.stdout)
    except Exception as e:
        logger.warning("pw-dump failed: %s", e)
        return []


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

        subprocess.run(
            ["pw-metadata", str(playback_id), "target.node", str(target_id)],
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
        subprocess.run(
            ["pw-metadata", str(stream_node_id), "target.node", str(target_id)],
            check=True, timeout=3, capture_output=True
        )
        logger.info("Moved native stream %d -> %s (id=%d)", stream_node_id, target_sink_name, target_id)
        return True
    except Exception as e:
        logger.warning("pw-metadata failed: %s", e)
        return False
