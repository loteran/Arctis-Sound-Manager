# Copyright (C) 2022 Giacomo Furlan (elegos) — original work
# Copyright (C) 2026 loteran — modifications
# SPDX-License-Identifier: GPL-3.0-or-later

import logging
import shutil
import sys
from pathlib import Path

from arctis_sound_manager import service_control as sc
from arctis_sound_manager.constants import (HOME_SYSTEMD_SERVICE_FOLDER,
                                            SYSTEMD_SERVICE_NAME)

logger = logging.getLogger('SystemdUnit')


def is_systemd_unit_enabled() -> bool:
    # "arctis-manager" is the logical name; service_control resolves it to the
    # real systemd unit and runs `systemctl --user is-enabled`.
    return sc.is_enabled("arctis-manager")

def _running_in_container() -> bool:
    try:
        from arctis_sound_manager.bug_reporter import _detect_container_env
        return _detect_container_env() != 'native'
    except Exception:
        return False


_PACKAGED_UNIT_DIRS = (
    Path('/usr/lib/systemd/user'),
    Path('/usr/local/lib/systemd/user'),
    Path('/etc/systemd/user'),
)


def _packaged_unit_path() -> Path | None:
    """The distribution's own copy of the unit, if it ships one."""
    for directory in _PACKAGED_UNIT_DIRS:
        candidate = directory / SYSTEMD_SERVICE_NAME
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


def _clear_shadowing_copy(path: Path, packaged: Path) -> None:
    """Remove a user copy we wrote ourselves, so *packaged* applies again."""
    try:
        if not path.is_file():
            return
        current = path.read_text()
    except OSError as exc:
        logger.warning('could not read %s: %r', path, exc)
        return

    if current == packaged.read_text():
        # Identical: harmless, but still a copy that would freeze on the next
        # packaged revision. Same reasoning as the device-profile reconcile.
        pass
    elif not _looks_like_our_template(current):
        logger.warning(
            '%s differs from the packaged unit and does not look like one ASM '
            'wrote — leaving it alone. It REPLACES %s, so if the daemon starts '
            'before the filter-chain, that is why.', path, packaged,
        )
        return

    try:
        path.unlink()
    except OSError as exc:
        logger.warning('could not remove the shadowing unit %s: %r', path, exc)
        return
    logger.info(
        'Removed %s so the packaged unit at %s applies again (it was shadowing '
        'it, and had drifted from it).', path, packaged,
    )
    sc.daemon_reload()


def _looks_like_our_template(content: str) -> bool:
    """True if *content* is a unit this module generated, current or older.

    Keyed on the ExecStart shape and the Description, which every version of
    the template has carried, rather than on an exact match against the
    current one — the whole point is to recognise the older copies too.
    """
    return ('Description=Arctis Sound Manager' in content
            and 'ExecStart=' in content
            and 'asm-daemon' in content
            and 'distrobox' not in content)


def ensure_systemd_unit(enable: bool = False) -> None:
    from arctis_sound_manager.init_system import detect_init
    if detect_init() != "systemd" and not shutil.which("systemctl"):
        return

    # Never write this unit from inside a container (issue #203). $HOME is
    # shared with the host, and ~/.config/systemd/user takes precedence over
    # the packaged unit — so the file written here is the one the HOST's
    # systemd runs. which('asm-daemon') resolves to /usr/bin/asm-daemon, which
    # exists only in the container: every restart, including every boot, fails
    # with 203/EXEC until StartLimitBurst locks the unit out. It fails quietly
    # because a freshly installed ASM keeps running — the process predates the
    # file — so nothing looks wrong until the next reboot.
    #
    # The Distrobox installers already write the correct host units
    # (`distrobox enter <container> -- /usr/bin/asm-daemon`, see
    # scripts/distrobox/_common.sh). Overwriting those is strictly destructive.
    if _running_in_container():
        return

    path = HOME_SYSTEMD_SERVICE_FOLDER / SYSTEMD_SERVICE_NAME

    # A packaged unit outranks anything this function can write, and a file of
    # the same name under ~/.config/systemd/user REPLACES it rather than
    # extending it (issue #206). The template below had drifted from the
    # packaged one — it lost `filter-chain.service` from After=/Wants= — so
    # every session start quietly reinstated a copy that let the daemon start
    # before the filter-chain existed, which is the node the mic capture has to
    # link into. Same shape as PKG-3, one file over: a local copy that wins and
    # is never refreshed.
    #
    # So: when the distribution ships the unit, write nothing, and clear away a
    # copy we recognise as our own so the packaged one takes effect again. A
    # file we do not recognise is someone's deliberate edit and is left alone,
    # with a warning, because silently deleting it would be worse than the
    # drift.
    packaged = _packaged_unit_path()
    if packaged is not None:
        _clear_shadowing_copy(path, packaged)
        if enable:
            sc.enable("arctis-manager", now=True)
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    write_systemd_service(path)
    if enable:
        # service_control swallows failures and returns False (the service may
        # already be running or be managed by a system package).
        sc.enable("arctis-manager", now=True)

def write_systemd_service(path: Path) -> None:
    daemon_path = shutil.which('asm-daemon') or Path(sys.argv[0]).resolve().parent / 'asm-daemon'

    # Keep After=/Wants= in step with systemd/arctis-manager.service, the unit
    # the packages install: this copy REPLACES it where both exist. They had
    # drifted, and the missing filter-chain.service let the daemon start before
    # the node its mic capture links into (#206). A test pins the two together.
    template = f'''[Unit]
Description=Arctis Sound Manager
After=pipewire.service pipewire-pulse.service filter-chain.service
Wants=pipewire.service filter-chain.service
StartLimitInterval=1min
StartLimitBurst=5

[Service]
Type=simple
ExecStart={daemon_path}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=graphical-session.target'''
    
    if path.exists() and path.read_text() == f'{template}\n':
        return

    with open(path, 'w') as f:
        f.writelines([f'{line}\n' for line in template.split('\n')])
