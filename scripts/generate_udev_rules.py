#!/usr/bin/env python3
"""Generate udev rules from device YAML files — writes to stdout.

Usage:
    python3 scripts/generate_udev_rules.py [devices_dir]

devices_dir defaults to src/arctis_sound_manager/devices/ relative to this
script. Used by the AUR PKGBUILD, RPM spec and debian/rules during package
build so rules are always generated from the source of truth (device YAMLs)
rather than hardcoded.

The actual generator lives in arctis_sound_manager.udev_rules so this script
and `asm-cli udev dump-rules` always emit identical output.
"""
import sys
from pathlib import Path

# When invoked from the package build (with the wheel not yet installed) we
# need to make src/ importable directly.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if (_REPO_ROOT / 'src').is_dir():
    sys.path.insert(0, str(_REPO_ROOT / 'src'))

from arctis_sound_manager.udev_rules import generate_rules  # noqa: E402


def main() -> None:
    if len(sys.argv) > 1:
        devices_dir = Path(sys.argv[1])
    else:
        devices_dir = _REPO_ROOT / 'src' / 'arctis_sound_manager' / 'devices'

    if not devices_dir.is_dir():
        print(f'error: devices directory not found: {devices_dir}', file=sys.stderr)
        sys.exit(1)

    sys.stdout.write(generate_rules([devices_dir]))


if __name__ == '__main__':
    main()
