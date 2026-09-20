# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Real SteelSeries hardware with no vendor command channel at all (#266).

Distinct from devices/generic.yaml's 'generic: true' (no USB device, no
product_ids, manual sink picking): an 'audio_only' profile is still matched
by the USB scan on its real vendor/product id, and only skips the HID
conversation — because, unlike every other Arctis, this one has none to have
(a plain USB Audio Class headset whose only HID interface is a standard
Consumer Control page, not a vendor one).
"""
from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest

from arctis_sound_manager.config import DeviceConfiguration


def _valid_audio_only_raw(**overrides) -> dict:
    device = {
        "name": "Test Audio-Only Headset",
        "vendor_id": 0x1038,
        "product_ids": [0x9999],
        "audio_only": True,
        "command_transport": "interrupt",
        "command_interface_index": [-1, -1],
        "listen_interface_indexes": [],
        "dial_interface_index": -1,
        "command_padding": {"length": 64, "position": "end", "filler": 0x00},
    }
    device.update(overrides)
    return {"device": device}


# ── config validation ───────────────────────────────────────────────────────

def test_a_well_formed_audio_only_profile_loads():
    config = DeviceConfiguration(_valid_audio_only_raw())
    assert config.audio_only is True
    assert config.generic is False
    assert config.product_ids == [0x9999]
    assert config.status is None
    assert config.command_interface_index[0] == -1


def test_generic_and_audio_only_are_mutually_exclusive():
    with pytest.raises(ValueError, match="mutually exclusive"):
        DeviceConfiguration(_valid_audio_only_raw(generic=True, product_ids=[]))


def test_audio_only_still_needs_a_vendor_id():
    with pytest.raises(ValueError, match="vendor_id"):
        DeviceConfiguration(_valid_audio_only_raw(vendor_id=0))


def test_audio_only_still_needs_product_ids():
    with pytest.raises(ValueError, match="product_ids"):
        DeviceConfiguration(_valid_audio_only_raw(product_ids=[]))


def test_audio_only_rejects_a_real_command_interface():
    """A profile someone half-converted, still naming a real interface."""
    with pytest.raises(ValueError, match="command_interface_index"):
        DeviceConfiguration(_valid_audio_only_raw(command_interface_index=[3, 0]))


def test_audio_only_rejects_listen_interfaces():
    with pytest.raises(ValueError, match="listen_interface_indexes"):
        DeviceConfiguration(_valid_audio_only_raw(listen_interface_indexes=[3]))


def test_audio_only_rejects_a_default_dial_interface():
    """dial_interface_index defaults to 0 — a real interface — once
    listen_interface_indexes is empty and nothing else is said. Silently
    accepting that default would let a claim/detach cycle reach a real
    Audio Class interface behind an "nothing is touched" profile."""
    raw = _valid_audio_only_raw()
    del raw["device"]["dial_interface_index"]
    with pytest.raises(ValueError, match="dial_interface_index"):
        DeviceConfiguration(raw)


def test_audio_only_rejects_dial_candidates():
    with pytest.raises(ValueError, match="dial_interface_candidates"):
        DeviceConfiguration(_valid_audio_only_raw(dial_interface_candidates=[3, 4]))


# ── core.py: no interface is ever touched ───────────────────────────────────

def _engine_with(config: DeviceConfiguration):
    from arctis_sound_manager.core import CoreEngine

    engine = CoreEngine.__new__(CoreEngine)
    engine.logger = MagicMock()
    engine.device_config = config
    engine.usb_device = MagicMock()
    engine._command_iface_override = None
    return engine


def test_all_used_interfaces_is_empty_for_an_audio_only_profile():
    config = DeviceConfiguration(_valid_audio_only_raw())
    engine = _engine_with(config)

    assert engine._all_used_interfaces(config) == []


def test_resolve_command_interface_does_not_touch_the_device():
    """No point asking the hardware anything: -1 was declared on purpose,
    not a mistaken interface number to correct."""
    config = DeviceConfiguration(_valid_audio_only_raw())
    engine = _engine_with(config)

    engine.resolve_command_interface()

    engine.usb_device.get_active_configuration.assert_not_called()
    assert engine._command_iface_override is None


def test_kernel_detach_is_a_no_op():
    config = DeviceConfiguration(_valid_audio_only_raw())
    engine = _engine_with(config)
    engine.permission_error = None
    engine.usb_device.idVendor = 0x1038
    engine.usb_device.idProduct = 0x9999

    ok = engine.kernel_detach(engine.usb_device, config)

    assert ok is True
    engine.usb_device.is_kernel_driver_active.assert_not_called()


# ── the shipped profile itself ──────────────────────────────────────────────

def test_the_arctis_5_2018_profile_is_loaded():
    from arctis_sound_manager.config import load_device_configurations

    configs = load_device_configurations()
    matches = [c for c in configs if 0x12aa in c.product_ids]
    assert len(matches) == 1, "expected exactly one profile to claim PID 0x12aa"
    assert matches[0].audio_only is True
    assert matches[0].vendor_id == 0x1038
