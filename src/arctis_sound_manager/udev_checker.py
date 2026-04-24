from pathlib import Path

from arctis_sound_manager.constants import UDEV_RULES_PATHS


def _expected_pids() -> list[tuple[int, int]]:
    """Return (vendor_id, product_id) pairs from all device YAML configs."""
    try:
        from arctis_sound_manager.config import load_device_configurations
        configs = load_device_configurations()
        return [(c.vendor_id, pid) for c in configs for pid in c.product_ids]
    except Exception:
        return []


def is_udev_rules_valid() -> bool:
    """Returns True if the installed udev rules file covers all known device PIDs."""
    for p in UDEV_RULES_PATHS:
        path = Path(p)
        if path.exists():
            try:
                content = path.read_text()
            except OSError:
                continue
            if 'uaccess' not in content:
                return False
            pids = _expected_pids()
            if not pids:
                return '1038' in content
            return all(f'{pid:04x}' in content for _, pid in pids)
    return False
