"""
PipeWire utilities for native (non-PulseAudio) stream management.
Used to detect and move apps like mpv/haruna that bypass PulseAudio.
"""
import json
import logging
import subprocess

logger = logging.getLogger("pw_utils")


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
