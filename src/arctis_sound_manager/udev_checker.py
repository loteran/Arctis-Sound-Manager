# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

import logging
import re
import shutil
import subprocess
from pathlib import Path

from arctis_sound_manager.constants import (UDEV_RULES_PATHS,
                                            UDEV_UACCESS_RULES_PATHS)

_logger = logging.getLogger(__name__)

# Match a udev rules line that grants user access to a SteelSeries device:
#   - SUBSYSTEM=="usb"
#   - ATTRS{idVendor}=="1038"
#   - ATTRS{idProduct}=="<hex>"  (single value or '|' alternation)
#   - either MODE="0666" or TAG+="uaccess"
_RULE_PID_RE = re.compile(
    r'SUBSYSTEM=="usb".*?ATTRS\{idVendor\}=="1038".*?ATTRS\{idProduct\}=="([0-9a-fA-F|]+)"',
    re.IGNORECASE,
)
# The same grant for the device's /dev/hidraw* nodes (hidraw transport, the
# default since 1.7.0). A rules file written before 1.7.0 covers every PID on
# the usb lines and none here: the nodes stay root 0600, ASM falls back to
# libusb on every connect, and the check used to call that file current (#342).
_HIDRAW_PID_RE = re.compile(
    r'SUBSYSTEM=="hidraw".*?ATTRS\{idVendor\}=="1038".*?ATTRS\{idProduct\}=="([0-9a-fA-F|]+)"',
    re.IGNORECASE,
)


def _expected_pids() -> list[tuple[int, int]]:
    """Return (vendor_id, product_id) pairs from all device YAML configs.

    Charging-case PIDs count too: udev_rules.py writes them next to the
    device's own, and a rules file written before they existed must read as
    outdated — otherwise nothing ever rewrites it and the case stays
    unreadable (EACCES) on an otherwise current install (#217).
    """
    try:
        from arctis_sound_manager.config import load_device_configurations
        configs = load_device_configurations()
        return [(c.vendor_id, pid) for c in configs
                for pid in [*c.product_ids,
                            *(c.charging_case.product_ids if c.charging_case else [])]]
    except Exception as e:
        _logger.warning(f"udev_checker: failed to load device configurations: {e!r}")
        return []


def _pids_in_rules(content: str, subsystem: str = 'usb') -> set[int]:
    """Extract every PID covered by a real Arctis udev rule line.

    Strips comments before parsing so 'uaccess' / '1038' inside a `#` line
    cannot fool the checker, and supports the `pidA|pidB` alternation form
    used by the older hardcoded ruleset. *subsystem* picks the usb lines
    (default) or the hidraw ones.
    """
    rule_re = _HIDRAW_PID_RE if subsystem == 'hidraw' else _RULE_PID_RE
    pids: set[int] = set()
    for raw in content.splitlines():
        line = raw.split('#', 1)[0]
        if 'uaccess' not in line and 'MODE="0666"' not in line:
            continue
        m = rule_re.search(line)
        if not m:
            continue
        for token in m.group(1).split('|'):
            try:
                pids.add(int(token, 16))
            except ValueError:
                continue
    return pids


def _running_in_container() -> bool:
    # Delegates to container.py, the shared home for this check (it used to
    # be copy-pasted here, in systemd.py and in gui/system_deps_dialog.py, and
    # the copies drifted). Imported lazily and defensively: this must never be
    # the thing that stops the dialog from opening.
    try:
        from arctis_sound_manager.container import running_in_container
        return running_in_container()
    except Exception:  # noqa: BLE001
        return False


def _local_rules_contents(paths: list[str]) -> list[tuple[str, str]]:
    """(path, content) for every requested rules file readable locally."""
    found: list[tuple[str, str]] = []
    for p in paths:
        path = Path(p)
        if not path.exists():
            continue
        try:
            found.append((p, path.read_text()))
        except OSError as e:
            _logger.warning(f"udev_checker: cannot read {path}: {e!r}")
    return found


