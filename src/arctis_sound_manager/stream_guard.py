# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Decide which audio sources are allowed to reach a screen-share capture node.

Discord's Linux screen share creates its own PipeWire node (``discord_capture``)
and links *every* playback stream into it, so a stream that is only meant for
the headset — a browser tab, the Chat channel, a notification — is broadcast to
everyone in the call. Discord offers no source picker on Linux and the node is
not ours, so the only lever is the graph: cut the links we do not want.

This module holds the pure decision logic (no I/O, no subprocesses) so it can be
tested against recorded ``pw-dump`` output. The daemon in
``scripts/stream_guard.py`` supplies the dump and destroys the links this module
returns.

The allow rule is deliberately *not* an app-name list. A source may reach the
capture node only if it also feeds one of the allowed Arctis channels, so the
guard inherits whatever routing the user (and ``video_router``) already
established: move an app to the Game channel and it is on the stream, move it to
Media and it is off. Nothing to keep in sync.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger("stream_guard")

CONFIG_FILE = Path.home() / ".config" / "arctis_manager" / "stream_guard.json"

# Capture nodes we police. Discord names its screen-share node "discord_capture";
# the Vencord-based clients reuse the same mechanism under their own names.
DEFAULT_CAPTURE_NODES = ("discord_capture",)

# Logical channel -> the node names that represent it in the graph. Both the
# virtual sink and its filter-chain input count: a stream sits on the former
# when the Sonar EQ is off and on the latter when it is on, and the user means
# the same channel either way.
CHANNEL_SINKS: dict[str, tuple[str, ...]] = {
    "game":  ("Arctis_Game",  "effect_input.sonar-game-eq"),
    "chat":  ("Arctis_Chat",  "effect_input.sonar-chat-eq"),
    "media": ("Arctis_Media", "effect_input.sonar-media-eq"),
    # Present whether or not the channel is switched on: the guard matches by
    # node name, and a name that resolves to nothing simply never matches.
    "aux":   ("Arctis_Aux",   "effect_input.sonar-aux-eq"),
}

DEFAULT_CHANNELS = ("game",)

_LINK_TYPE = "PipeWire:Interface:Link"
_NODE_TYPE = "PipeWire:Interface:Node"


class GuardConfig:
    """User-facing settings: which channels are allowed on the stream."""

    __slots__ = ("enabled", "channels")

    def __init__(self, enabled: bool = True, channels: tuple[str, ...] = DEFAULT_CHANNELS):
        self.enabled = enabled
        # Preserve caller order but drop unknown channel names, so a typo in the
        # config file degrades to "fewer channels allowed" rather than crashing
        # the daemon or silently allowing everything.
        self.channels = tuple(c for c in channels if c in CHANNEL_SINKS)

    def allowed_sinks(self) -> set[str]:
        """Node names whose audio may reach the capture node."""
        return {name for ch in self.channels for name in CHANNEL_SINKS[ch]}

    def to_dict(self) -> dict:
        return {"enabled": self.enabled, "channels": list(self.channels)}

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, GuardConfig):
            return NotImplemented
        return self.enabled == other.enabled and self.channels == other.channels

    def __repr__(self) -> str:
        return f"GuardConfig(enabled={self.enabled}, channels={self.channels!r})"


def load_config(path: Path | None = None) -> GuardConfig:
    """Read the guard config, falling back to the default on any problem.

    A missing or corrupt file must not leave the guard disabled: the failure
    mode of "guard off" is private audio going out to a call, which is exactly
    what the user installed this to prevent. Unreadable config therefore means
    *enabled with the default channel*, not *pass everything through*.
    """
    p = path or CONFIG_FILE
    try:
        raw = json.loads(p.read_text())
    except FileNotFoundError:
        return GuardConfig()
    except Exception as exc:
        logger.warning("Unreadable %s (%s) — falling back to defaults", p, exc)
        return GuardConfig()

    if not isinstance(raw, dict):
        logger.warning("Malformed %s (expected an object) — falling back to defaults", p)
        return GuardConfig()

    channels = raw.get("channels", DEFAULT_CHANNELS)
    if not isinstance(channels, (list, tuple)):
        logger.warning("Malformed 'channels' in %s — falling back to defaults", p)
        channels = DEFAULT_CHANNELS
    return GuardConfig(enabled=bool(raw.get("enabled", True)), channels=tuple(channels))


def save_config(cfg: GuardConfig, path: Path | None = None) -> None:
    """Persist *cfg* atomically (the GUI and the daemon may both write)."""
    p = path or CONFIG_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg.to_dict(), indent=2))
    tmp.replace(p)


