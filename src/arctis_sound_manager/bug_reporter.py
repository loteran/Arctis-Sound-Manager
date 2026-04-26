"""
Bug reporting utilities — system info, crash file I/O, GitHub URL.
No Qt imports: safe to use in daemon (headless).
"""
import json
import os
import platform
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import quote

CRASH_REPORT_FILE = Path.home() / '.config' / 'arctis_manager' / 'crash_report.json'
GITHUB_ISSUES_URL = 'https://github.com/loteran/Arctis-Sound-Manager/issues/new'


def _python_lib_versions() -> dict[str, str]:
    """Versions of the Python libs that most often cause runtime weirdness.
    Backend mismatches (e.g. system pulsectl vs pipx pulsectl) usually show
    up here before they show up anywhere else."""
    from importlib.metadata import PackageNotFoundError, version
    # Mapping: import-friendly label → distribution name on PyPI.
    libs = {
        'pulsectl':    'pulsectl',
        'pyudev':      'pyudev',
        'pyusb':       'pyusb',
        'dbus-next':   'dbus-next',
        'ruamel-yaml': 'ruamel.yaml',
        'pyside6':     'PySide6',
        'pillow':      'pillow',
    }
    out: dict[str, str] = {}
    for label, dist in libs.items():
        try:
            out[label] = version(dist)
        except PackageNotFoundError:
            out[label] = '(not installed)'
        except Exception as e:
            out[label] = f'(error: {e!r})'
    return out


def _detect_install_methods() -> list[str]:
    """Surface every install method present on this system at once.

    Single most common source of "I just upgraded but nothing changed":
    the user has rpm + pipx (or apt + pipx) in parallel, /usr/bin/asm-daemon
    masks the pipx one or vice-versa, and the version they SEE in journalctl
    is not the version they THINK they upgraded.
    """
    methods: list[str] = []
    cmds = (
        ('rpm',    ['rpm', '-q', '--qf', '%{VERSION}', 'arctis-sound-manager']),
        ('pacman', ['pacman', '-Q', 'arctis-sound-manager']),
        ('apt',    ['dpkg-query', '-W', '-f=${Version}', 'arctis-sound-manager']),
        ('pipx',   ['pipx', 'list', '--short']),
    )
    for name, cmd in cmds:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
            if r.returncode != 0:
                continue
            out = r.stdout.strip()
            if name == 'pipx':
                if 'arctis-sound-manager' not in out:
                    continue
                ver = next(
                    (line.split()[1] for line in out.splitlines()
                     if line.startswith('arctis-sound-manager')),
                    '?',
                )
            elif name == 'pacman':
                ver = out.split()[1] if out else '?'
            else:
                ver = out or '?'
            methods.append(f'{name}={ver}')
        except Exception:
            pass

    # Every asm-daemon binary in PATH (catches pip --user installs that
    # don't show up in any package manager).
    try:
        r = subprocess.run(
            ['bash', '-c', 'command -v -a asm-daemon'],
            capture_output=True, text=True, timeout=2,
        )
        bins = [b for b in r.stdout.strip().splitlines() if b]
        if len(bins) > 1:
            methods.append(f'asm-daemon binaries in PATH: {bins}')
    except Exception:
        pass
    return methods


