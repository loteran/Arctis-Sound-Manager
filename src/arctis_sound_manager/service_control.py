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
    # No dinit service for the GUI — handled via XDG autostart in autostart.py.
    "arctis-gui":          {"systemd": "arctis-gui",          "dinit": None},
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


def _run(cmd: list[str], timeout: float | None, capture: bool) -> bool:
    try:
        kwargs: dict = {}
        if capture:
            kwargs["capture_output"] = True
            kwargs["text"] = True
        if timeout is not None:
            kwargs["timeout"] = timeout
        result = subprocess.run(cmd, check=False, **kwargs)
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


def restart(*services: str, timeout: float | None = None, capture: bool = False) -> bool:
    """Restart services. Use this to (re)apply a new config — never ``start``,
    which is a no-op if the service is already running."""
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
            r = subprocess.run(["systemctl", "--user", "is-active", real],
                               capture_output=True, text=True)
            return r.stdout.strip() == "active"
        r = subprocess.run(["dinitctl", "status", real], capture_output=True, text=True)
        # dinit status prints "State: STARTED" for a running service.
        return "STARTED" in r.stdout
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
            r = subprocess.run(["systemctl", "--user", "is-enabled", real],
                               capture_output=True, text=True)
            return r.stdout.strip() == "enabled"
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
