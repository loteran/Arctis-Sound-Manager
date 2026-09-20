# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Detect that the package was upgraded underneath a running process.

A package manager replaces files on disk; it does not touch processes. Python
loaded its modules at startup and keeps executing them, so after `pacman -Syu`,
`dnf upgrade` or `apt upgrade`, the GUI and the daemon go on running the version
they started with — usually until the next reboot.

That is worse than it sounds. Someone upgrades *because* of a fix, sees the bug
still there, and reports it against a version number they are not running. The
bug report says 1.2.14; the code answering the report is 1.2.12.

Package scriptlets restart the user services (see
``scripts/restart-user-services.sh``). The GUI is commonly started by the
desktop's autostart rather than systemd, so there is no unit for a scriptlet to
restart, and killing it from root would run whatever exit path the old code
has. Instead the scriptlet *asks*: `asm-gui --restart` knocks on the running
instance's single-instance socket (:func:`request_gui_restart`) and the GUI
replaces itself in place, the same way it does when its own poll
(:func:`upgraded_under_us`) notices the upgrade. Two triggers for one exec:
the poll alone left a tray on yesterday's code for a whole session when it
did not fire, with the clip shortcut and the capture on it.
"""

from __future__ import annotations

import importlib
import logging
import os
import socket
import sys
import tempfile

from arctis_sound_manager import service_control as sc
from arctis_sound_manager.utils import project_version

log = logging.getLogger(__name__)

#: Version of the code this process is actually executing. Captured at import,
#: which for the GUI and the daemon is startup — before any upgrade can land.
RUNNING_VERSION: str = project_version()

_UNKNOWN = ("", "dev")


def _package_stamp() -> float | None:
    """When the installed package's own code last changed on disk.

    The modification time of this module's directory, which every package
    manager rewrites when it lays the new files down. Version alone missed
    a rebuild of the same version (a local package with a bumped release, a
    distro's -2), so the GUI kept running the old code with no banner.
    None for anything that is not an installed package worth watching.
    """
    try:
        return os.stat(os.path.dirname(os.path.abspath(__file__))).st_mtime
    except OSError:
        return None


#: The package's on-disk stamp at startup — before any upgrade can land.
RUNNING_STAMP: float | None = _package_stamp()

# Logical names, resolved per init system by service_control (systemd or
# dinit — see service_control._SERVICE_MAP). Restarting user services used to
# be hand-rolled here with a bare `shutil.which("systemctl")` check, which
# meant dinit boxes (Artix, Arch+dinit) got no restart at all: the "Restart
# Now" banner relaunched the GUI while the daemon kept running the old code
# forever. service_control is the one module allowed to shell out to
# systemctl/dinitctl; route through it like every other call site does.
_USER_SERVICES = (
    "arctis-manager",
    "arctis-video-router",
    "arctis-stream-guard",
)


def installed_version() -> str:
    """Version currently on disk, re-read rather than remembered."""
    # importlib.metadata caches its sys.path scan; an upgrade that swaps the
    # dist-info directory would otherwise stay invisible for the life of the
    # process.
    importlib.invalidate_caches()
    return project_version()


def upgraded_under_us() -> str | None:
    """The newly installed version if the package changed under us, else None.

    Any comparison involving an unknown version ("dev", a source checkout, an
    editable install) returns None: there is nothing meaningful to compare, and
    a spurious "please restart" banner is worse than no banner.
    """
    if RUNNING_VERSION in _UNKNOWN:
        return None
    on_disk = installed_version()
    if on_disk in _UNKNOWN:
        return None
    if on_disk != RUNNING_VERSION:
        return on_disk
    # Same version string, but the files under us were replaced: a rebuild
    # of the same release. Just as stale, and just as invisible before.
    stamp = _package_stamp()
    if RUNNING_STAMP is not None and stamp is not None and stamp != RUNNING_STAMP:
        return on_disk
    return None


def restart_user_services() -> None:
    """Restart ASM's own user services, if they are running — systemd or dinit.

    Only services already active are restarted (the try-restart behaviour):
    someone who stopped ASM on purpose must not get it back from an upgrade.
    Needs no privileges — these are the user's own units. Delegates entirely
    to service_control, the one module allowed to shell out to
    systemctl/dinitctl, so this works the same whether the active init is
    systemd or dinit; it used to check only for `systemctl` and silently do
    nothing on dinit boxes.
    """
    if not sc.manager_available():
        log.warning("Could not restart user services: no usable init manager "
                    "(neither systemctl nor dinitctl found)")
        return
    running = [name for name in _USER_SERVICES if sc.is_active(name)]
    if not running:
        return
    if not sc.restart(*running, timeout=30):
        log.warning("Could not restart user services: %s", ", ".join(running))


#: Name of the GUI's single-instance socket (scripts/gui.py listens on it
#: through QLocalServer). Shared from here so the sender needs no Qt.
GUI_SERVER_NAME = "ArctisManagerGui"

#: What a sender writes to that socket to have the GUI exec itself on the
#: code now on disk. The GUI answers "ok" once it has read it.
GUI_RESTART_COMMAND = b"restart"


def gui_socket_path() -> str:
    """Where QLocalServer puts a socket named without a slash: the temp dir."""
    return os.path.join(tempfile.gettempdir(), GUI_SERVER_NAME)


def request_gui_restart(timeout: float = 5.0) -> bool:
    """Ask the running GUI, if any, to restart on the code now on disk.

    A plain Unix socket rather than QLocalSocket: this runs from a package
    scriptlet as the user, with no display and no reason to import Qt. True
    when a GUI was there to take the request; False when nothing listens,
    which is not an error — there is simply nothing to restart.
    """
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(gui_socket_path())
            sock.sendall(GUI_RESTART_COMMAND)
            # The reply only says it was picked up; the command is already in
            # the GUI's buffer either way, so a slow answer is not a failure.
            try:
                sock.recv(16)
            except OSError:
                pass
    except OSError as exc:
        log.debug("no GUI to restart: %s", exc)
        return False
    return True


def restart_gui() -> None:
    """Replace this process with a fresh one, running the code now on disk.

    execv rather than spawn-and-exit: same pid, no window flash from an
    orphaned parent, and no chance of two GUIs briefly fighting over the tray
    icon and the D-Bus name.
    """
    try:
        os.execv(sys.executable, [sys.executable, *sys.argv])
    except OSError as exc:
        # Never leave the caller believing the restart happened.
        log.error("Could not restart the GUI in place: %s", exc)
        raise
