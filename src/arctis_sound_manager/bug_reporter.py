"""
Bug reporting utilities — system info, crash file I/O, GitHub URL.
No Qt imports: safe to use in daemon (headless).
"""
import json
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


def collect_system_info() -> dict:
    info: dict = {}

    try:
        from arctis_sound_manager.utils import project_version
        info['version'] = project_version()
    except Exception:
        info['version'] = 'unknown'

    info['python'] = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    info['kernel'] = platform.release()

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

    return info


def format_bug_report(traceback_str: Optional[str] = None) -> str:
    info = collect_system_info()

    lines = [
        '## Environment',
        f'- **ASM version**: {info.get("version", "unknown")}',
        f'- **Python**: {info.get("python", "unknown")}',
        f'- **OS**: {info.get("distro", "unknown")} (kernel {info.get("kernel", "?")})',
        f'- **PipeWire**: {info.get("pipewire", "unknown")}',
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
