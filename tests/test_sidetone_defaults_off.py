# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
test_sidetone_defaults_off.py — no profile may switch sidetone on by default.

A setting's default is not a hint: until the user moves the control, it is the
value ASM sends at every connection. The Nova Elite listed both of its
sidetones in device_init with a default of 5, so a headset whose owner never
opened the panel heard their own microphone, at half level, every time it
connected. The Nova Pro Omni carried the same default on its on-ear sidetone.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from ruamel.yaml import YAML

DEVICES = Path(__file__).parent.parent / "src" / "arctis_sound_manager" / "devices"


def _sidetones():
    for path in sorted(DEVICES.glob("*.yaml")):
        device = YAML(typ="safe").load(path)["device"]
        for section in (device.get("settings") or {}).values():
            for name, setting in (section or {}).items():
                if "sidetone" in name or "side_tone" in name:
                    yield pytest.param(setting, id=f"{path.stem}:{name}")


@pytest.mark.parametrize("setting", _sidetones())
def test_sidetone_defaults_to_off(setting):
    default = setting["default"]
    if "values_mapping" in setting:
        # YAML 1.1 reads a bare `off` as False.
        assert setting["values_mapping"][default] in ("off", False)
    else:
        assert default == setting["min"]