def collect_system_info() -> dict:
    info: dict = {}

    try:
        from arctis_sound_manager.utils import project_version
        info['version'] = project_version()
    except Exception:
        info['version'] = 'unknown'

    info['python'] = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    info['kernel'] = platform.release()
    info['python_libs'] = _python_lib_versions()
    info['install_methods'] = _detect_install_methods()

    # Distro name
    try:
        r = subprocess.run(['lsb_release', '-d'], capture_output=True, text=True, timeout=2)
        info['distro'] = r.stdout.split(':', 1)[1].strip() if r.returncode == 0 else ''
    except Exception:
        info['distro'] = ''
    if not info['distro']:
        try:
            for line in Path('/etc/os-release').read_text().splitlines():
                if line.startswith('PRETTY_NAME='):
                    info['distro'] = line.split('=', 1)[1].strip().strip('"')
                    break
        except Exception:
            info['distro'] = platform.system()

    # PipeWire version
    try:
        r = subprocess.run(['pipewire', '--version'], capture_output=True, text=True, timeout=2)
        info['pipewire'] = r.stdout.strip().splitlines()[0] if r.returncode == 0 else 'not found'
    except Exception:
        info['pipewire'] = 'unknown'

    # Recent daemon logs (last 100 lines from journald)
    try:
        r = subprocess.run(
            ['journalctl', '--user', '-u', 'arctis-manager.service',
             '-n', '100', '--no-pager', '--output=short'],
            capture_output=True, text=True, timeout=5
        )
        info['logs'] = r.stdout.strip() if r.returncode == 0 else ''
    except Exception:
        info['logs'] = ''

    # USB HID device info (interfaces, endpoints)
    try:
        r = subprocess.run(
            ['asm-cli', 'tools', 'arctis-devices'],
            capture_output=True, text=True, timeout=5
        )
        info['usb_hid'] = r.stdout.strip() if r.returncode == 0 else r.stderr.strip()
    except Exception:
        info['usb_hid'] = ''

    # PipeWire audio cards
    try:
        r = subprocess.run(
            ['pactl', 'list', 'cards', 'short'],
            capture_output=True, text=True, timeout=5
        )
        info['pw_cards'] = r.stdout.strip() if r.returncode == 0 else ''
    except Exception:
        info['pw_cards'] = ''

    # Full sink list — useful when troubleshooting multi-device routing (issue #20).
    try:
        r = subprocess.run(['pactl', 'list', 'sinks', 'short'],
                           capture_output=True, text=True, timeout=5)
        info['pw_sinks'] = r.stdout.strip() if r.returncode == 0 else ''
    except Exception:
        info['pw_sinks'] = ''

    # WirePlumber state — catches priority/routing decisions made above the PA layer.
    try:
        r = subprocess.run(['wpctl', 'status'], capture_output=True, text=True, timeout=5)
        info['wpctl'] = r.stdout.strip() if r.returncode == 0 else ''
    except Exception:
        info['wpctl'] = ''

    # udev rules: which paths exist + the actual content of the active file.
    # The ASM checker's own verdict on whether the rules are valid is also useful.
    try:
        from arctis_sound_manager.constants import UDEV_RULES_PATHS
        from arctis_sound_manager.udev_checker import is_udev_rules_valid
        rules_present = [p for p in UDEV_RULES_PATHS if Path(p).exists()]
        info['udev_paths'] = rules_present
        info['udev_valid'] = bool(is_udev_rules_valid())
        if rules_present:
            try:
                info['udev_content'] = Path(rules_present[0]).read_text()
            except Exception as e:
                info['udev_content'] = f'(could not read {rules_present[0]}: {e!r})'
        else:
            info['udev_content'] = ''
    except Exception as e:
        info['udev_paths'] = []
        info['udev_valid'] = None
        info['udev_content'] = f'(udev probe failed: {e!r})'

    # USB monitor backend (pyudev event-driven vs polling fallback) — straight
    # from the module so we don't have to instantiate a second monitor.
    try:
        from arctis_sound_manager.usb_devices_monitor import _PYUDEV_AVAILABLE
        info['usb_monitor_backend'] = 'pyudev' if _PYUDEV_AVAILABLE else 'polling'
    except Exception:
        info['usb_monitor_backend'] = 'unknown'

    # D-Bus session info — ASM is dead in the water without a session bus.
    info['dbus_session'] = (
        os.environ.get('DBUS_SESSION_BUS_ADDRESS')
        or (f'/run/user/{os.getuid()}/bus'
            if Path(f'/run/user/{os.getuid()}/bus').exists()
            else '<not set>')
    )
    info['session_type'] = os.environ.get('XDG_SESSION_TYPE', '<unset>')
    info['desktop'] = os.environ.get('XDG_CURRENT_DESKTOP', '<unset>')

    return info


