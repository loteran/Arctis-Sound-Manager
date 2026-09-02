# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Central init-system abstraction for ASM.

Every part of ASM that needs to start/stop/restart/enable a service (the
PipeWire stack, filter-chain, arctis-manager, etc.) goes through this module
instead of calling ``systemctl``/``dinitctl`` directly. This:

* maps a single *logical* service name to the correct real name per init
  system (the classic trap: ``filter-chain`` on systemd is
  ``pipewire-filter-chain`` on dinit — issue #25);
* never crashes when the init manager is absent (e.g. ``systemctl`` missing on
  Artix/dinit) — it logs and returns ``False`` instead of raising
  ``FileNotFoundError``;
* makes the ``start`` vs ``restart`` distinction explicit in one place, so a
  config-reload always uses ``restart`` (``start`` is a no-op if the service is
  already running, which silently drops the new config — the root cause of the
  "EQ does nothing" reports on dinit).

All functions accept *logical* names. Use :func:`restart` to apply new configs.
"""

import logging
import shutil
import subprocess
from typing import Literal

from arctis_sound_manager.init_system import detect_init

logger = logging.getLogger(__name__)

Init = Literal["systemd", "dinit", "unknown"]

# Logical name -> real service name per init system.
# A value of ``None`` means "this init system has no such service" (e.g. the
# GUI is launched via XDG autostart on dinit, not a dinit service).
_SERVICE_MAP: dict[str, dict[str, str | None]] = {
    "pipewire":            {"systemd": "pipewire",            "dinit": "pipewire"},
    "wireplumber":         {"systemd": "wireplumber",         "dinit": "wireplumber"},
    "pipewire-pulse":      {"systemd": "pipewire-pulse",      "dinit": "pipewire-pulse"},
    # The divergence that caused issue #25:
    "filter-chain":        {"systemd": "filter-chain",        "dinit": "pipewire-filter-chain"},
    "arctis-manager":      {"systemd": "arctis-manager",      "dinit": "arctis-manager"},
    "arctis-video-router": {"systemd": "arctis-video-router", "dinit": "arctis-video-router"},
    "arctis-stream-guard": {"systemd": "arctis-stream-guard", "dinit": "arctis-stream-guard"},
    # No dinit service for the GUI — handled via XDG autostart in autostart.py.
    #
    # The systemd unit is named for the desktop entry, not for the project, and
    # that is load-bearing: xdg-desktop-portal derives an app id for a
    # non-sandboxed process from the unit its cgroup names, matching
    # `app-<AppID>[-<random>].service|.scope`. Under `arctis-gui.service` the id
    # comes out empty and the GlobalShortcuts portal refuses the session with
    # "NotAllowed: An app id is required" — the clip shortcut never binds and the
    # Clips page reports no global shortcut. `app-ArctisManager.service` resolves
    # to ArctisManager.desktop and binds. Setting the app id from inside Qt does
    # not help: the host portal Registry it would use is not available outside a
    # sandbox. Callers keep saying "arctis-gui" — this map is the one place the
    # real name lives.
    "arctis-gui":          {"systemd": "app-ArctisManager",   "dinit": None},
}


def _resolve(logical: str, init: Init) -> str | None:
    """Map a logical service name to the real name for ``init``.

    Unknown logical names pass through unchanged (callers may use real names
    for one-off services not in the map); ``None`` means "not applicable here".
    """
    entry = _SERVICE_MAP.get(logical)
    if entry is None:
        return logical
    return entry.get(init)


def manager_available() -> bool:
    """True if a usable service manager binary is present for the active init."""
    init = detect_init()
    if init == "dinit":
        return shutil.which("dinitctl") is not None
    if init == "systemd":
        return shutil.which("systemctl") is not None
    return False


# Absolute-path cache so subprocess can take the posix_spawn (vfork) path
# instead of fork()+exec. The daemon (core.py) restarts the PipeWire stack
# through this module while libusb device I/O runs in a sibling thread; a
# fork() there replays libusb's pthread_atfork handlers and COW-copies the VM
# mid-poll(), a heap-corruption vector (issue #123). posix_spawn needs an
# absolute executable *and* close_fds=False, so we pin both. Safe: the spawned
# tools are short-lived and the daemon's fds are O_CLOEXEC.
_ABS_EXE_CACHE: dict[str, str] = {}


def _abs_exe(name: str) -> str:
    """Resolve a CLI tool to its absolute path (cached); bare name if absent."""
    if name not in _ABS_EXE_CACHE:
        _ABS_EXE_CACHE[name] = shutil.which(name) or name
    return _ABS_EXE_CACHE[name]


def _run(cmd: list[str], timeout: float | None, capture: bool) -> bool:
    try:
        kwargs: dict = {"close_fds": False}
        if capture:
            kwargs["capture_output"] = True
            kwargs["text"] = True
        if timeout is not None:
            kwargs["timeout"] = timeout
        result = subprocess.run([_abs_exe(cmd[0]), *cmd[1:]], check=False, **kwargs)
        if result.returncode != 0:
            stderr = (getattr(result, "stderr", "") or "").strip()
            logger.warning("service_control: %s failed (rc=%s) %s",
                           " ".join(cmd), result.returncode, stderr)
            return False
        return True
    except FileNotFoundError:
        logger.warning("service_control: %s not found — skipping %s", cmd[0], " ".join(cmd))
        return False
    except subprocess.TimeoutExpired:
        logger.warning("service_control: %s timed out", " ".join(cmd))
        return False
    except OSError as e:
        logger.warning("service_control: %s errored: %s", " ".join(cmd), e)
        return False


def run_raw(cmd: list[str], timeout: float | None = 10.0) -> subprocess.CompletedProcess | None:
    """Run a literal init-manager command through this module's exception-safe,
    posix_spawn-safe plumbing (issue #123), for one-off/migration callers that
    need the real output or a literal service name — not the logical-name
    mapping that :func:`restart`/:func:`start`/:func:`stop`/etc. apply.

    Two cases the logical-name helpers cannot serve:

    * The caller needs stdout/stderr (e.g. ``asm-setup`` checking for
      "started" in ``dinitctl status`` output, or printing a failure reason).
    * The caller needs a *literal* service name that :data:`_SERVICE_MAP`
      deliberately maps to ``None`` on this init system (e.g. a stale literal
      ``arctis-gui`` dinit service left over from before XDG autostart —
      ``_SERVICE_MAP["arctis-gui"]["dinit"]`` is ``None`` by design, meaning
      "no such service normally exists here", which is exactly why the
      one-off migration cleanup for a service that *does* exist needs to
      bypass it).

    Every other caller should prefer the logical-name helpers above — this
    exists so those callers don't have to spawn their own raw
    ``subprocess.run`` (which is how EXT-2 happened: every call in
    ``asm-setup``'s ``_setup_dinit_services()`` guarded only
    ``TimeoutExpired``, never ``FileNotFoundError``, so a dinit box missing
    ``dinitctl`` from PATH died with an uncaught traceback partway through
    setup instead of a clear message).

    Returns ``None`` — never raises — if the binary is missing, the call
    times out, or another ``OSError`` occurs. Callers should treat ``None`` as
    "could not run this, continue best-effort" and print/log accordingly.
    """
    try:
        return subprocess.run([_abs_exe(cmd[0]), *cmd[1:]], capture_output=True, text=True,
                              timeout=timeout, check=False, close_fds=False)
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("service_control: run_raw(%s) failed: %s", " ".join(cmd), exc)
        return None


def _action(verb: str, services: tuple[str, ...], timeout: float | None, capture: bool) -> bool:
    """Run ``verb`` (start/stop/restart) on one or more logical services.

    systemd accepts multiple units in a single ``systemctl --user`` call; dinit
    takes one service per ``dinitctl`` invocation, so we loop. Returns True only
    if every underlying command succeeded.
    """
    init = detect_init()
    if init == "unknown" or not manager_available():
        logger.warning("service_control: no usable init manager — skipping %s %s",
                       verb, " ".join(services))
        return False

    real = [r for r in (_resolve(s, init) for s in services) if r]
    if not real:
        return True  # nothing applicable on this init (e.g. arctis-gui on dinit)

    if init == "systemd":
        return _run(["systemctl", "--user", verb, *real], timeout, capture)

    # dinit: one service per call
    ok = True
    for svc in real:
        ok = _run(["dinitctl", verb, svc], timeout, capture) and ok
    return ok


# Restarting any of these tears down the Arctis_* sinks and the filter-chain EQ
# nodes, so every stream sitting on a channel is displaced for a few seconds.
_GRAPH_REBUILDING = {
    "filter-chain", "pipewire", "pipewire-pulse", "wireplumber", "arctis-manager",
}


def restart(*services: str, timeout: float | None = None, capture: bool = False) -> bool:
    """Restart services. Use this to (re)apply a new config — never ``start``,
    which is a no-op if the service is already running.

    Restarting an audio service opens a reconfiguration window
    (:mod:`arctis_sound_manager.audio_reconfig`) so ``arctis-video-router`` does
    not mistake the streams PipeWire displaces during the restart for moves the
    user made, and save the displacement as their routing override. It is opened
    here rather than at each call site because every path that rebuilds the
    graph goes through this function, and the ones that did not open it (the
    tray's "restart the audio engine", the daemon's stale-config repair) are
    exactly the ones where the reassignment went unnoticed. Callers that also
    restore streams afterwards should use
    :func:`~arctis_sound_manager.audio_reconfig.audio_reconfiguration`, which
    closes the window once the graph has settled instead of waiting for it to
    expire.
    """
    if _GRAPH_REBUILDING.intersection(services):
        try:
            from arctis_sound_manager import audio_reconfig
            audio_reconfig.begin()
        except Exception as exc:  # never let this block a restart
            logger.warning("service_control: could not open the reconfig window: %s", exc)
    return _action("restart", services, timeout, capture)


def start(*services: str, timeout: float | None = None, capture: bool = False) -> bool:
    """Start services (no-op if already running). For applying new config use
    :func:`restart` instead."""
    return _action("start", services, timeout, capture)


def stop(*services: str, timeout: float | None = None, capture: bool = False) -> bool:
    """Stop services."""
    return _action("stop", services, timeout, capture)


def enable(service: str, now: bool = False) -> bool:
    """Enable a service at boot/login. ``now=True`` also starts it (systemd)."""
    init = detect_init()
    if init == "unknown" or not manager_available():
        logger.warning("service_control: no usable init manager — skipping enable %s", service)
        return False
    real = _resolve(service, init)
    if not real:
        return True  # not applicable (e.g. arctis-gui on dinit -> XDG autostart)
    if init == "systemd":
        args = ["systemctl", "--user", "enable", "--now", real] if now else \
               ["systemctl", "--user", "enable", real]
        return _run(args, None, True)
    ok = _run(["dinitctl", "enable", real], None, True)
    if now:
        ok = _run(["dinitctl", "start", real], None, True) and ok
    return ok


def disable(service: str) -> bool:
    """Disable a service so it no longer starts at boot/login."""
    init = detect_init()
    if init == "unknown" or not manager_available():
        return False
    real = _resolve(service, init)
    if not real:
        return True
    if init == "systemd":
        return _run(["systemctl", "--user", "disable", real], None, True)
    return _run(["dinitctl", "disable", real], None, True)


def is_active(service: str) -> bool:
    """True if the service is currently running."""
    init = detect_init()
    if not manager_available():
        return False
    real = _resolve(service, init)
    if not real:
        return False
    try:
        if init == "systemd":
            r = subprocess.run([_abs_exe("systemctl"), "--user", "is-active", real],
                               capture_output=True, text=True, timeout=5, close_fds=False)
            return r.stdout.strip() == "active"
        r = subprocess.run([_abs_exe("dinitctl"), "status", real], capture_output=True, text=True,
                           timeout=5, close_fds=False)
        # dinit status prints "State: STARTED" for a running service.
        return "STARTED" in r.stdout
    except subprocess.TimeoutExpired:
        logger.warning("service_control: is_active(%s) timed out", service)
        return False
    except (FileNotFoundError, OSError):
        return False


def is_enabled(service: str) -> bool:
    """True if the service is enabled at boot/login."""
    init = detect_init()
    if not manager_available():
        return False
    real = _resolve(service, init)
    if not real:
        return False
    if init == "systemd":
        try:
            r = subprocess.run([_abs_exe("systemctl"), "--user", "is-enabled", real],
                               capture_output=True, text=True, timeout=5, close_fds=False)
            return r.stdout.strip() == "enabled"
        except subprocess.TimeoutExpired:
            logger.warning("service_control: is_enabled(%s) timed out", service)
            return False
        except (FileNotFoundError, OSError):
            return False
    # dinit has no is-enabled; reuse the symlink-walking helper.
    from arctis_sound_manager.init_system import is_dinit_service_enabled
    return is_dinit_service_enabled(real)


def daemon_reload() -> bool:
    """Reload unit files (systemd only; no-op on dinit)."""
    if detect_init() != "systemd" or shutil.which("systemctl") is None:
        return True
    return _run(["systemctl", "--user", "daemon-reload"], None, True)


def nrestarts(service: str) -> int | None:
    """Return systemd's NRestarts counter for ``service`` (a crash-loop signal),
    or None when it can't be determined (non-systemd, missing binary, parse
    error). Routed through here so no module issues raw ``systemctl`` calls —
    which also keeps every spawn on the posix_spawn path (issue #123)."""
    if detect_init() != "systemd" or shutil.which("systemctl") is None:
        return None
    real = _resolve(service, "systemd")
    if not real:
        return None
    try:
        r = subprocess.run(
            [_abs_exe("systemctl"), "--user", "show", real, "-p", "NRestarts"],
            capture_output=True, text=True, timeout=5, close_fds=False,
        )
        for line in r.stdout.splitlines():
            if line.startswith("NRestarts="):
                return int(line.split("=", 1)[1].strip())
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired, ValueError) as exc:
        logger.debug("service_control: NRestarts(%s) query failed: %s", service, exc)
    return None


def restart_detached(*services: str, delay: float = 1.0) -> None:
    """Restart services from a detached child that outlives the caller.

    Used when the GUI must exit *before* the PipeWire stack restarts (otherwise
    the restart kills the GUI's own audio clients mid-call). Fire-and-forget.
    """
    init = detect_init()
    if init == "unknown" or not manager_available():
        logger.warning("service_control: no usable init manager — skipping detached restart")
        return
    real = [r for r in (_resolve(s, init) for s in services) if r]
    if not real:
        return
    if init == "systemd":
        inner = f"sleep {delay} && systemctl --user restart {' '.join(real)}"
    else:
        chain = " && ".join(f"dinitctl restart {svc}" for svc in real)
        inner = f"sleep {delay} && {chain}"
    try:
        subprocess.Popen(["sh", "-c", inner],
                         start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as e:
        logger.warning("service_control: detached restart failed: %s", e)
