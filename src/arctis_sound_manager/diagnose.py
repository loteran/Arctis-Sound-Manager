"""Generate a diagnostic dump for bug reports.

Captures everything a maintainer typically needs to triage an Arctis Sound
Manager issue: project version, OS / desktop / session info, USB device tree
filtered to vendor 0x1038, udev rules state, PulseAudio/PipeWire sinks, the
last journalctl entries for the daemon, and the user's settings (with any
secrets stripped).

Output is written to stdout in plain text so users can paste it into an
issue, or saved to a file. No data is sent anywhere — this is local-only.
"""
from __future__ import annotations

import io
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from arctis_sound_manager.constants import (HOME_CONFIG_FOLDER,
                                            SETTINGS_FOLDER,
                                            UDEV_RULES_PATHS)
from arctis_sound_manager.utils import project_version

# Settings keys that may carry semi-private data (city names for the weather
# widget, custom paths, etc.) — strip them before dumping.
_REDACT_KEY_PATTERNS = (
    re.compile(r'(?i)location'),
    re.compile(r'(?i)city'),
    re.compile(r'(?i)token'),
    re.compile(r'(?i)password'),
    re.compile(r'(?i)email'),
)


def _section(title: str, body: str) -> str:
    bar = '=' * 72
    return f'\n{bar}\n== {title}\n{bar}\n{body.rstrip()}\n'


def _run(cmd: list[str], timeout: float = 5.0) -> str:
    if not cmd or not shutil.which(cmd[0]):
        return f'(skipped: {cmd[0]} not in PATH)'
    try:
        out = subprocess.run(
            cmd, check=False, text=True,
            capture_output=True, timeout=timeout,
        )
        return out.stdout + (f'\n(stderr) {out.stderr}' if out.stderr else '')
    except subprocess.TimeoutExpired:
        return f'(timed out after {timeout}s: {" ".join(cmd)})'
    except Exception as e:
        return f'(error: {e!r})'


def _redact_settings(payload: dict) -> dict:
    redacted = {}
    for k, v in payload.items():
        if any(p.search(k) for p in _REDACT_KEY_PATTERNS):
            redacted[k] = '<redacted>'
        elif isinstance(v, dict):
            redacted[k] = _redact_settings(v)
        else:
            redacted[k] = v
    return redacted


def _section_versions() -> str:
    info = {
        'asm_version': project_version(),
        'python':      platform.python_version(),
        'platform':    platform.platform(),
        'distro':      _run(['cat', '/etc/os-release']) or '(no /etc/os-release)',
        'kernel':      platform.release(),
        'hostname':    socket.gethostname(),
        'session':     {
            'XDG_CURRENT_DESKTOP': os.environ.get('XDG_CURRENT_DESKTOP', '<unset>'),
            'XDG_SESSION_TYPE':    os.environ.get('XDG_SESSION_TYPE', '<unset>'),
            'WAYLAND_DISPLAY':     os.environ.get('WAYLAND_DISPLAY', '<unset>'),
            'DISPLAY':             os.environ.get('DISPLAY', '<unset>'),
            'DBUS_SESSION':        os.environ.get('DBUS_SESSION_BUS_ADDRESS', '<unset>'),
        },
    }
    return json.dumps(info, indent=2, default=str)


def _section_lsusb() -> str:
    raw = _run(['lsusb', '-d', '1038:'])
    if 'skipped' in raw or not raw.strip():
        # Fallback: enumerate via pyusb so the section is useful even on
        # systems where lsusb isn't installed (NixOS minimal etc.).
        try:
            import usb.core
            entries = []
            for dev in usb.core.find(find_all=True, idVendor=0x1038):
                entries.append(
                    f'  vid=0x{dev.idVendor:04x} pid=0x{dev.idProduct:04x} '
                    f'bus={dev.bus} address={dev.address}'
                )
            raw = '\n'.join(entries) if entries else '(no SteelSeries vendor 0x1038 device on the bus)'
        except Exception as e:
            raw += f'\npyusb fallback failed: {e!r}'
    return raw