def format_bug_report(traceback_str: Optional[str] = None) -> str:
    info = collect_system_info()

    lines = [
        '## Environment',
        f'- **ASM version**: {info.get("version", "unknown")}',
        f'- **Python**: {info.get("python", "unknown")}',
        f'- **OS**: {info.get("distro", "unknown")} (kernel {info.get("kernel", "?")})',
        f'- **PipeWire**: {info.get("pipewire", "unknown")}',
        f'- **Desktop / Session**: {info.get("desktop", "?")} / {info.get("session_type", "?")}',
        f'- **D-Bus session**: `{info.get("dbus_session", "?")}`',
        f'- **USB monitor backend**: {info.get("usb_monitor_backend", "?")}',
        '',
    ]

    methods = info.get('install_methods', [])
    if methods:
        lines += [
            '## ASM installation(s) detected',
            '<!-- More than one entry below = duplicate install. Run scripts/uninstall.sh to clean up. -->',
            '```',
            *(f'- {m}' for m in methods),
            '```',
            '',
        ]

    libs = info.get('python_libs', {})
    if libs:
        lines += [
            '## Python library versions',
            '```',
            *(f'{k}: {v}' for k, v in libs.items()),
            '```',
            '',
        ]

    if traceback_str:
        lines += [
            '## Crash traceback',
            '```',
            traceback_str.strip(),
            '```',
            '',
        ]

    usb_hid = info.get('usb_hid', '')
    if usb_hid:
        lines += [
            '## USB HID devices',
            '```',
            usb_hid,
            '```',
            '',
        ]

    pw_cards = info.get('pw_cards', '')
    if pw_cards:
        lines += [
            '## PipeWire audio cards',
            '```',
            pw_cards,
            '```',
            '',
        ]

    pw_sinks = info.get('pw_sinks', '')
    if pw_sinks:
        lines += [
            '## PipeWire sinks',
            '```',
            pw_sinks,
            '```',
            '',
        ]

    wpctl = info.get('wpctl', '')
    if wpctl:
        lines += [
            '## WirePlumber (`wpctl status`)',
            '```',
            wpctl[-3000:],
            '```',
            '',
        ]

    udev_paths = info.get('udev_paths', [])
    if udev_paths or info.get('udev_content'):
        valid = info.get('udev_valid')
        valid_str = '✅ valid' if valid else ('❌ invalid/missing' if valid is False else '?')
        lines += [
            '## udev rules',
            f'- `is_udev_rules_valid()`: {valid_str}',
            f'- Paths present: `{udev_paths}`',
            '```',
            info.get('udev_content', '')[:6000] or '(no rules file present on disk)',
            '```',
            '',
        ]

    logs = info.get('logs', '')
    if logs:
        lines += [
            '## Recent daemon logs',
            '```',
            logs[-4000:],
            '```',
            '',
        ]

    lines += [
        '## Steps to reproduce',
        '<!-- Describe what you were doing when the bug occurred -->',
        '',
        '## Expected behavior',
        '',
        '## Actual behavior',
    ]

    return '\n'.join(lines)


def github_issue_url(title: str) -> str:
    return f'{GITHUB_ISSUES_URL}?labels=bug&title={quote(title)}'


def write_crash_report(exc_type, exc_value, exc_tb, source: str = 'gui') -> None:
    try:
        tb_str = ''.join(traceback.format_exception(exc_type, exc_value, exc_tb))
        report = {
            'timestamp': datetime.now().isoformat(),
            'source': source,
            'traceback': tb_str,
        }
        CRASH_REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
        CRASH_REPORT_FILE.write_text(json.dumps(report, indent=2))
    except Exception:
        pass


def read_crash_report() -> Optional[dict]:
    try:
        if CRASH_REPORT_FILE.exists():
            return json.loads(CRASH_REPORT_FILE.read_text())
    except Exception:
        pass
    return None


def clear_crash_report() -> None:
    try:
        CRASH_REPORT_FILE.unlink(missing_ok=True)
    except Exception:
        pass
