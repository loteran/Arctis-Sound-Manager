# Copyright (C) 2022 Giacomo Furlan (elegos) — original work
# Copyright (C) 2026 loteran — modifications
# SPDX-License-Identifier: GPL-3.0-or-later

import shutil
import sys
from pathlib import Path

from arctis_sound_manager import service_control as sc
from arctis_sound_manager.constants import (HOME_SYSTEMD_SERVICE_FOLDER,
                                            SYSTEMD_SERVICE_NAME)


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
    path.parent.mkdir(parents=True, exist_ok=True)
    write_systemd_service(path)
    if enable:
        # service_control swallows failures and returns False (the service may
        # already be running or be managed by a system package).
        sc.enable("arctis-manager", now=True)

def write_systemd_service(path: Path) -> None:
    daemon_path = shutil.which('asm-daemon') or Path(sys.argv[0]).resolve().parent / 'asm-daemon'

    template = f'''[Unit]
Description=Arctis Sound Manager
After=pipewire.service pipewire-pulse.service
Wants=pipewire.service
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
