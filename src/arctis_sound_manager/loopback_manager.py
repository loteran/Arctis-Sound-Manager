# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
loopback_manager.py — Dynamic PipeWire loopback management for Arctis virtual sinks.

Each Arctis virtual sink (Game / Chat / Media) is implemented as a ``pw-loopback``
process managed by this module.  Creating loopbacks dynamically (rather than via
static ``pipewire.conf.d`` snippets) allows them to be torn down and re-created at
any time without restarting the main PipeWire daemon — which would kill Discord and
other applications that hold a PulseAudio/PipeWire sink reference.

Routing overview (unchanged from static config):
    App → Arctis_<Ch> (capture / Audio/Sink) → [loopback] → Arctis_<Ch>_sink_out
         → effect_input.sonar-<ch>-eq (filter-chain) → ... → physical output

The 8-channel negotiation (FL FR FC LFE RL RR SL SR) happens automatically when
PipeWire links the loopback playback port to an 8-channel EQ node; we only need to
tell PipeWire *not* to remix (``stream.dont-remix=false`` allows it, because the
default would prevent PipeWire from expanding 2ch to 8ch).

This module is intentionally pure: no device_state access, no file I/O, no import-
time side effects.  Callers (e.g. core.py) are responsible for resolving targets and
channel names before constructing LoopbackSpec objects.
"""
from __future__ import annotations

import logging
import subprocess
import threading
from dataclasses import dataclass

_log = logging.getLogger(__name__)

# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class LoopbackSpec:
    """Complete specification for a single pw-loopback virtual sink.

    Attributes
    ----------
    channel:
        Logical channel name used as a key in the LoopbackManager registry
        (e.g. ``"game"``, ``"chat"``, ``"media"``).
    capture_name:
        ``node.name`` of the capture side (the visible Audio/Sink, e.g.
        ``"Arctis_Game"``).  This is what applications see and route to.
    playback_name:
        ``node.name`` of the playback side (hidden output port, e.g.
        ``"Arctis_Game_sink_out"``).
    target:
        ``node.target`` for the playback side — the downstream node that
        PipeWire links this loopback into (e.g.
        ``"effect_input.sonar-game-eq"`` in Sonar mode, or the physical ALSA
        output in simple mode).
    description:
        Human-readable label shown in audio control panels (``node.description``).
    """

    channel: str
    capture_name: str
    playback_name: str
    target: str
    description: str


# ── Pre-defined sink table (mirrors _VIRTUAL_SINKS in sonar_to_pipewire.py) ──

# Callers that need "standard" Arctis specs but want to resolve targets
# themselves can use DEFAULT_SINKS as a reference table.
DEFAULT_SINKS: list[dict] = [
    {
        "channel":      "game",
        "capture_name": "Arctis_Game",
        "playback_name": "Arctis_Game_sink_out",
        "sonar_target": "effect_input.sonar-game-eq",
        "description":  "Game",
    },
    {
        "channel":      "chat",
        "capture_name": "Arctis_Chat",
        "playback_name": "Arctis_Chat_sink_out",
        "sonar_target": "effect_input.sonar-chat-eq",
        "description":  "Chat",
    },
    {
        "channel":      "media",
        "capture_name": "Arctis_Media",
        "playback_name": "Arctis_Media_sink_out",
        "sonar_target": "effect_input.sonar-media-eq",
        "description":  "Media",
    },
]


# ── Command builder ──────────────────────────────────────────────────────────

def _build_pw_loopback_argv(spec: LoopbackSpec) -> list[str]:
    """Build the ``pw-loopback`` argv for *spec*.

    The capture side is always 2ch [FL FR] with ``media.class=Audio/Sink``
    so that applications can route audio to it.  The playback side carries
    ``node.target``, ``stream.dont-remix=false`` (which lets PipeWire expand
    2→8ch when linking to an 8ch EQ node), and the standard linger/fallback
    flags used throughout this project.

    The props string format is the ``key=value`` space-separated form accepted
    by ``pw-loopback --capture-props`` / ``--playback-props``.

    Example for the Media channel::

        pw-loopback
          --capture-props='node.name=Arctis_Media media.class=Audio/Sink
                           audio.channels=2 audio.position=[FL FR]'
          --playback-props='node.name=Arctis_Media_sink_out
                            node.description=Media
                            audio.channels=2 audio.position=[FL FR]
                            stream.dont-remix=false
                            node.target=effect_input.sonar-media-eq
                            node.dont-fallback=true node.linger=true
                            latency.msec=50'

    Returns
    -------
    list[str]
        Argv suitable for ``subprocess.Popen``.
    """
    # node.description is the user-facing name shown in app output pickers
    # (Discord, browsers) and mixers — it MUST be on the capture side, which is
    # the sink applications see. The value contains spaces, so it is wrapped in
    # double quotes; PipeWire's SPA parser reads the quoted string as one value.
    capture_props = (
        f"node.name={spec.capture_name}"
        f' node.description="{spec.description}"'
        f" media.class=Audio/Sink"
        f" audio.channels=2"
        f" audio.position=[FL FR]"
    )
    playback_props = (
        f"node.name={spec.playback_name}"
        f' node.description="{spec.description}"'
        f" audio.channels=2"
        f" audio.position=[FL FR]"
        f" stream.dont-remix=false"
        f" node.target={spec.target}"
        f" node.dont-fallback=true"
        f" node.linger=true"
        f" latency.msec=50"
    )
    return [
        "pw-loopback",
        f"--capture-props={capture_props}",
        f"--playback-props={playback_props}",
    ]


# ── Manager ──────────────────────────────────────────────────────────────────

_TERMINATE_TIMEOUT: float = 2.0   # seconds to wait after SIGTERM before SIGKILL


class LoopbackManager:
    """Thread-safe manager for a set of ``pw-loopback`` child processes.

    Each channel (``"game"``, ``"chat"``, ``"media"``, …) maps to at most one
    running ``subprocess.Popen`` handle.  The manager is intentionally
    decoupled from device state: callers pass fully-resolved ``LoopbackSpec``
    objects; the manager only handles process lifecycle.

    Usage::

        mgr = LoopbackManager()
        mgr.start(LoopbackSpec("game", "Arctis_Game", "Arctis_Game_sink_out",
                               "effect_input.sonar-game-eq", "Game"))
        # ... later, after filter-chain restart ...
        mgr.recreate_all(specs)
        # ... on daemon teardown ...
        mgr.stop_all()
    """

    def __init__(self) -> None:
        self._lock: threading.Lock = threading.Lock()
        self._handles: dict[str, subprocess.Popen] = {}  # channel -> Popen
        # Remembered spec per channel, used by restart_dead() to re-launch
        # a crashed process.  A channel is removed from _specs when stopped
        # intentionally (stop / stop_all) so the watchdog does not revive it.
        self._specs: dict[str, LoopbackSpec] = {}

    # ── Public API ────────────────────────────────────────────────────────

    def start(self, spec: LoopbackSpec) -> None:
        """Launch a ``pw-loopback`` process for *spec*.

        If a process is already registered for ``spec.channel``, it is stopped
        first (ensuring clean re-creation).  The spec is memorised so that
        :meth:`restart_dead` can revive the process if it crashes later.

        Parameters
        ----------
        spec:
            Fully resolved loopback specification.
        """
        with self._lock:
            self._stop_unlocked(spec.channel)
            argv = _build_pw_loopback_argv(spec)
            _log.info("Starting loopback %r: %s", spec.channel, " ".join(argv))
            proc = subprocess.Popen(argv)
            self._handles[spec.channel] = proc
            # Remember spec AFTER a successful Popen so a failed launch
            # (OSError) doesn't leave a stale entry that the watchdog would
            # keep trying to re-start forever.
            self._specs[spec.channel] = spec

    def stop(self, channel: str) -> None:
        """Terminate the loopback process for *channel*.

        Sends SIGTERM and waits up to :data:`_TERMINATE_TIMEOUT` seconds; if
        the process has not exited by then, SIGKILL is sent.  No-op if the
        channel is not registered or the process has already exited.

        The spec for *channel* is removed so that :meth:`restart_dead` will
        not attempt to revive an intentionally-stopped loopback.

        Parameters
        ----------
        channel:
            Logical channel name (``"game"``, ``"chat"``, ``"media"``, …).
        """
        with self._lock:
            self._stop_unlocked(channel)
            # Discard the remembered spec so the watchdog does not revive it.
            self._specs.pop(channel, None)

    def recreate(self, spec: LoopbackSpec) -> None:
        """Stop the existing loopback for *spec.channel*, then start a new one.

        Parameters
        ----------
        spec:
            Fully resolved loopback specification (may have an updated target).
        """
        # start() already calls _stop_unlocked internally — no double-lock needed.
        self.start(spec)

    def stop_all(self) -> None:
        """Stop all registered loopback processes.

        All remembered specs are discarded so :meth:`restart_dead` will not
        attempt to revive any of them after a deliberate shutdown.
        """
        with self._lock:
            for channel in list(self._handles.keys()):
                self._stop_unlocked(channel)
            # Clear specs for intentionally-stopped channels so the watchdog
            # does not attempt to revive them.
            self._specs.clear()

    def recreate_all(self, specs: list[LoopbackSpec]) -> None:
        """Stop all current loopbacks, then start one for each spec.

        Parameters
        ----------
        specs:
            List of fully resolved loopback specifications.  Channels not
            present in *specs* are simply stopped; new channels in *specs*
            that were not previously running are started.
        """
        # Stop all first so the old nodes are torn down before new ones come up.
        self.stop_all()
        for spec in specs:
            self.start(spec)

    def specs(self) -> dict[str, "LoopbackSpec"]:
        """Return a shallow copy of the current spec registry.

        The copy is safe to iterate outside the lock.  Mutating the returned
        dict does not affect the internal state; however, the
        :class:`LoopbackSpec` values themselves are shared (they are
        immutable dataclasses so this is fine).

        Returns
        -------
        dict[str, LoopbackSpec]
            Mapping of channel name → remembered spec for every channel that
            has been started and not yet intentionally stopped.
        """
        with self._lock:
            return dict(self._specs)

    def is_running(self, channel: str) -> bool:
        """Return True if the loopback process for *channel* is still alive.

        Parameters
        ----------
        channel:
            Logical channel name.
        """
        with self._lock:
            proc = self._handles.get(channel)
            if proc is None:
                return False
            return proc.poll() is None

    def restart_dead(self) -> list[str]:
        """Restart any loopback process that has died unexpectedly.

        Iterates over every channel that has a remembered spec (i.e. was
        started and not intentionally stopped).  For each one whose process
        has exited (``poll() is not None``) or was never tracked, the process
        is re-launched using the memorised :class:`LoopbackSpec`.

        Channels stopped deliberately via :meth:`stop` or :meth:`stop_all`
        have their spec removed from :attr:`_specs`, so this method will
        **not** revive them.

        This method is thread-safe and is designed to be called from the
        :meth:`CoreEngine._loopback_watchdog` coroutine at a regular cadence.

        Returns
        -------
        list[str]
            Logical channel names that were restarted (empty list if all
            loopbacks are healthy or nothing is registered).
        """
        restarted: list[str] = []
        with self._lock:
            for channel, spec in list(self._specs.items()):
                proc = self._handles.get(channel)
                is_dead = proc is None or proc.poll() is not None
                if not is_dead:
                    continue
                # The process has crashed — attempt to re-launch it.
                _log.warning(
                    "Loopback %r died unexpectedly (rc=%s) — restarting",
                    channel,
                    proc.returncode if proc is not None else "N/A",
                )
                try:
                    argv = _build_pw_loopback_argv(spec)
                    new_proc = subprocess.Popen(argv)
                    self._handles[channel] = new_proc
                    restarted.append(channel)
                    _log.info(
                        "Loopback %r restarted (pid=%d)", channel, new_proc.pid
                    )
                except Exception as exc:
                    _log.error(
                        "Failed to restart loopback %r: %r — will retry next cycle",
                        channel, exc,
                    )
        return restarted

    # ── Internal helpers ──────────────────────────────────────────────────

    def _stop_unlocked(self, channel: str) -> None:
        """Stop the process for *channel* without acquiring the lock.

        Must only be called from within a ``with self._lock`` block.
        """
        proc = self._handles.pop(channel, None)
        if proc is None:
            return
        if proc.poll() is not None:
            # Already dead — nothing to do.
            _log.debug("Loopback %r had already exited (rc=%s)", channel, proc.returncode)
            return
        _log.info("Stopping loopback %r (pid=%d)", channel, proc.pid)
        try:
            proc.terminate()
            try:
                proc.wait(timeout=_TERMINATE_TIMEOUT)
            except subprocess.TimeoutExpired:
                _log.warning(
                    "Loopback %r (pid=%d) did not exit after SIGTERM — sending SIGKILL",
                    channel, proc.pid,
                )
                proc.kill()
                try:
                    proc.wait(timeout=_TERMINATE_TIMEOUT)
                except subprocess.TimeoutExpired:
                    _log.error(
                        "Loopback %r (pid=%d) survived SIGKILL — leaking process",
                        channel, proc.pid,
                    )
        except OSError as exc:
            _log.warning("Error stopping loopback %r: %s", channel, exc)


# ── Convenience factory ──────────────────────────────────────────────────────

def make_specs(
    sonar: bool,
    physical_game: str,
    physical_chat: str,
    device_name: str = "Arctis",
) -> list[LoopbackSpec]:
    """Build the three standard Arctis LoopbackSpec objects.

    This helper is the only place where Sonar vs. simple-mode target
    resolution happens; it deliberately accepts pre-resolved physical output
    node names so that this module stays free of device_state imports.

    Parameters
    ----------
    sonar:
        When True, Game/Chat/Media loopbacks target their respective Sonar EQ
        filter-chain input nodes.  When False, they route directly to the
        physical ALSA outputs.
    physical_game:
        ALSA output node name for the game/media path (e.g.
        ``"alsa_output.usb-SteelSeries_Arctis...pro-output-1"``).
    physical_chat:
        ALSA output node name for the chat path (e.g.
        ``"alsa_output.usb-SteelSeries_Arctis...pro-output-0"``).
    device_name:
        Short human-readable device name prepended to the description
        (e.g. ``"Arctis Nova Pro Wireless"``).

    Returns
    -------
    list[LoopbackSpec]
        Three specs: game, chat, media.
    """
    specs: list[LoopbackSpec] = []
    for sink in DEFAULT_SINKS:
        if sonar:
            target = sink["sonar_target"]
        elif sink["channel"] == "chat":
            target = physical_chat
        else:
            target = physical_game
        specs.append(LoopbackSpec(
            channel=sink["channel"],
            capture_name=sink["capture_name"],
            playback_name=sink["playback_name"],
            target=target,
            description=f"{device_name} {sink['description']}",
        ))
    return specs
