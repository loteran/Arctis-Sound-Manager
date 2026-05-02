import logging
import re
from pathlib import Path

from arctis_sound_manager.constants import UDEV_RULES_PATHS

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


def _expected_pids() -> list[tuple[int, int]]:
    """Return (vendor_id, product_id) pairs from all device YAML configs."""
    try:
        from arctis_sound_manager.config import load_device_configurations
        configs = load_device_configurations()
        return [(c.vendor_id, pid) for c in configs for pid in c.product_ids]
    except Exception as e:
        _logger.warning(f"udev_checker: failed to load device configurations: {e!r}")
        return []


def _pids_in_rules(content: str) -> set[int]:
    """Extract every PID covered by a real Arctis udev rule line.

    Strips comments before parsing so 'uaccess' / '1038' inside a `#` line
    cannot fool the checker, and supports the `pidA|pidB` alternation form
    used by the older hardcoded ruleset.
    """
    pids: set[int] = set()
    for raw in content.splitlines():
        line = raw.split('#', 1)[0]
        if 'uaccess' not in line and 'MODE="0666"' not in line:
            continue
        m = _RULE_PID_RE.search(line)
        if not m:
            continue
        for token in m.group(1).split('|'):
            try:
                pids.add(int(token, 16))
            except ValueError:
                continue
    return pids


def get_udev_rules_status() -> str:
    """Return the status of the installed udev rules.

    'ok'       — at least one rules file covers every known PID.
    'outdated' — a rules file exists but is missing PIDs added by new device YAMLs.
    'missing'  — no rules file exists at any configured path.
    """
    expected = {pid for _, pid in _expected_pids()}
    if not expected:
        _logger.warning("udev_checker: no expected PIDs available — treating rules as missing.")
        return 'missing'

    any_file_found = False
    for p in UDEV_RULES_PATHS:
        path = Path(p)
        if not path.exists():
            continue
        try:
            content = path.read_text()
        except OSError as e:
            _logger.warning(f"udev_checker: cannot read {path}: {e!r}")
            continue
        any_file_found = True
        covered = _pids_in_rules(content)
        if expected.issubset(covered):
            return 'ok'
        missing = sorted(expected - covered)
        if missing:
            _logger.info(
                f"udev_checker: {path} missing PIDs: "
                + ', '.join(f'0x{pid:04x}' for pid in missing)
            )

    return 'outdated' if any_file_found else 'missing'


def is_udev_rules_valid() -> bool:
    """Return True iff at least one installed rules file covers every known PID."""
    return get_udev_rules_status() == 'ok'
