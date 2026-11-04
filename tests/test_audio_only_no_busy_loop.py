# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""#369: since 1.6.6 the Arctis Pro 2019 (and the Arctis 5) have audio-only
profiles with dial_interface_index: -1. That -1 was scanned as a dial
candidate; guess_interface_endpoint raised before any await, the listen task
ended at once and loop() resubmitted it at once: 100 % of a core."""
from __future__ import annotations

import asyncio
import threading
from unittest.mock import MagicMock

import pytest
from ruamel.yaml import YAML

from arctis_sound_manager.config import DeviceConfiguration
from arctis_sound_manager.core import CoreEngine
from tests.test_status_untagged_replies import DEVICES_DIR

_yaml = YAML(typ="safe")


@pytest.mark.parametrize("profile", ["arctis_pro_2019.yaml", "arctis_5_2018.yaml"])
def test_audio_only_profiles_scan_no_dial_interface(profile):
    engine = MagicMock()
    engine.device_config = DeviceConfiguration(_yaml.load(DEVICES_DIR / profile))
    engine.device_settings.get_dial_interface.return_value = None
    CoreEngine._update_active_dial_interfaces(engine)
    assert engine._active_extra_dial_interfaces == []


def test_a_missing_interface_backs_off_instead_of_returning_at_once(monkeypatch):
    engine = MagicMock()
    engine._device_lock = threading.RLock()
    engine._hidraw = None
    engine._endpointless_interfaces = set()
    engine.guess_interface_endpoint.side_effect = Exception("Failed to find interface")
    slept = []

    async def fake_sleep(s):
        slept.append(s)

    monkeypatch.setattr("arctis_sound_manager.core.asyncio.sleep", fake_sleep)
    asyncio.run(CoreEngine.listen_endpoint_loop(engine, -1))
    assert slept and slept[0] >= 1.0