def _host_rules_contents(paths: list[str]) -> list[tuple[str, str]]:
    """The requested files on the host, when ASM runs inside a container.

    A distrobox container has its own /etc, and udev only ever reads the
    host's. scripts/distrobox/*.sh install the rules there, from the host,
    which is correct — but this checker looked inside the container and so
    reported 'missing' on a perfectly good Bazzite install, then offered to
    fix it by writing to the container's /etc, where nothing would read it.

    Best effort: no distrobox-host-exec, or a call that fails, simply leaves
    the local answer standing.
    """
    if not _running_in_container():
        return []
    host_exec = shutil.which('distrobox-host-exec')
    if host_exec is None:
        return []

    found: list[tuple[str, str]] = []
    for p in paths:
        try:
            result = subprocess.run(
                [host_exec, 'cat', p],
                capture_output=True, text=True, timeout=5, check=False)
        except (OSError, subprocess.SubprocessError) as e:
            _logger.warning(f"udev_checker: cannot read host rules via distrobox-host-exec: {e!r}")
            break
        if result.returncode == 0 and result.stdout.strip():
            found.append((f'{p} (host)', result.stdout))
    return found


def _effective(found: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """The one file udev actually reads among same-named copies, as a list.

    A file in /etc replaces a same-named one in /run, which replaces the
    packaged one in /usr/lib (or /lib) outright. Checking every copy let a
    stale /etc file left by an older asm-setup hide the package's current
    rules while this check, finding /usr/lib complete, answered 'ok' — so
    the stale copy was never rewritten (#217).
    """
    rank = lambda item: (0 if item[0].startswith('/etc/') else
                         1 if item[0].startswith('/run/') else 2)
    return sorted(found, key=rank)[:1]


def get_udev_rules_status() -> str:
    """Return the status of the installed udev rules.

    'ok'       — at least one rules file covers every known PID.
    'outdated' — a rules file exists but is missing PIDs added by new device YAMLs,
                 or has no hidraw lines for them (written before 1.7.0, #342).
    'missing'  — no rules file exists at any configured path.
    """
    expected = {pid for _, pid in _expected_pids()}
    if not expected:
        _logger.warning("udev_checker: no expected PIDs available — treating rules as missing.")
        return 'missing'

    any_main_file_found = False
    # The host is consulted only when the container's own filesystem has not
    # already answered 'ok', so the subprocess cost lands on the path that was
    # about to open a dialog anyway.
    for source in (_local_rules_contents, _host_rules_contents):
        main_rules = _effective(source(UDEV_RULES_PATHS))
        uaccess_rules = _effective(source(UDEV_UACCESS_RULES_PATHS))
        if main_rules:
            any_main_file_found = True
        for label, content in main_rules:
            covered = _pids_in_rules(content)
            if not expected.issubset(covered):
                missing = sorted(expected - covered)
                _logger.info(
                    f"udev_checker: {label} missing PIDs: "
                    + ', '.join(f'0x{pid:04x}' for pid in missing)
                )
                continue
            hidraw_missing = expected - _pids_in_rules(content, 'hidraw')
            if hidraw_missing:
                _logger.info(
                    f"udev_checker: {label} has no hidraw rule for: "
                    + ', '.join(f'0x{pid:04x}' for pid in sorted(hidraw_missing))
                )
                continue
            if any(expected.issubset(_pids_in_rules(content))
                   for _uaccess_label, content in uaccess_rules):
                return 'ok'
            _logger.info("udev_checker: main rules are current but the "
                         "70- uaccess companion is missing or outdated")

    return 'outdated' if any_main_file_found else 'missing'


def is_udev_rules_valid() -> bool:
    """Return True iff at least one installed rules file covers every known PID."""
    return get_udev_rules_status() == 'ok'


def hidraw_rules_cover(product_id: int) -> bool | None:
    """Whether the rules udev reads let a user open this PID's hidraw nodes.

    None when no ASM rules file is found at all (a distro that ships its own
    rules elsewhere, NixOS…): nothing to conclude, the caller just tries.
    False when the file udev reads has no hidraw line for the PID: opening the
    node can only fail with EACCES, and trying it first hands the interface to
    the kernel driver for two seconds before falling back to libusb — on an
    Arctis 7 dongle that churn ended with the port disabled (#342).
    """
    for source in (_local_rules_contents, _host_rules_contents):
        main_rules = _effective(source(UDEV_RULES_PATHS))
        if main_rules:
            return product_id in _pids_in_rules(main_rules[0][1], 'hidraw')
    return None
