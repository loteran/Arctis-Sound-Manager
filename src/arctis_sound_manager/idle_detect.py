# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Decide whether the whole ASM audio graph is idle (#180/#223/#230 class).

Background: the headset's own hardware auto-off timer (``auto_off_time_minutes``)
never fires while ASM runs, because ASM's loopbacks keep a PCM stream open on the
headset continuously (node.passive was removed everywhere in v1.4.21 — see #223,
#230 — so nothing suspends on its own any more). Restoring auto-off requires ASM
to notice inactivity itself and act on it explicitly, rather than delegating that
decision to PipeWire/WirePlumber suspend heuristics the way ``node.passive`` did.

This module holds the pure decision logic only (no I/O, no subprocesses, no
mutation) so it can be tested against recorded ``pw-dump`` output, on the same
pattern as :mod:`stream_guard`. As of this writing nothing calls the action side
yet — :class:`IdleTracker` only classifies "cut"/"restore" transitions; wiring an
actual link cutdown to them is a later step, gated behind real-usage observation
first (a bad idle detector here would silence audio, not just miss a link).

Deliberately NOT built on :func:`~arctis_sound_manager.pw_utils.get_native_streams`:
that helper filters out ``client.api == "pipewire-pulse"``, which excludes Discord
and most browsers. Treating those as "absent" is exactly the #223-class mistake —
a channel going silent while something the detector could not see was still using
it — so :func:`active_channels` reimplements the presence check straight off
``pw-dump``, counting every client stream regardless of API.

Verified live on 2026-09-06 against the real graph: each ``Arctis_*`` sink's own
PipeWire-generated companion stream (``Arctis_Game_sink_out`` and siblings, which
feed the EQ input) reports ``media.class == "Stream/Output/Audio"`` and sits in
state ``"running"`` permanently now that nothing is passive (v1.4.21) — with no
filter, every channel looked active 100% of the time, always. These carry
``node.virtual: true``; a real client stream (checked against a live ``paplay``)
never does. :func:`active_channels` excludes them on that property.
"""
from __future__ import annotations

from arctis_sound_manager.stream_guard import CHANNEL_SINKS

_NODE_TYPE = "PipeWire:Interface:Node"
_LINK_TYPE = "PipeWire:Interface:Link"
_STREAM_CLASS = "Stream/Output/Audio"

# node.name -> channel, inverted once per call from stream_guard's mapping (both
# the raw sink and its EQ input count, whichever a stream currently sits on).
_SINK_TO_CHANNEL: dict[str, str] = {
    name: channel for channel, names in CHANNEL_SINKS.items() for name in names
}


def active_channels(dump: list) -> set[str]:
    """Return the channels with at least one actively-playing stream on them.

    A channel counts as active only when a client stream (``media.class ==
    "Stream/Output/Audio"``) is itself in PipeWire state ``"running"`` — i.e.
    actually producing audio, not paused/corked — AND linked directly into one
    of that channel's sinks (:data:`stream_guard.CHANNEL_SINKS`: the raw
    ``Arctis_*`` sink when Sonar EQ is off, or its ``effect_input.sonar-*-eq``
    filter-chain input when EQ is on).

    A long-lived but currently-paused stream (Discord idling in a voice
    channel with nobody talking, a video paused mid-playback) is NOT active —
    that is the whole point: presence alone says nothing about #180.
    """
    if not dump:
        return set()

    node_name: dict[int, str] = {}
    node_state: dict[int, str | None] = {}
    stream_ids: set[int] = set()

    for obj in dump:
        if obj.get("type") != _NODE_TYPE:
            continue
        node_id = obj.get("id")
        if not isinstance(node_id, int):
            continue
        info = obj.get("info") or {}
        props = info.get("props") or {}
        name = props.get("node.name")
        if name:
            node_name[node_id] = name
        node_state[node_id] = info.get("state")
        # node.virtual=true marks PipeWire's own auto-generated companion
        # stream for a sink (here: each Arctis_*_sink_out feeding the EQ
        # input) — confirmed live on 2026-09-06: with node.passive gone
        # (v1.4.21) these sit permanently in state "running" regardless of
        # real content, which made every channel look active 100% of the
        # time. A real client stream (verified against a live `paplay`, and
        # against Discord/browsers via client.api=="pipewire-pulse") never
        # carries this property.
        if props.get("media.class") == _STREAM_CLASS and not props.get("node.virtual"):
            stream_ids.add(node_id)

    active: set[str] = set()
    for obj in dump:
        if obj.get("type") != _LINK_TYPE:
            continue
        props = (obj.get("info") or {}).get("props") or {}
        out_node = props.get("link.output.node")
        in_node = props.get("link.input.node")
        if out_node not in stream_ids:
            continue
        channel = _SINK_TO_CHANNEL.get(node_name.get(in_node, ""))
        if channel is None:
            continue
        if node_state.get(out_node) == "running":
            active.add(channel)
    return active


class IdleTracker:
    """Tracks whole-graph activity across watchdog ticks (one instance per
    daemon session — a fresh device session should get a fresh tracker, the
    same way the watchdog's other per-session state is reset).

    Classifies each tick's observation into a transition — ``"cut"`` (just
    went idle), ``"restore"`` (just came back active), or ``"none"`` (no
    change) — without doing anything about it. Deliberately conservative:
    * a single anti-flap floor between transitions,
    * a hard cap on transitions/hour beyond which the tracker permanently
      disarms itself for the session (a flapping detector is worse than none —
      it is the same failure shape as #223's suspend/resume churn).
    """

    def __init__(
        self,
        idle_after_s: float = 600.0,
        min_transition_interval_s: float = 60.0,
        max_transitions_per_hour: int = 4,
    ):
        self.idle_after_s = idle_after_s
        self._min_transition_interval_s = min_transition_interval_s
        self._max_transitions_per_hour = max_transitions_per_hour
        self.state: str = "active"  # "active" | "idle"
        self.disarmed: bool = False
        self._last_active_at: float | None = None
        self._last_transition_at: float = float("-inf")
        self._recent_transitions: list[float] = []

    def feed(self, now: float, any_active: bool) -> str:
        """Update with this tick's observation. Returns the transition that
        just happened: ``"cut"``, ``"restore"``, or ``"none"``."""
        if self.disarmed:
            return "none"

        if any_active:
            self._last_active_at = now
            if self.state == "idle":
                return self._transition(now, "active", "restore")
            return "none"

        if self.state == "active":
            if self._last_active_at is None:
                # First tick ever with nothing active: start the clock rather
                # than assuming idle time already accrued before we existed.
                self._last_active_at = now
                return "none"
            if now - self._last_active_at >= self.idle_after_s:
                return self._transition(now, "idle", "cut")
        return "none"

    def _transition(self, now: float, new_state: str, kind: str) -> str:
        if now - self._last_transition_at < self._min_transition_interval_s:
            return "none"  # anti-flap floor

        self._recent_transitions = [t for t in self._recent_transitions if now - t < 3600.0]
        self._recent_transitions.append(now)
        if len(self._recent_transitions) > self._max_transitions_per_hour:
            self.disarmed = True
            return "none"

        self.state = new_state
        self._last_transition_at = now
        return kind