def _section_udev() -> str:
    out = io.StringIO()
    out.write('Searched paths:\n')
    for p in UDEV_RULES_PATHS:
        path = Path(p)
        if path.exists():
            try:
                size = path.stat().st_size
                out.write(f'  [present] {path} ({size} bytes)\n')
            except OSError as e:
                out.write(f'  [error]   {path} ({e!r})\n')
        else:
            out.write(f'  [missing] {path}\n')

    try:
        from arctis_sound_manager.udev_checker import is_udev_rules_valid
        out.write(f'\nis_udev_rules_valid(): {is_udev_rules_valid()}\n')
    except Exception as e:
        out.write(f'\nis_udev_rules_valid() raised: {e!r}\n')

    # Show the first existing rules file (helpful when triaging "rules
    # claim X but actually look like Y" reports).
    for p in UDEV_RULES_PATHS:
        path = Path(p)
        if path.exists():
            try:
                out.write(f'\nFirst rules file ({path}):\n')
                out.write(path.read_text())
            except OSError as e:
                out.write(f'\n(could not read {path}: {e!r})')
            break
    return out.getvalue()


def _section_pulseaudio() -> str:
    out = io.StringIO()
    try:
        import pulsectl
        client = pulsectl.Pulse('arctis-diagnose')
        sinks = client.sink_list()
        out.write(f'PulseAudio sinks ({len(sinks)}):\n')
        for s in sinks:
            out.write(f'  - {s.name}  ({s.description})\n')
        sources = client.source_list()
        out.write(f'\nSources ({len(sources)}):\n')
        for s in sources[:30]:
            out.write(f'  - {s.name}  ({s.description})\n')
        client.disconnect()
    except Exception as e:
        out.write(f'(could not connect: {e!r})')
    return out.getvalue()


def _section_journalctl() -> str:
    raw = _run(
        ['journalctl', '--user', '-u', 'arctis-manager.service', '-n', '100', '--no-pager'],
        timeout=8.0,
    )
    return raw


def _section_settings() -> str:
    out = io.StringIO()
    settings_yaml = SETTINGS_FOLDER.parent / 'general_settings.yaml'
    if not settings_yaml.exists():
        out.write(f'(no settings file at {settings_yaml})')
        return out.getvalue()
    try:
        from ruamel.yaml import YAML
        data = YAML(typ='safe').load(settings_yaml) or {}
        if isinstance(data, dict):
            data = _redact_settings(data)
        out.write(json.dumps(data, indent=2, default=str))
    except Exception as e:
        out.write(f'(failed to parse {settings_yaml}: {e!r})')
    return out.getvalue()


def _section_yamls() -> str:
    out = io.StringIO()
    out.write(f'HOME devices folder: {HOME_CONFIG_FOLDER}\n')
    if HOME_CONFIG_FOLDER.is_dir():
        for f in sorted(HOME_CONFIG_FOLDER.glob('*.yaml')):
            out.write(f'  - {f.name} ({f.stat().st_size} bytes)\n')
    else:
        out.write('  (folder absent)\n')
    return out.getvalue()


def diagnose(stream=sys.stdout) -> int:
    stream.write(f'# Arctis Sound Manager — diagnostic dump\n')
    stream.write(f'# Generated: {datetime.now(timezone.utc).isoformat()}\n')

    sections = [
        ('Versions / session', _section_versions),
        ('SteelSeries USB devices (vendor 0x1038)', _section_lsusb),
        ('udev rules', _section_udev),
        ('PulseAudio / PipeWire', _section_pulseaudio),
        ('User device YAML overrides', _section_yamls),
        ('Settings (redacted)', _section_settings),
        ('Journalctl — arctis-manager.service (last 100 lines)', _section_journalctl),
    ]
    for title, fn in sections:
        try:
            stream.write(_section(title, fn()))
        except Exception as e:
            stream.write(_section(title, f'(section failed: {e!r})'))
    return 0
