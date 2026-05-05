import shutil
import subprocess
import sys
from pathlib import Path

from arctis_sound_manager.constants import (HOME_SYSTEMD_SERVICE_FOLDER,
                                            SYSTEMD_SERVICE_NAME)


def is_systemd_unit_enabled() -> bool:
    try:
        subprocess.check_call(['systemctl', '--user', 'is-enabled', SYSTEMD_SERVICE_NAME], stdout=subprocess.DEVNULL)
        return True
    except subprocess.CalledProcessError:
        pass

    return False

def ensure_systemd_unit(enable: bool = False) -> None:
    from arctis_sound_manager.init_system import detect_init
    if detect_init() != "systemd" and not shutil.which("systemctl"):
        return
    path = HOME_SYSTEMD_SERVICE_FOLDER / SYSTEMD_SERVICE_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    write_systemd_service(path)
    if enable:
        try:
            subprocess.run(
                ['systemctl', '--user', 'enable', '--now', SYSTEMD_SERVICE_NAME],
                check=True, capture_output=True,
            )
        except subprocess.CalledProcessError:
            pass  # service may already be running or managed by system package

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
