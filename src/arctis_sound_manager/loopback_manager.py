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
import os
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass

_log = logging.getLogger(__name__)


# Resolve pw-loopback to an absolute path (cached) so subprocess.Popen can take
# the posix_spawn (vfork) path instead of fork()+exec. This module spawns
# pw-loopback from the daemon process, which also runs libusb device I/O in a
# sibling thread; a fork() there replays libusb's pthread_atfork handlers and
# COW-copies the VM mid-poll(), a heap-corruption vector (issue #123).
# posix_spawn needs an absolute executable *and* close_fds=False at the Popen
# site. close_fds=False is safe: the daemon's fds are all O_CLOEXEC (Python
# opens fds O_CLOEXEC by default since PEP 446, and libusb sets it on usbfs
# handles), so nothing leaks into the long-lived pw-loopback child past exec.
_PW_LOOPBACK_EXE: str | None = None


def _pw_loopback_exe() -> str:
    global _PW_LOOPBACK_EXE
    if _PW_LOOPBACK_EXE is None:
        _PW_LOOPBACK_EXE = shutil.which("pw-loopback") or "pw-loopback"
    return _PW_LOOPBACK_EXE


# ── PipeWire socket resolution ────────────────────────────────────────────────

def _resolve_pipewire_socket() -> str | None:
    """Resolve the absolute path of the active PipeWire socket.

    Probes candidate locations in order of preference:

    1. ``{XDG_RUNTIME_DIR}/{PIPEWIRE_REMOTE}`` — honours both env vars so
       that custom socket names (e.g. ``pipewire-1`` from a nested session)
       are respected.
    2. ``/run/user/{uid}/pipewire-0`` — hard-coded host-side default, used
       as a fallback when ``XDG_RUNTIME_DIR`` points inside a Distrobox
       container mount that differs from the host socket path.

    Returns the first path that passes ``os.path.exists``, or ``None`` when
    no reachable socket is found.  Never raises — callers can treat ``None``
    as "socket unknown, inherit parent env".

    Parameters
    ----------
    (none)

    Returns
    -------
    str | None
        Absolute socket path, or ``None``.
    """
    try:
        uid = os.getuid()
        runtime_dir = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{uid}"
        remote = os.environ.get("PIPEWIRE_REMOTE") or "pipewire-0"
        candidate = f"{runtime_dir}/{remote}"
        if os.path.exists(candidate):
            return candidate
        # Fallback: always probe the host-side default socket, because under
        # Distrobox ``XDG_RUNTIME_DIR`` may point to a container-private path
        # even though PipeWire runs on the host and its socket lives at the
        # standard location.  Skipped when it equals candidate (no dup check).
        host_fallback = f"/run/user/{uid}/pipewire-0"
        if candidate != host_fallback and os.path.exists(host_fallback):
            return host_fallback
        return None
    except Exception:
        return None


def _pw_loopback_env() -> dict[str, str] | None:
    """Build an env dict that pins the active PipeWire socket for pw-loopback.

    Returns a *copy* of ``os.environ`` with ``XDG_RUNTIME_DIR`` overridden
    to the socket's parent directory and ``PIPEWIRE_REMOTE`` overridden to
    the socket's basename.  This forces every spawned ``pw-loopback`` child
    to connect to the same socket that was active at spawn time, even if the
    daemon's own environment has a stale or container-relative
    ``XDG_RUNTIME_DIR`` (the typical Bazzite / Steam Game Mode + Distrobox
    scenario that triggers issue #90).

    Returns ``None`` when ``_resolve_pipewire_socket`` cannot find a socket.
    Callers should then pass ``env=None`` to ``subprocess.Popen``, which
    inherits the parent environment unchanged — preserving the pre-#90
    behaviour as a safe fallback so the daemon never regresses on systems
    where no socket file is found.

    Returns
    -------
    dict[str, str] | None
        Pinned environment mapping, or ``None`` when no socket is found.
    """
    socket = _resolve_pipewire_socket()
    if socket is None:
        return None
    env = dict(os.environ)
    env["XDG_RUNTIME_DIR"] = os.path.dirname(socket)
    env["PIPEWIRE_REMOTE"] = os.path.basename(socket)
    return env