def _index_nodes(dump: list) -> dict[int, str]:
    """Map node id -> node.name for every node in a ``pw-dump`` payload."""
    nodes: dict[int, str] = {}
    for obj in dump:
        if obj.get("type") != _NODE_TYPE:
            continue
        node_id = obj.get("id")
        name = (obj.get("info") or {}).get("props", {}).get("node.name")
        if isinstance(node_id, int) and name:
            nodes[node_id] = name
    return nodes


def _index_links(dump: list) -> list[tuple[int, int, int]]:
    """Return ``(link_id, output_node_id, input_node_id)`` for every link."""
    links: list[tuple[int, int, int]] = []
    for obj in dump:
        if obj.get("type") != _LINK_TYPE:
            continue
        link_id = obj.get("id")
        props = (obj.get("info") or {}).get("props", {})
        out_node = props.get("link.output.node")
        in_node = props.get("link.input.node")
        if isinstance(link_id, int) and isinstance(out_node, int) and isinstance(in_node, int):
            links.append((link_id, out_node, in_node))
    return links


def links_to_cut(
    dump: list,
    allowed_sinks: set[str],
    capture_nodes: tuple[str, ...] = DEFAULT_CAPTURE_NODES,
) -> list[int]:
    """Return the ids of links feeding a capture node that must be destroyed.

    A link into the capture node survives when its source is either one of the
    allowed channel sinks itself (the capture is reading that sink's monitor) or
    a stream that also feeds one of them (the normal case: an app playing on the
    Game channel). Everything else is cut.

    Returns an empty list when no capture node is present, which is the state
    whenever no screen share is running.
    """
    node_names = _index_nodes(dump)
    capture_ids = {nid for nid, name in node_names.items() if name in capture_nodes}
    if not capture_ids:
        return []

    links = _index_links(dump)
    allowed_ids = {nid for nid, name in node_names.items() if name in allowed_sinks}

    # Which nodes feed an allowed channel sink — computed over the whole graph,
    # not just the capture links, since that is the evidence that a stream
    # belongs to the channel.
    feeds_allowed = {out_node for _, out_node, in_node in links if in_node in allowed_ids}

    doomed: list[int] = []
    for link_id, out_node, in_node in links:
        if in_node not in capture_ids:
            continue
        if out_node in capture_ids:
            # The capture node's own monitor looping back — not ours to police.
            continue
        if out_node in allowed_ids or out_node in feeds_allowed:
            continue
        doomed.append(link_id)
    return doomed


def link_ports(dump: list, link_ids: list[int]) -> list[tuple[int, int]]:
    """Map each id in *link_ids* to its ``(output-port, input-port)`` pair.

    This is the hand-off point for a safe destroy (issue CHA-3). A link's
    PipeWire object id is not a safe handle to carry from this ``pw-dump``
    snapshot to a later, separate destroy call: PipeWire recycles global ids
    within seconds on a churning graph — exactly the situation here, since
    Discord relinks continuously while a share is up — so by the time a
    caller acts on a link id it can name a completely different object, of
    any type, not necessarily even a link any more. Destroying it via
    ``pw-cli destroy <id>`` then destroys whatever now holds that id
    (reproduced live: a stale id landed on ``Arctis_Game`` and took the
    whole sink down, scattering every pinned stream).

    A ``(output-port, input-port)`` pair is content-addressed instead:
    ``pw-link -d <out> <in>`` only ever destroys the link presently
    connecting those two exact ports, and fails harmlessly if that link (or
    either port) is already gone — which is the ordinary, expected case
    here, since Discord may have torn the link down itself in the meantime.

    Ids absent from *dump*, or whose Link object lacks usable port props,
    are silently skipped: nothing to destroy either way. The result
    preserves the order of *link_ids* and may be shorter than it.
    """
    wanted = set(link_ids)
    pairs: dict[int, tuple[int, int]] = {}
    for obj in dump:
        if obj.get("type") != _LINK_TYPE:
            continue
        link_id = obj.get("id")
        if link_id not in wanted:
            continue
        props = (obj.get("info") or {}).get("props", {})
        out_port = props.get("link.output.port")
        in_port = props.get("link.input.port")
        if isinstance(out_port, int) and isinstance(in_port, int):
            pairs[link_id] = (out_port, in_port)
    return [pairs[lid] for lid in link_ids if lid in pairs]


def describe_cut(dump: list, link_ids: list[int]) -> list[str]:
    """Human-readable ``source -> capture`` labels for *link_ids*, for logging."""
    node_names = _index_nodes(dump)
    by_id = {lid: (out, inp) for lid, out, inp in _index_links(dump)}
    labels = []
    for lid in link_ids:
        out, inp = by_id.get(lid, (None, None))
        labels.append(
            f"{node_names.get(out, f'#{out}')} -> {node_names.get(inp, f'#{inp}')}"
        )
    return labels
