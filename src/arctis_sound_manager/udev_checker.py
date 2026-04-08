from pathlib import Path

from arctis_sound_manager.constants import UDEV_RULES_PATHS


def is_udev_rules_valid() -> bool:
    """Returns True if a valid ASM udev rules file exists on disk."""
    for p in UDEV_RULES_PATHS:
        path = Path(p)
        if path.exists():
            try:
                content = path.read_text()
                return '1038' in content and 'uaccess' in content
            except OSError:
                pass
    return False
