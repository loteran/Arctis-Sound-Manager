import os
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from argparse import ArgumentParser
from pathlib import Path
from typing import NamedTuple

from ruamel.yaml import YAML

from arctis_sound_manager.cli_tools import arctis_usb_info
from arctis_sound_manager.config import DeviceConfiguration
from arctis_sound_manager.constants import (DEVICES_CONFIG_FOLDER,
                                            UDEV_RULES_PATHS)
from arctis_sound_manager.utils import project_version

ConfigRuleset = NamedTuple(
    'ConfigRuleset',
    [
        ('vendor_id', int),
        ('product_ids', list[int]),
        ('device_name', str)
    ])

ICONS_PATH = Path().home() / '.local' / 'share' / 'icons'
ICON_PATH = ICONS_PATH / 'arctis-manager.svg'

APPLICATIONS_PATH = Path().home() / '.local' / 'share' / 'applications'
DESKTOP_WINDOW_PATH = APPLICATIONS_PATH / 'ArctisManager.desktop'
DESKTOP_SYSTRAY_PATH = APPLICATIONS_PATH / 'ArctisManagerSystray.desktop'

SYSTEMD_USER_DIR = Path().home() / '.config' / 'systemd' / 'user'
SERVICE_PATH = SYSTEMD_USER_DIR / 'arctis-manager.service'
GUI_SERVICE_PATH = SYSTEMD_USER_DIR / 'arctis-gui.service'

_SERVICE_TEMPLATE = """\
[Unit]
Description=Arctis Sound Manager
After=pipewire.service pipewire-pulse.service
Wants=pipewire.service
StartLimitInterval=1min
StartLimitBurst=5

[Service]
Type=simple
ExecStart={asm_daemon}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=graphical-session.target
"""

_GUI_SERVICE_TEMPLATE = """\
[Unit]
Description=Arctis Sound Manager — System Tray
After=graphical-session.target arctis-manager.service
Wants=arctis-manager.service

[Service]
Type=simple
ExecStart={asm_gui} --systray
Restart=on-failure
RestartSec=5

[Install]
WantedBy=graphical-session.target
"""

def _has_tty() -> bool:
    """Returns True if stdin is a real terminal (CLI context)."""
    try:
        return os.isatty(sys.stdin.fileno())
    except Exception:
        return False


