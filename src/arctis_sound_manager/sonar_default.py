# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Is Sonar actually in the audio path, or only appearing to be?

ASM builds the Sonar channels as virtual sinks and then relies on per-app
routing to move streams onto them. That works for apps ASM knows about, and
quietly does nothing for everything else: with the system default still pointed
at the headset's own ALSA device, a newly launched app goes straight to the
hardware, skipping every equaliser and channel ASM presents. The UI looks
identical either way — the channels are there, the EQ curves are there, the
sliders move — so the user believes Sonar is processing audio that never
reaches it.

SteelSeries' own software avoids this by registering its virtual device as the
system default, which is why the same setup "just works" on Windows and appears
broken here. This module detects the mismatch so ASM can offer to fix it,
rather than leaving the user to infer it from a missing effect.

Pure logic — no PulseAudio calls — so the states can be tested directly.
"""

from __future__ import annotations

import logging
from enum import Enum

log = logging.getLogger(__name__)

# The Sonar channel sinks, in the order they make sense as a system default.
# Media first: it is the channel meant for "everything else" — browsers, music,
# system sounds — which is exactly what follows the default. Making Game the
# default would file every new app as game audio and skew ChatMix.
SONAR_CHANNELS = ("Arctis_Media", "Arctis_Game", "Arctis_Chat")


class SonarRouting(Enum):
    ACTIVE = "active"
    """The default output is a Sonar channel — new apps are processed."""

    BYPASSED = "bypassed"
    """Sonar channels exist, but the default output skips them."""

    UNAVAILABLE = "unavailable"
    """No Sonar channels present; nothing to offer yet."""


def classify(default_sink: str | None, available_sinks: list[str]) -> SonarRouting:
    """Say whether audio following the system default reaches Sonar.

    *default_sink* is the ``node.name`` of the current default output, and
    *available_sinks* every sink currently in the graph.
    """
    channels = [c for c in SONAR_CHANNELS if c in available_sinks]
    if not channels:
        return SonarRouting.UNAVAILABLE
    if default_sink and default_sink in channels:
        return SonarRouting.ACTIVE
    return SonarRouting.BYPASSED


def suggested_channel(available_sinks: list[str]) -> str | None:
    """Which Sonar channel to propose as the system default."""
    return next((c for c in SONAR_CHANNELS if c in available_sinks), None)


def should_offer(default_sink: str | None, available_sinks: list[str],
                 already_asked: bool) -> bool:
    """Whether to put the offer in front of the user right now.

    Asked at most once: someone who declined has decided their routing, and
    re-prompting every launch would be nagging about a supported setup — a user
    routing apps by hand, or sending the default somewhere ASM does not manage,
    is not misconfigured.
    """
    if already_asked:
        return False
    return classify(default_sink, available_sinks) is SonarRouting.BYPASSED


def explain(state: SonarRouting, default_label: str = "") -> str:
    """One line describing *state*, for the UI."""
    if state is SonarRouting.ACTIVE:
        return "Sonar is processing audio from apps that follow the system default."
    if state is SonarRouting.BYPASSED:
        target = default_label or "your output device"
        return (f"Apps that follow the system default go straight to {target}, "
                f"bypassing Sonar's channels and equalisers.")
    return "Sonar channels are not running yet."


def apply_default(channel: str) -> bool:
    """Point the system default output at *channel*.

    Returns True when the change took. Failures are reported rather than
    raised: this is an offer the user accepted, not something the app depends
    on, and a broken PulseAudio connection should not take the dialog down.
    """
    try:
        import pulsectl
    except ImportError:
        log.warning("pulsectl unavailable — cannot set the default output")
        return False
    try:
        with pulsectl.Pulse("asm-sonar-default") as pulse:
            pulse.sink_default_set(channel)
        log.info("default output set to %s", channel)
        return True
    except Exception as exc:
        log.warning("could not set the default output to %s: %s", channel, exc)
        return False


def current_state() -> tuple[SonarRouting, str | None, str]:
    """Look up the live state: (routing, suggested channel, default label)."""
    try:
        import pulsectl
    except ImportError:
        return SonarRouting.UNAVAILABLE, None, ""
    try:
        with pulsectl.Pulse("asm-sonar-state") as pulse:
            sinks = pulse.sink_list()
            names = [s.name for s in sinks]
            default = pulse.server_info().default_sink_name
            label = next(
                (s.proplist.get("node.description")
                 or s.proplist.get("node.nick")
                 or getattr(s, "description", "")
                 or s.name
                 for s in sinks if s.name == default), default or "")
    except Exception as exc:
        log.debug("could not read the audio graph: %s", exc)
        return SonarRouting.UNAVAILABLE, None, ""

    return classify(default, names), suggested_channel(names), label
