# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""
The Nova 7 Gen 2 family's firmware blocks every command for 1.5 s after
save_to_flash (base_arctis_nova_7_gen2_tx: "FW will block all commands for
1.5s after Save triggered", HIDSLEEP 1500). device_init sent a status
request and a second save right behind the first, into that window.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from ruamel.yaml import YAML

from arctis_sound_manager.core import init_entry_pause_seconds

DEVICES_DIR = Path(__file__).parent.parent / "src" / "arctis_sound_manager" / "devices"


@pytest.mark.parametrize("profile", ["nova_7_perc_battery.yaml", "nova_7p_perc_battery.yaml"])
def test_every_save_is_followed_by_the_firmwares_pause(profile):
    init = YAML(typ="safe").load(DEVICES_DIR / profile)["device"]["device_init"]
    saves = [i for i, entry in enumerate(init) if entry == [0x09]]

    assert saves, "the profile no longer saves: update this test"
    for i in saves:
        assert i + 1 < len(init) and init_entry_pause_seconds(init[i + 1]) >= 1.5, (
            f"save_to_flash at entry {i} is not followed by a 1.5 s pause")