def _is_graphical() -> bool:
    """Returns True if a graphical display server is available."""
    return bool(os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY'))


def _graphical_elevators() -> list[str]:
    """
    Returns an ordered list of graphical elevation tools for the current
    desktop environment. pkexec (polkit) is the universal fallback.
    """
    desktop = os.environ.get('XDG_CURRENT_DESKTOP', '').lower()
    tools: list[str] = []
    if 'kde' in desktop:
        tools += ['kdesu', 'kdesudo']
    elif 'lxqt' in desktop:
        tools += ['lxqt-sudo']
    tools.append('pkexec')
    return tools

def sudo_it(command: list[str]) -> int:
    """
    Run *command* with elevated privileges, picking the right tool based on
    the execution context:

    - Terminal (TTY present)  → sudo first, graphical tools as fallback
    - GUI (display, no TTY)   → graphical tools only (pkexec / DE-specific)
    - Headless (no TTY/display) → sudo only (expects NOPASSWD or service context)
    """
    has_tty = _has_tty()
    graphical = _is_graphical()

    if has_tty:
        elevators = ['sudo'] + _graphical_elevators()
    elif graphical:
        elevators = _graphical_elevators()
    else:
        elevators = ['sudo']

    for elevator in elevators:
        binary = shutil.which(elevator)
        if not binary:
            continue
        try:
            result = subprocess.run([binary, *command], check=True)
            return result.returncode
        except subprocess.CalledProcessError as e:
            print(f'{elevator} failed with code {e.returncode}.')
        except FileNotFoundError:
            pass

    print('No working privilege escalation tool found.')
    if not has_tty and not graphical:
        print('Hint: configure sudoers NOPASSWD or run manually: sudo asm-cli udev write-rules --force --reload')
    return 250

def _make_elevated_script(*commands: list[str]) -> str:
    """Write a temp shell script containing *commands* (each a list), make it executable, return its path."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False, prefix='asm-udev-') as sh:
        sh.write('#!/bin/sh\nset -e\n')
        for cmd in commands:
            sh.write(' '.join(shlex.quote(c) for c in cmd) + '\n')
        sh_path = sh.name
    os.chmod(sh_path, stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
    return sh_path


def write_udev_rules(rules_path: Path, create_directories: bool, force_write: bool, and_reload: bool = False) -> int:
    run_with_sudo = False

    print('Writing udev rules...')

    if rules_path.is_dir():
        print(f'Cannot write to directory {rules_path}')
        print('Please specify a file.')

        return 1

    if create_directories:
        rules_path.parent.mkdir(parents=True, exist_ok=True)

    if not rules_path.parent.exists():
        print(f'Cannot write to {rules_path}')
        print('Parent directory does not exist.')

        return 2

    if rules_path.exists() and not os.access(rules_path, os.W_OK) \
        or not rules_path.exists() and not os.access(rules_path.parent, os.W_OK):
        print(f"User can't write to {rules_path}. Elevating privileges (pkexec or sudo)...")
        run_with_sudo = True

    if not force_write and rules_path.exists():
        print(f'File {rules_path} already exists.')
        print('To overwrite add option --force.')

        return 3

    yaml = YAML(typ='safe')
    products: dict[tuple[int, str], ConfigRuleset] = {}
    for config_path in DEVICES_CONFIG_FOLDER:
        for config_file in config_path.glob('*.yaml'):
            config_yaml = yaml.load(config_file)

            config = DeviceConfiguration(config_yaml)
            key = (config.vendor_id, config.name)
            products[key] = ConfigRuleset(config.vendor_id, config.product_ids, config.name)

    rule_template = 'SUBSYSTEM=="usb", ATTRS{{idVendor}}=="{idVendor}", ATTRS{{idProduct}}=="{idProduct}", MODE="0666", TAG+="uaccess"'
    rules = []
    for ruleset in products.values():
        rules.append('')
        rules.append(f'# {ruleset.device_name}')
        for pid in ruleset.product_ids:
            rules.append(rule_template.format(
                idVendor=f'{ruleset.vendor_id:04x}',
                idProduct=f'{pid:04x}',
            ))

    concat_rules = '\n'.join(rules)
    file_content = f'''# Generated by Arctis Sound Manager via asm-cli udev write-rules

ACTION=="remove", GOTO="local_end"
{concat_rules}

LABEL="local_end"'''
    if run_with_sudo:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.rules', delete=False) as tmp:
            tmp.write(f'{file_content}\n')
            tmp_path = tmp.name
        try:
            if and_reload:
                # Bundle write + reload + trigger in a single elevated call (one password prompt)
                print('Bundling write + reload in a single elevated call...')
                sh_path = _make_elevated_script(
                    ["install", "-m", "644", tmp_path, str(rules_path)],
                    ["udevadm", "control", "--reload-rules"],
                    ["udevadm", "trigger", "--subsystem-match=usb"],
                )
                try:
                    return sudo_it([sh_path])
                finally:
                    os.unlink(sh_path)
            else:
                return sudo_it(["install", "-m", "644", tmp_path, str(rules_path)])
        finally:
            os.unlink(tmp_path)
    else:
        with rules_path.open('w') as f:
            f.write(f'{file_content}\n')
        if and_reload:
            return reload_udev_rules()

    return 0


def reload_udev_rules() -> int:
    print('Reloading udev rules...')
    if os.geteuid() == 0:
        # Already root — run both commands directly
        for cmd in [
            ["udevadm", "control", "--reload-rules"],
            ["udevadm", "trigger", "--subsystem-match=usb"],
        ]:
            try:
                result = subprocess.run(cmd, check=True).returncode
                if result:
                    return result
            except subprocess.CalledProcessError as e:
                print(f'- Command failed with code {e.returncode}!')
                return e.returncode
        return 0

    # Not root — bundle both udevadm calls into a single elevated invocation
    print('Bundling reload + trigger in a single elevated call...')
    sh_path = _make_elevated_script(
        ["udevadm", "control", "--reload-rules"],
        ["udevadm", "trigger", "--subsystem-match=usb"],
    )
    try:
        return sudo_it([sh_path])
    finally:
        os.unlink(sh_path)

def write_desktop_entries() -> int:
    print('Writing desktop entries...')

    # 1. write the icon file
    ICONS_PATH.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path(__file__).parent.parent / 'gui' / 'images' / 'steelseries_logo.svg', ICON_PATH)

    # 2. write the desktop entry
    APPLICATIONS_PATH.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path(__file__).parent.parent / 'desktop' / 'ArctisManager.desktop', DESKTOP_WINDOW_PATH)

    asm_gui = shutil.which('asm-gui')
    if asm_gui:
        DESKTOP_WINDOW_PATH.write_text(DESKTOP_WINDOW_PATH.read_text().replace('exec asm-gui', asm_gui))

    DESKTOP_WINDOW_PATH.chmod(0o755)

    # Remove legacy systray-only shortcut if present
    if DESKTOP_SYSTRAY_PATH.exists():
        DESKTOP_SYSTRAY_PATH.unlink()

    # 3. write the systemd user service files (pipx install — binaries are in ~/.local/bin)
    SYSTEMD_USER_DIR.mkdir(parents=True, exist_ok=True)
    asm_daemon = shutil.which('asm-daemon')
    if asm_daemon:
        SERVICE_PATH.write_text(_SERVICE_TEMPLATE.format(asm_daemon=asm_daemon))
        print(f'    [ok] Service file written: {SERVICE_PATH}')
    else:
        print('    [!] asm-daemon not found in PATH — skipping daemon service file.')

    asm_gui = shutil.which('asm-gui')
    if asm_gui:
        GUI_SERVICE_PATH.write_text(_GUI_SERVICE_TEMPLATE.format(asm_gui=asm_gui))
        print(f'    [ok] Service file written: {GUI_SERVICE_PATH}')
    else:
        print('    [!] asm-gui not found in PATH — skipping GUI service file.')

    return 0

def remove_desktop_entries() -> int:
    print('Removing desktop entries...')
    if ICON_PATH.exists():
        ICON_PATH.unlink()

    if DESKTOP_WINDOW_PATH.exists():
        DESKTOP_WINDOW_PATH.unlink()

    if DESKTOP_SYSTRAY_PATH.exists():
        DESKTOP_SYSTRAY_PATH.unlink()

    if GUI_SERVICE_PATH.exists():
        GUI_SERVICE_PATH.unlink()

    return 0


def main():
    parser = ArgumentParser(description=f'Arctis Sound Manager CLI v {project_version()}')
    subparsers = parser.add_subparsers(dest='command', required=True)

    udev_parser = subparsers.add_parser('udev', help='UDEV rules')
    udev_subparsers = udev_parser.add_subparsers(dest='action', required=True)

    write_parser = udev_subparsers.add_parser('write-rules', help='Write the udev rules')
    write_parser.add_argument('--rules-path', default=None, type=Path)
    write_parser.add_argument('--create-directories', action='store_true')
    write_parser.add_argument('--force', action='store_true')
    write_parser.add_argument('--reload', action='store_true')

    reload_parser = udev_subparsers.add_parser('reload-rules', help='Reload the udev rules')

    desktop_parser = subparsers.add_parser('desktop', help='Desktop entries management')

    destkop_subparsers = desktop_parser.add_subparsers(dest='action', required=True)
    destkop_subparsers.add_parser('write', help='Write the desktop entries')
    destkop_subparsers.add_parser('remove', help='Remove the desktop entries')

    # Tools
    tools_parser = subparsers.add_parser('tools', help='Reverse engineering tools')

    usb_devices_subparser = tools_parser.add_subparsers(dest='action', required=True)
    arctis_devices_parser = usb_devices_subparser.add_parser('arctis-devices', help='List important Arctis device(s) information, like HID interfaces, alternate configs, etc.')
    arctis_devices_parser.add_argument('--vendor-id', default=0x1038, type=int)

    args = parser.parse_args()

    if not hasattr(args, 'action'):
        parser.print_help()
        return

    if args.command == 'udev':
        if args.action == 'write-rules':
            rules_path = args.rules_path if args.rules_path else next((Path(p) for p in UDEV_RULES_PATHS if Path(p).parent.is_dir()), None)
            if not rules_path:
                print('No valid rules path found. Please specify one with --rules-path.')
                sys.exit(1)

            result = write_udev_rules(rules_path, args.create_directories, args.force, and_reload=args.reload)
            sys.exit(result)
        elif args.action == 'reload-rules':
            sys.exit(reload_udev_rules())
    elif args.command == 'desktop':
        if args.action == 'write':
            return write_desktop_entries()
        elif args.action == 'remove':
            return remove_desktop_entries()
    elif args.command == 'tools':
        if args.action == 'arctis-devices':
            return arctis_usb_info(args.vendor_id)

if __name__ == '__main__':
    main()
