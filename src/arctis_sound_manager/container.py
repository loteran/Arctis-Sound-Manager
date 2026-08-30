# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Where ASM is running, and how to reach the host from inside a container.

udev, polkit and system packages all live on the host. Inside a distrobox or
a toolbox they are one namespace away, and the two halves of ASM disagreed
about that: ``udev_checker`` learned to *read* the host's rules through
``distrobox-host-exec``, while ``asm-cli udev write-rules`` kept writing into
the container's own /etc, where udev never looks. The dialog then reopened on
every launch saying the rules were missing, right after a "Run" that had
reported success — the loop a Bazzite user was stuck in.

Three copies of the same ``_running_in_container`` helper had also drifted
into ``gui/system_deps_dialog``, ``systemd`` and ``udev_checker``. This is the
one they share.
"""

import logging
import shutil
import subprocess

_logger = logging.getLogger(__name__)

# distrobox-host-exec talks to the host through the container's own socket;
# calls are cheap but not free, and a wedged host would otherwise hang a GUI
# thread. Every call here is bounded.
_HOST_CALL_TIMEOUT = 5


def running_in_container() -> bool:
    """True inside distrobox / toolbox / flatpak / snap / docker.

    Imported lazily and defensively, for the same reason systemd.py does it:
    this must never be the thing that stops a dialog from opening.
    """
    try:
        from arctis_sound_manager.bug_reporter import _detect_container_env
        return _detect_container_env() != 'native'
    except Exception:  # noqa: BLE001
        return False


def host_exec() -> list[str] | None:
    """The argv prefix that runs a command on the host.

    ``[]``   — not in a container, run the command as-is.
    ``['distrobox-host-exec']`` — in a container, and the host is reachable.
    ``None`` — in a container with no way out. This is the case that has to be
    *said*, never silently treated as success: the caller cannot do the work,
    and the user has to be handed the command to run on the host themselves.
    """
    if not running_in_container():
        return []
    found = shutil.which('distrobox-host-exec')
    return [found] if found else None


def host_distro() -> str | None:
    """The host's os-release ID, or None if it cannot be read.

    The dependency dialog used to name the *container's* distribution — an
    Arch distrobox on Bazzite was announced as "arch", so every install line
    it offered was a pacman command aimed at an immutable Fedora host.

    This is the distribution's identity and nothing else. It deliberately does
    NOT answer VARIANT_ID: Silverblue and Kinoite report ``ID=fedora``, and
    substituting their variant would also rename Fedora Workstation to
    "workstation", which is not a distribution any caller can act on. Whether
    the host can accept a package install is a separate question with a
    factual answer — see host_is_immutable().
    """
    return _host_os_release().get('ID') or None


def host_is_immutable() -> bool:
    """True when the host's system tree cannot take a normal package install.

    Asked as a fact rather than inferred from a name. ``/run/ostree-booted``
    is the canonical marker every ostree host carries (Silverblue, Kinoite,
    Bazzite and their rebases), and a read-only /usr catches the image-based
    hosts that are not ostree — SteamOS above all, where pacman exists and
    appears to work, then loses everything at the next system update (#181,
    #88). A host we cannot reach is not assumed immutable: unknown must not
    silently disable an install path that works.
    """
    prefix = host_exec()
    if not prefix:
        return False
    probe = 'test -e /run/ostree-booted || test ! -w /usr'
    try:
        return subprocess.run(
            [*prefix, 'sh', '-c', probe],
            capture_output=True,
            timeout=_HOST_CALL_TIMEOUT, check=False).returncode == 0
    except (OSError, subprocess.SubprocessError) as e:
        _logger.warning(f"container: cannot probe host mutability: {e!r}")
        return False


def _host_os_release() -> dict[str, str]:
    """The host's /etc/os-release as a dict, empty when it cannot be read."""
    prefix = host_exec()
    if not prefix:
        return {}
    try:
        result = subprocess.run(
            [*prefix, 'cat', '/etc/os-release'],
            capture_output=True, text=True,
            timeout=_HOST_CALL_TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError) as e:
        _logger.warning(f"container: cannot read host os-release: {e!r}")
        return {}
    if result.returncode != 0:
        return {}

    fields: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, sep, value = line.partition('=')
        if sep:
            fields[key.strip()] = value.strip().strip('"\'').lower()
    return fields