def current_pipewire_socket_signature() -> str:
    """Return a stable identifier for the currently active PipeWire socket.

    Used by :meth:`~arctis_sound_manager.core.CoreEngine._loopback_watchdog`
    to detect PipeWire socket changes (e.g. Gamescope / Steam Game Mode
    session switch under Distrobox) that require all loopbacks to be
    recreated so they rebind to the new socket.

    Returns the absolute socket path, or an empty string when no socket can
    be resolved.  Never raises.

    Returns
    -------
    str
        Active socket path or ``""`` when unknown.
    """
    return _resolve_pipewire_socket() or ""


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
        ``target.object``/``node.target`` for the playback side — the downstream node that
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
    ``target.object`` (WirePlumber >= 0.5) plus ``node.target`` (0.4.x compat),
    ``stream.dont-remix=false`` (which lets PipeWire expand
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
                            target.object=effect_input.sonar-media-eq
                            node.target=effect_input.sonar-media-eq
                            node.dont-fallback=true node.autoconnect=false
                            node.linger=true latency.msec=50'

    The playback node uses ``node.autoconnect=false`` so WirePlumber never
    routes it: ASM owns the playback→EQ link itself (issue #100), which makes
    the routing immune to any competing output device on the system.

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
        # WirePlumber >= 0.5 resolves target.object (object.serial / node.name
        # lookup) with priority over node.target; without it the stream can be
        # linked to the physical ALSA sink instead of the EQ (issue #102).
        f" target.object={spec.target}"
        f" node.target={spec.target}"   # kept for WirePlumber 0.4.x compat
        # WirePlumber's restore-stream (node.stream.restore-target, default true)
        # re-applies a previously stored target for streams with a stable
        # node.name, overriding target.object at every recreate. A poisoned entry
        # (e.g. a past manual move to the physical ALSA sink) then feeds an endless
        # mislink → watchdog-recreate → restore loop. Opt out per-stream (#100).
        f" state.restore-target=false"
        f" node.dont-fallback=true"
        # Take the playback node OUT of WirePlumber's session policy entirely
        # (issue #100). With autoconnect=false WirePlumber never links this node,
        # so no competing output device — a second USB DAC (e.g. Creative Pebble
        # Nova), the physical headset, whatever the user's default sink is — can
        # ever steal it. ASM owns the link instead and creates it directly
        # (pw_utils.ensure_loopback_link), matched channel-for-channel. The
        # target.object / node.target hints above are kept for documentation and
        # for the brief window before ASM links, but they are no longer relied on
        # to win the tug-of-war against WirePlumber's policy — because there is no
        # tug-of-war anymore.
        f" node.autoconnect=false"
        f" node.linger=true"
        f" latency.msec=50"
    )
    return [
        _pw_loopback_exe(),
        f"--capture-props={capture_props}",
        f"--playback-props={playback_props}",
    ]


# ── Orphan loopback reaping ───────────────────────────────────────────────────
#
# pw-loopback is started with node.linger=true (see _build_pw_loopback_argv)
# so that its PipeWire node survives brief hiccups. That flag has a side
# effect: the *process* also survives its parent. If the ASM daemon dies
# without going through LoopbackManager.stop_all() — crash, `systemctl
# restart`, kill -9 — its pw-loopback children are simply orphaned (reparented
# by the kernel), not terminated. The next daemon starts with an empty
# LoopbackManager._handles and has no way to know they exist, so it launches
# three brand new pw-loopback processes with the exact same node.name as the
# survivors. PipeWire happily accepts the duplicates, and the user ends up
# with Arctis_Game/Chat/Media doubled in their mixer — half of them dead ends
# that audio can silently vanish into.
#
# The fix is a sweep, run right before every launch, that finds and kills any
# pw-loopback process already claiming the node.name we are about to (re)use.

# Root of the process filesystem. Resolved lazily (read at call time, not at
# import time) inside _find_orphan_pw_loopback_pids() so tests can point it
# at a fake tree via monkeypatch without threading a parameter through every
# call site.
_PROC_ROOT = "/proc"


def _read_proc_cmdline(proc_root: str, pid: int) -> list[str] | None:
    """Read and split ``/proc/<pid>/cmdline`` into its argv list.

    ``/proc`` entries can vanish between listing and reading (the process
    exits) or be unreadable (permissions) — both are ordinary races, not
    errors, so this returns ``None`` instead of raising.

    Returns
    -------
    list[str] | None
        The process's argv, or ``None`` if it could not be read or was
        empty (e.g. a zombie, whose cmdline file reads as empty).
    """
    path = os.path.join(proc_root, str(pid), "cmdline")
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except (FileNotFoundError, ProcessLookupError, PermissionError, OSError):
        return None
    if not raw:
        return None
    parts = raw.split(b"\x00")
    if parts and parts[-1] == b"":
        parts = parts[:-1]
    if not parts:
        return None
    return [part.decode("utf-8", errors="replace") for part in parts]


def _capture_node_name(argv: list[str]) -> str | None:
    """Extract the capture-side ``node.name`` from a pw-loopback argv.

    ``--capture-props`` is a *single* argv element containing multiple
    space-separated ``key=value`` pairs (see :func:`_build_pw_loopback_argv`,
    e.g. ``--capture-props=node.name=Arctis_Game media.class=Audio/Sink ...``).
    Matching is done on the exact ``node.name=<value>`` token — never a bare
    substring search — because both of the following must be told apart:

    * ``target.object=Arctis_Game`` (what a DualSense haptics loopback that
      *targets* the Arctis Game sink looks like) must never be confused with
      ``node.name=Arctis_Game`` (the ASM process that *owns* that sink) — we
      only ever look for the ``node.name=`` key, never ``target.object``.
    * ``node.name=Arctis_Game_sink_out`` must not match a search for
      ``node.name=Arctis_Game`` — splitting the props string on whitespace
      delimits each token's value at the next space (or end of string), so
      the extracted value is compared for exact equality, not prefix.

    Returns
    -------
    str | None
        The capture node.name, or ``None`` if this argv has no
        ``--capture-props`` element, or that element carries no
        ``node.name`` token.
    """
    prefix = "--capture-props="
    for arg in argv:
        if not arg.startswith(prefix):
            continue
        props = arg[len(prefix):]
        for token in props.split(" "):
            if token.startswith("node.name="):
                return token[len("node.name="):]
    return None


def _find_orphan_pw_loopback_pids(
    capture_name: str, exclude_pids: "set[int]"
) -> list[tuple[int, str]]:
    """Scan ``/proc`` for pw-loopback processes capturing as *capture_name*.

    Finds survivors of a previous, uncleanly-terminated ASM daemon instance
    (see the module-level comment above for why they exist). Never raises:
    every per-process read is best-effort, and a scan-wide failure (e.g.
    ``/proc`` unreadable) yields an empty list rather than propagating — a
    failed sweep must never block launching the real loopback.

    Parameters
    ----------
    capture_name:
        The exact ``node.name`` of the channel about to be (re)started
        (``spec.capture_name``, e.g. ``"Arctis_Game"``).
    exclude_pids:
        PIDs that must never be reported even if they match — this
        manager's own live children and the current process.

    Returns
    -------
    list[tuple[int, str]]
        ``(pid, node_name)`` pairs for every matching orphan found.
    """
    try:
        entries = os.listdir(_PROC_ROOT)
    except (FileNotFoundError, PermissionError, OSError) as exc:
        _log.warning("Could not list %s to scan for orphan loopbacks: %s", _PROC_ROOT, exc)
        return []

    found: list[tuple[int, str]] = []
    for entry in entries:
        if not entry.isdigit():
            continue
        pid = int(entry)
        if pid in exclude_pids:
            continue
        argv = _read_proc_cmdline(_PROC_ROOT, pid)
        if not argv:
            continue
        exe = argv[0]
        if not (exe == "pw-loopback" or exe.endswith("/pw-loopback")):
            continue
        name = _capture_node_name(argv)
        if name == capture_name:
            found.append((pid, name))
    return found


def _terminate_orphan_pid(pid: int, node_name: str) -> None:
    """Terminate a single orphan pw-loopback process.

    Same SIGTERM-then-SIGKILL discipline as
    :meth:`LoopbackManager._stop_unlocked`, adapted for a PID we hold no
    :class:`subprocess.Popen` handle for — it is not our child (orphans are
    reparented away from the daemon that originally spawned them), so
    ``proc.wait()`` is not available; liveness is instead polled via
    ``os.kill(pid, 0)``.

    Every failure mode here (process already gone, no permission, signal
    delivery error) is logged and swallowed — this must never raise, since
    it runs on the way to launching the real loopback.
    """
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return  # already gone — a race between the scan and the kill
    except (PermissionError, OSError) as exc:
        _log.warning(
            "Could not signal orphan loopback pid=%d (node.name=%s): %s",
            pid, node_name, exc,
        )
        return

    deadline = time.monotonic() + _TERMINATE_TIMEOUT
    while True:
        try:
            os.kill(pid, 0)
        except (ProcessLookupError, PermissionError):
            return  # exited, or we can no longer check — stop trying either way
        if time.monotonic() >= deadline:
            break
        time.sleep(0.05)

    # Re-identify before escalating. os.kill(pid, 0) only proves *a* process
    # holds this pid, not that it is still the one we signalled: if the orphan
    # died on SIGTERM and the kernel handed its pid to something else within
    # the timeout, SIGKILL would land on an innocent bystander. Re-reading the
    # cmdline closes that window — the scan's own kill path has no equivalent
    # exposure because it signals immediately after reading.
    argv = _read_proc_cmdline(_PROC_ROOT, pid)
    if not argv or _capture_node_name(argv) != node_name:
        _log.debug(
            "Orphan loopback pid=%d is no longer node.name=%s — not escalating to SIGKILL",
            pid, node_name,
        )
        return

    _log.warning(
        "Orphan loopback pid=%d (node.name=%s) did not exit after SIGTERM — sending SIGKILL",
        pid, node_name,
    )
    try:
        os.kill(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass


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
            self._reap_orphan_loopbacks_unlocked(spec)
            argv = _build_pw_loopback_argv(spec)
            _log.info("Starting loopback %r: %s", spec.channel, " ".join(argv))
            # Pin the active PipeWire socket via env so pw-loopback always
            # connects to the correct socket even when XDG_RUNTIME_DIR in the
            # daemon's environment is stale or container-relative (issue #90).
            # _pw_loopback_env() returns None when no socket is found, and
            # Popen(env=None) inherits the parent env unchanged — safe fallback.
            proc = subprocess.Popen(argv, env=_pw_loopback_env(), close_fds=False)
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

    def restart_dead(
        self, skip_channels: "set[str] | None" = None
    ) -> list[str]:
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

        Parameters
        ----------
        skip_channels:
            Optional set of channel names to exclude from restart even if
            their process has died.  Used by the anti-flapping guard in
            :meth:`CoreEngine._loopback_watchdog` to keep a channel suppressed
            during its cooldown period.  ``None`` (default) behaves exactly
            like the previous no-argument version — all dead channels are
            restarted.

        Returns
        -------
        list[str]
            Logical channel names that were restarted (empty list if all
            loopbacks are healthy or nothing is registered).
        """
        _skip: set[str] = skip_channels if skip_channels is not None else set()
        restarted: list[str] = []
        with self._lock:
            for channel, spec in list(self._specs.items()):
                if channel in _skip:
                    # Channel is in cooldown — do not revive it this cycle.
                    continue
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
                    # Same orphan sweep as start() — restart_dead() spawns its
                    # own replacement process rather than calling start()
                    # (which would deadlock: self._lock is non-reentrant and
                    # is already held here), so the reap has to be repeated at
                    # this second spawn site to close the same window.
                    self._reap_orphan_loopbacks_unlocked(spec)
                    argv = _build_pw_loopback_argv(spec)
                    # Same socket-pinning as in start() — ensures the revived
                    # process connects to the correct PipeWire socket (issue #90).
                    new_proc = subprocess.Popen(argv, env=_pw_loopback_env(), close_fds=False)
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

    def _reap_orphan_loopbacks_unlocked(self, spec: LoopbackSpec) -> None:
        """Kill any untracked pw-loopback process already using spec.capture_name.

        Recovers from a daemon that died without calling :meth:`stop_all`
        (crash, ``systemctl restart``, kill -9): its ``pw-loopback`` children
        (started with ``node.linger=true``) outlive it as orphans this
        instance's :attr:`_handles` never learned about, and would otherwise
        end up duplicated when this instance launches its own loopback for
        the same channel — see the module-level comment above
        :func:`_find_orphan_pw_loopback_pids`.

        Must only be called from within a ``with self._lock`` block, right
        before spawning a new process for *spec*. Never raises — a failed
        sweep must not prevent the real loopback from launching.
        """
        try:
            exclude_pids = {os.getpid()}
            exclude_pids.update(proc.pid for proc in self._handles.values())
            orphans = _find_orphan_pw_loopback_pids(spec.capture_name, exclude_pids)
        except Exception as exc:  # defensive: the scan already swallows its own errors
            _log.warning("Orphan loopback scan failed for channel %r: %s", spec.channel, exc)
            return
        for pid, node_name in orphans:
            _log.warning(
                "Killing orphan pw-loopback pid=%d node.name=%s (channel %r) — "
                "survivor of a previous ASM daemon instance that did not shut "
                "down cleanly",
                pid, node_name, spec.channel,
            )
            _terminate_orphan_pid(pid, node_name)

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
