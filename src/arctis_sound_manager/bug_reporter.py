# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Bug reporting utilities — system info, crash file I/O, GitHub URL.
No Qt imports: safe to use in daemon (headless).
"""
import json
import os
import platform
import re
import shutil
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


def _run_out(cmd: list[str], timeout: float = 5.0) -> str:
    """Run *cmd* and return its stdout, stripped.

    Returns '' when the binary is missing, the command times out, or it
    raises. stdout is returned even on a non-zero exit code because tools
    like `systemctl is-active` exit non-zero while still printing the state
    ('inactive', 'failed') we want to report.
    """
    if not cmd or not shutil.which(cmd[0]):
        return ''
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except Exception:
        return ''


_ARCTIS_PATTERNS = ('arctis', '1038', 'steelseries')


def _detect_container_env() -> str:
    """Distrobox / Flatpak / Snap / docker / native detection.

    Inside a Distrobox the daemon only reaches PipeWire through forwarded
    sockets — knowing we are in a container is the first question a
    maintainer asks when virtual outputs are missing (issue #74).
    """
    if os.environ.get('FLATPAK_ID'):
        return f"flatpak (FLATPAK_ID={os.environ['FLATPAK_ID']})"
    if os.environ.get('SNAP'):
        return f"snap (SNAP={os.environ['SNAP']})"
    container = os.environ.get('container', '')
    if (container == 'distrobox'
            or os.environ.get('DISTROBOX_ENTER_PATH')
            or os.environ.get('CONTAINER_ID')):
        name = os.environ.get('CONTAINER_ID', '?')
        return f'distrobox (container={container or "?"}, CONTAINER_ID={name})'
    if container:
        return f'container ({container})'
    if Path('/.dockerenv').exists():
        return 'docker'
    return 'native'


def _arctis_pw_nodes() -> str:
    """PipeWire objects matching the Arctis (node name, 'steelseries', or
    vendor id 1038). Empty result while USB sees the device means PipeWire
    never created the ALSA nodes — the issue #74 Distrobox failure mode.

    Prefers `pw-dump`; falls back to `pactl list sinks` when pipewire-utils
    is not installed.
    """
    if shutil.which('pw-dump'):
        raw = _run_out(['pw-dump'], timeout=5.0)
        try:
            objects = json.loads(raw)
        except Exception:
            objects = None
        if isinstance(objects, list):
            lines = []
            for obj in objects:
                blob = json.dumps(obj).lower()
                if not any(p in blob for p in _ARCTIS_PATTERNS):
                    continue
                props = (obj.get('info') or {}).get('props') or {}
                lines.append(
                    f"id={obj.get('id')} "
                    f"name={props.get('node.name') or props.get('device.name', '?')} "
                    f"class={props.get('media.class', '?')} "
                    f"desc={props.get('node.description') or props.get('device.description', '')}"
                )
            return '\n'.join(lines)
    raw = _run_out(['pactl', 'list', 'sinks'], timeout=5.0)
    blocks = re.split(r'\n(?=Sink #)', raw)
    kept = [b for b in blocks if any(p in b.lower() for p in _ARCTIS_PATTERNS)]
    return '\n'.join(kept).strip()


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

    # WirePlumber restore-stream state — an entry that pins an Arctis loopback
    # (Arctis_*_sink_out) to the physical ALSA sink is re-applied at every
    # recreate and drives the endless mislink loop on WirePlumber 0.5.x (#100).
    info['wp_restore_stream_arctis'] = ''
    try:
        _rs = Path.home() / '.local' / 'state' / 'wireplumber' / 'restore-stream'
        if _rs.is_file():
            _hits = [
                ln for ln in _rs.read_text(errors='replace').splitlines()
                if 'Arctis' in ln and ('target' in ln or 'alsa_output' in ln)
            ]
            info['wp_restore_stream_arctis'] = '\n'.join(_hits[:40])
    except Exception:
        info['wp_restore_stream_arctis'] = ''

    # Filter-chain safe mode + config presence (issue #88). Safe mode gates
    # EQ config regeneration (sonar_to_pipewire.ensure_sonar_eq_configs): while
    # it is armed ASM will NOT recreate missing sonar-*-eq.conf, so the EQ nodes
    # never load and every loopback orphans on an absent target = no audio. This
    # is invisible without surfacing the marker, so collect it plus the active
    # and ASM-disabled config directories to tell "safe mode armed" apart from
    # "user moved the configs away".
    info['filter_chain_safe_mode'] = ''
    info['filter_chain_conf_active'] = ''
    info['filter_chain_conf_disabled'] = ''
    try:
        _marker = Path.home() / '.config' / 'arctis_manager' / 'filter_chain_safe_mode.json'
        if _marker.is_file():
            info['filter_chain_safe_mode'] = (
                'ARMED — EQ config regeneration is suppressed\n'
                + _marker.read_text(errors='replace').strip()
            )
        else:
            info['filter_chain_safe_mode'] = 'not armed'
    except Exception:
        info['filter_chain_safe_mode'] = ''
    try:
        _cdir = Path.home() / '.config' / 'pipewire' / 'filter-chain.conf.d'
        if _cdir.is_dir():
            info['filter_chain_conf_active'] = '\n'.join(
                sorted(p.name for p in _cdir.glob('*.conf'))
            )
        _ddir = _cdir.parent / 'filter-chain.conf.d.disabled'
        if _ddir.is_dir():
            info['filter_chain_conf_disabled'] = '\n'.join(
                sorted(p.name for p in _ddir.glob('*.conf'))
            )
    except Exception:
        pass

    # --- PipeWire runtime / container diagnostics (issue #74) ----------------
    # When ASM runs inside Distrobox/Flatpak, PipeWire is only reachable
    # through forwarded sockets. These fields show whether the sockets are
    # actually passed through and whether PipeWire sees the headset at all.
    info['pipewire_runtime_dir'] = os.environ.get('PIPEWIRE_RUNTIME_DIR', '<unset>')
    info['pulse_server'] = os.environ.get('PULSE_SERVER', '<unset>')
    info['container_env'] = _detect_container_env()

    info['pw_sources'] = _run_out(['pactl', 'list', 'sources', 'short'])

    info['filter_chain_status'] = (
        _run_out(['systemctl', '--user', 'is-active', 'filter-chain']) or 'unknown'
    )
    info['pw_service_status'] = ' '.join(
        f"{unit}={_run_out(['systemctl', '--user', 'is-active', unit]) or 'unknown'}"
        for unit in ('pipewire', 'pipewire-pulse')
    )

    info['pw_arctis_nodes'] = _arctis_pw_nodes()

    info['journalctl_pipewire'] = _run_out(
        ['journalctl', '--user', '-u', 'pipewire', '-n', '20', '--no-pager'],
    )
    info['journalctl_filter_chain'] = _run_out(
        ['journalctl', '--user', '-u', 'filter-chain', '-n', '80', '--no-pager'],
    )

    # A filter-chain SIGSEGV (issue #88) leaves a coredump whose backtrace names
    # the offending module — the single most useful artifact to locate the crash.
    # coredumpctl is systemd-only; _run_out returns '' when it's absent.
    _coredump_raw = _run_out(
        ['coredumpctl', 'info', '--no-pager', 'pipewire'], timeout=10.0,
    )
    info['coredump_filter_chain'] = (
        '\n'.join(_coredump_raw.splitlines()[-200:]) if _coredump_raw else ''
    )

    # The generated filter-chain configs themselves: a LADSPA plugin referenced
    # here but absent on the host is the most likely segfault cause.
    _fc_conf_dir = Path.home() / '.config' / 'pipewire' / 'filter-chain.conf.d'
    _fc_conf_entries: list[str] = []
    if _fc_conf_dir.is_dir():
        try:
            for _p in sorted(_fc_conf_dir.iterdir()):
                if not _p.is_file():
                    continue
                try:
                    _fc_content = _p.read_text(encoding='utf-8', errors='replace')
                except OSError as _e:
                    _fc_content = f'(could not read: {_e!r})'
                _fc_conf_entries.append(
                    f'### {_p.name}\n```\n{_fc_content.strip()}\n```'
                )
        except OSError:
            pass
    info['filter_chain_confs'] = '\n\n'.join(_fc_conf_entries)

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

    # ── Gamescope / Steam Game Mode detection ─────────────────────────────────
    # Under Bazzite (and other Steam Deck / Gamescope setups) the WirePlumber
    # routing policy keeps changing, which can trigger sustained loopback flapping
    # (issue #90).  Detecting this session upfront helps triage.
    _gamescope_by_proc = bool(_run_out(['pgrep', '-x', 'gamescope']))
    _desktop_val = (
        os.environ.get('XDG_CURRENT_DESKTOP', '')
        + ' '
        + os.environ.get('XDG_SESSION_DESKTOP', '')
    ).lower()
    _gamescope_by_env = 'gamescope' in _desktop_val
    if _gamescope_by_proc and _gamescope_by_env:
        info['gamescope_session'] = 'yes (process found + XDG env match)'
    elif _gamescope_by_proc:
        info['gamescope_session'] = 'yes (gamescope process found)'
    elif _gamescope_by_env:
        info['gamescope_session'] = 'yes (XDG_CURRENT_DESKTOP/XDG_SESSION_DESKTOP contains "gamescope")'
    else:
        info['gamescope_session'] = 'no'

    # ── Loopback watchdog activity summary ────────────────────────────────────
    # Count occurrences of key watchdog log patterns in the already-captured
    # arctis-manager journal so maintainers can instantly see if flapping is
    # happening without grepping through the full log.
    _WATCHDOG_KEYWORDS = (
        '_loopback_watchdog',
        'restarted dead',
        'mislinked',
        'orphaned',
        'flapping',
        'backing off',
    )
    _log_text = info.get('logs', '')
    if _log_text:
        _activity: dict[str, int] = {}
        for _kw in _WATCHDOG_KEYWORDS:
            _count = _log_text.lower().count(_kw.lower())
            if _count:
                _activity[_kw] = _count
        info['loopback_watchdog_activity'] = _activity
    else:
        info['loopback_watchdog_activity'] = {}

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
        f'- **Container environment**: {info.get("container_env", "?")}',
        f'- **PIPEWIRE_RUNTIME_DIR**: `{info.get("pipewire_runtime_dir", "?")}`',
        f'- **PULSE_SERVER**: `{info.get("pulse_server", "?")}`',
        f'- **PipeWire services**: {info.get("pw_service_status", "?")}',
        f'- **filter-chain.service**: {info.get("filter_chain_status", "?")}',
        f'- **Gamescope session**: {info.get("gamescope_session", "?")}',
        '',
    ]

    # Gamescope / Game Mode section — only when detected, so regular desktop
    # reports stay uncluttered.
    if info.get('gamescope_session', 'no') != 'no':
        lines += [
            '## Gamescope / Steam Game Mode',
            '<!-- Gamescope session detected.  WirePlumber routing policy in Game Mode',
            '     can repeatedly mis-route loopbacks, causing audio cuts (issue #90). -->',
            f'- **Detection**: {info.get("gamescope_session", "?")}',
            f'- **XDG_CURRENT_DESKTOP**: `{os.environ.get("XDG_CURRENT_DESKTOP", "<unset>")}`',
            f'- **XDG_SESSION_DESKTOP**: `{os.environ.get("XDG_SESSION_DESKTOP", "<unset>")}`',
            '',
        ]

    # Loopback watchdog activity — only shown when there is something to report.
    _watchdog_activity = info.get('loopback_watchdog_activity', {})
    if _watchdog_activity:
        lines += [
            '## Loopback watchdog activity (from recent daemon logs)',
            '<!-- Non-zero counts here indicate the watchdog had to intervene.',
            '     High "flapping" or "backing off" counts = issue #90 (Gamescope). -->',
            '```',
            *[f'{kw}: {count}' for kw, count in sorted(_watchdog_activity.items())],
            '```',
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

    pw_sources = info.get('pw_sources', '')
    if pw_sources:
        lines += [
            '## PipeWire sources',
            '```',
            pw_sources,
            '```',
            '',
        ]

    # Always shown: an EMPTY node list while USB sees the headset is exactly
    # the signal that PipeWire never created the ALSA nodes (issue #74).
    lines += [
        '## PipeWire — Arctis nodes',
        '<!-- Empty while the USB section above shows the headset = PipeWire',
        '     does not see the device (common in Distrobox when the PipeWire',
        '     sockets are not forwarded into the container). -->',
        '```',
        info.get('pw_arctis_nodes', '') or '(none — PipeWire does not see any Arctis node)',
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

    restore_stream = info.get('wp_restore_stream_arctis', '')
    if restore_stream:
        lines += [
            '## WirePlumber restore-stream — Arctis targets',
            '<!-- A stored target pointing at alsa_output...analog-stereo here is the',
            '     restore-stream poison that pins the loopback to the physical sink',
            '     and drives the endless mislink loop (#100). Fix: stop wireplumber,',
            '     remove the Arctis lines from ~/.local/state/wireplumber/restore-stream,',
            '     restart wireplumber. -->',
            '```',
            restore_stream,
            '```',
            '',
        ]

    safe_mode = info.get('filter_chain_safe_mode', '')
    active_conf = info.get('filter_chain_conf_active', '')
    disabled_conf = info.get('filter_chain_conf_disabled', '')
    if safe_mode or active_conf or disabled_conf:
        # Flag the common failure: EQ nodes can't load if their .conf is not in
        # the active dir. If any sonar-*-eq.conf is missing here, the loopbacks
        # will orphan on an absent target and there will be no audio (#88).
        _expected_eq = {
            'sonar-game-eq.conf', 'sonar-chat-eq.conf',
            'sonar-media-eq.conf', 'sonar-output-eq.conf',
        }
        _present = set(active_conf.splitlines())
        _missing = sorted(_expected_eq - _present)
        lines += [
            '## Filter-chain safe mode & config presence',
            '<!-- Safe mode ARMED suppresses EQ config regeneration (#88): missing',
            '     sonar-*-eq.conf below means those EQ nodes never load and every',
            '     loopback orphans on an absent target = no audio. Recovery: reset',
            '     safe mode from the app (re-enables EQ), then restart the daemon. -->',
            f'- **Safe mode**: {safe_mode or "(unknown)"}',
        ]
        if _missing:
            lines.append(
                f'- ⚠️ **Missing EQ configs (no audio on these channels)**: `{", ".join(_missing)}`'
            )
        lines += [
            '',
            '`filter-chain.conf.d/` (active):',
            '```',
            active_conf or '(empty — no ASM filter-chain configs loaded)',
            '```',
            '`filter-chain.conf.d.disabled/` (moved aside by ASM safe mode):',
            '```',
            disabled_conf or '(none)',
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

    jc_pw = info.get('journalctl_pipewire', '')
    if jc_pw:
        lines += [
            '## PipeWire logs (`journalctl --user -u pipewire`, last 20)',
            '```',
            jc_pw[-3000:],
            '```',
            '',
        ]

    jc_fc = info.get('journalctl_filter_chain', '')
    if jc_fc:
        lines += [
            '## filter-chain logs (`journalctl --user -u filter-chain`, last 80)',
            '```',
            jc_fc[-6000:],
            '```',
            '',
        ]

    coredump = info.get('coredump_filter_chain', '')
    if coredump:
        lines += [
            '## filter-chain coredump backtrace (`coredumpctl info pipewire`)',
            '<!-- Captured from the systemd coredump store. Empty on non-systemd',
            '     distros or when no coredump was recorded. -->',
            '```',
            coredump[-6000:],
            '```',
            '',
        ]

    fc_confs = info.get('filter_chain_confs', '')
    if fc_confs:
        lines += [
            '## ASM filter-chain configs (`~/.config/pipewire/filter-chain.conf.d/`)',
            '<!-- A LADSPA plugin referenced here but absent on the host filesystem',
            '     is the most likely segfault cause (issue #88). -->',
            '',
            fc_confs,
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


def format_bug_report_short(traceback_str: Optional[str] = None,
                            attachment_path: Optional[Path] = None) -> str:
    """Compact issue-body version of the report — fits in GitHub's URL params.

    The full report (USB tree, udev rules content, sinks, wpctl, journalctl)
    is too large for `?body=` (browsers cap query strings around 8 kB and
    GitHub silently truncates). Keep the URL body short and ask the user to
    drop the diagnostic file as an attachment in the issue editor.
    """
    info = collect_system_info()
    libs = info.get('python_libs', {})
    methods = info.get('install_methods', [])

    lines = [
        '## Environment',
        f'- **ASM version**: {info.get("version", "unknown")}',
        f'- **Python**: {info.get("python", "unknown")}',
        f'- **OS**: {info.get("distro", "unknown")} (kernel {info.get("kernel", "?")})',
        f'- **PipeWire**: {info.get("pipewire", "unknown")}',
        f'- **Desktop / Session**: {info.get("desktop", "?")} / {info.get("session_type", "?")}',
        f'- **USB monitor backend**: {info.get("usb_monitor_backend", "?")}',
        f'- **Container environment**: {info.get("container_env", "?")}',
        f'- **Install methods**: {", ".join(methods) or "?"}',
        '',
        '## Library versions',
        ', '.join(f'{k}={v}' for k, v in libs.items() if not v.startswith('(')),
        '',
    ]

    if traceback_str:
        # Last 30 lines is enough to identify the failing frame; the full
        # traceback is in the attachment.
        tb_short = '\n'.join(traceback_str.strip().splitlines()[-30:])
        lines += [
            '## Crash traceback (last 30 lines)',
            '```',
            tb_short,
            '```',
            '',
        ]

    if attachment_path is not None:
        lines += [
            '## Full diagnostic',
            f'> Drag-and-drop **`{attachment_path.name}`** into the issue editor below.',
            f'> File location on disk: `{attachment_path}`',
            '> Contains: USB tree, udev rules, PA/PW sinks, WirePlumber state, journalctl logs.',
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


def write_full_report_to_file(traceback_str: Optional[str] = None) -> Path:
    """Write the full bug report (the heavy one) to a temp-ish path the user
    can drag-and-drop into the GitHub issue editor. Returns the path."""
    target_dir = Path.home() / '.cache' / 'arctis-sound-manager' / 'reports'
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    path = target_dir / f'bug-report-{stamp}.md'
    path.write_text(format_bug_report(traceback_str), encoding='utf-8')
    return path


def is_gh_cli_ready() -> bool:
    """True iff `gh` CLI is installed AND authenticated. The auth check is
    a quick `gh auth status` — exits non-zero when no token is configured."""
    if not _which('gh'):
        return False
    try:
        r = subprocess.run(
            ['gh', 'auth', 'status'],
            capture_output=True, text=True, timeout=4,
        )
        return r.returncode == 0
    except Exception:
        return False


def _which(cmd: str) -> bool:
    try:
        r = subprocess.run(['which', cmd], capture_output=True, text=True, timeout=2)
        return r.returncode == 0 and bool(r.stdout.strip())
    except Exception:
        return False


def submit_via_gh_cli(title: str, short_body: str, full_report_path: Path,
                     repo: str = 'loteran/Arctis-Sound-Manager') -> Optional[str]:
    """File the issue end-to-end via `gh` CLI:
      1. Upload the full diagnostic as a SECRET gist (not searchable, but
         accessible to anyone with the URL — same visibility as the issue).
      2. Append the gist URL to the short body.
      3. Create the issue.
      4. Return the new issue URL.

    Returns None on any failure so the caller can fall back to the manual
    drag-and-drop flow.
    """
    try:
        gist = subprocess.run(
            ['gh', 'gist', 'create', '--filename', full_report_path.name,
             '--desc', f'Arctis Sound Manager — {title}',
             str(full_report_path)],
            capture_output=True, text=True, timeout=15, check=True,
        )
        gist_url = gist.stdout.strip().splitlines()[-1].strip()
    except Exception:
        return None
    if not gist_url.startswith('https://'):
        return None

    body_with_link = (
        f'{short_body}\n\n'
        f'## Full diagnostic (gist)\n'
        f'{gist_url}\n'
    )
    try:
        issue = subprocess.run(
            ['gh', 'issue', 'create', '--repo', repo,
             '--label', 'bug', '--title', title, '--body', body_with_link],
            capture_output=True, text=True, timeout=15, check=True,
        )
        for line in issue.stdout.strip().splitlines():
            line = line.strip()
            if line.startswith('https://') and '/issues/' in line:
                return line
    except Exception:
        return None
    return None


def github_issue_url(title: str, body: Optional[str] = None) -> str:
    """Build a `new issue` URL. *body* is encoded as a query param when given;
    keep it under ~6 kB or browsers / GitHub will truncate."""
    params = f'labels=bug&title={quote(title)}'
    if body:
        params += f'&body={quote(body)}'
    return f'{GITHUB_ISSUES_URL}?{params}'


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
