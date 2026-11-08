# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Arctis Pro Wireless base's audio device must never lose its driver (#365).

The base enumerates twice: 0x1290 carries the HID command channel, 0x1294 the
ALSA card (interfaces 0-4 Audio Class, 5 HID). 0x1294 used to be listed in
product_ids as an "alternate PID", so a scan that ran before 0x1290 was
enumerated picked it as the device, kept the profile's interface 0 (its
AudioControl one, whose usage page cannot be read) and detached it from
snd-usb-audio on the libusb path. The card disappeared until a replug.
"""
from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from ruamel.yaml import YAML

from arctis_sound_manager.config import DeviceConfiguration
from arctis_sound_manager.core import CoreEngine

DEVICES_DIR = Path(__file__).parent.parent / "src" / "arctis_sound_manager" / "devices"
AUDIO_CLASS, HID_CLASS = 0x01, 0x03


def _pro_wireless() -> DeviceConfiguration:
    return DeviceConfiguration(YAML(typ="safe").load(DEVICES_DIR / "arctis_pro_wireless.yaml"))


def _audio_device() -> MagicMock:
    """The 0x1294 device as the user's lsusb -t shows it."""
    dev = MagicMock()
    dev.idVendor, dev.idProduct = 0x1038, 0x1294
    dev.get_active_configuration.return_value = [
        SimpleNamespace(bInterfaceNumber=n, bInterfaceClass=AUDIO_CLASS) for n in range(5)
    ] + [SimpleNamespace(bInterfaceNumber=5, bInterfaceClass=HID_CLASS)]
    dev.is_kernel_driver_active.return_value = True
    return dev


def _engine(config: DeviceConfiguration) -> CoreEngine:
    engine = CoreEngine.__new__(CoreEngine)
    engine.logger = MagicMock()
    engine.device_config = config
    engine._command_iface_override = None
    engine._hidraw = None
    engine.general_settings = SimpleNamespace(usb_transport=0)  # libusb
    return engine


def test_audio_pid_is_not_a_device_pid():
    cfg = _pro_wireless()
    assert cfg.product_ids == [0x1290]
    assert cfg.audio_product_ids == [0x1294]


def test_a_pid_cannot_be_both():
    raw = YAML(typ="safe").load(DEVICES_DIR / "arctis_pro_wireless.yaml")
    raw["device"]["audio_product_ids"] = [0x1290]
    with pytest.raises(ValueError, match="audio_product_ids"):
        DeviceConfiguration(raw)


def test_kernel_detach_leaves_audio_interfaces_to_their_driver():
    cfg = _pro_wireless()
    engine = _engine(cfg)
    dev = _audio_device()
    with patch.object(CoreEngine, "_record_usb_transport"), \
         patch("arctis_sound_manager.core.usb.util.claim_interface") as claim:
        engine.kernel_detach(dev, cfg)
    detached = {c.args[0] for c in dev.detach_kernel_driver.call_args_list}
    claimed = {c.args[1] for c in claim.call_args_list}
    assert 0 not in detached and 0 not in claimed
    assert not (detached | claimed) & set(range(5))


def test_hid_interfaces_are_still_detached_and_claimed():
    cfg = _pro_wireless()
    engine = _engine(cfg)
    dev = MagicMock()
    dev.idVendor, dev.idProduct = 0x1038, 0x1290
    dev.get_active_configuration.return_value = [
        SimpleNamespace(bInterfaceNumber=0, bInterfaceClass=HID_CLASS)]
    dev.is_kernel_driver_active.return_value = True
    with patch.object(CoreEngine, "_record_usb_transport"), \
         patch("arctis_sound_manager.core.usb.util.claim_interface") as claim:
        engine.kernel_detach(dev, cfg)
    dev.detach_kernel_driver.assert_any_call(0)
    assert any(c.args[1] == 0 for c in claim.call_args_list)


def test_a_declared_interface_that_is_not_hid_is_resolved_away():
    """Interface 0 of 0x1294 is AudioControl: proof it is not the channel."""
    cfg = _pro_wireless()
    engine = _engine(cfg)
    engine.usb_device = _audio_device()
    pages = {5: 0xFF00}
    with patch.object(CoreEngine, "_hid_usage_page", lambda self, n: pages.get(n)):
        engine.resolve_command_interface()
    assert engine._command_iface_override == 5


def test_alsa_lookup_includes_the_audio_pid():
    cfg = _pro_wireless()
    engine = _engine(cfg)
    sink = SimpleNamespace(name="alsa_output.usb-SteelSeries_Arctis_Pro_Wireless")
    engine.pa_audio_manager = MagicMock()
    engine.pa_audio_manager.get_arctis_sinks_classified.return_value = (sink, sink)
    engine.pa_audio_manager.get_physical_source.return_value = None
    engine._discover_physical_nodes(0x1038, 0x1290, attempts=1, delay=0)
    pids = engine.pa_audio_manager.get_arctis_sinks_classified.call_args.kwargs["product_id"]
    assert 0x1294 in pids and 0x1290 in pids


def test_the_audio_device_arriving_triggers_a_rescan():
    cfg = _pro_wireless()
    engine = _engine(cfg)
    engine.device_configurations = [cfg]
    engine._detect_lock = threading.Lock()
    with patch.object(CoreEngine, "configure_virtual_sinks") as configure:
        engine.on_device_connected(0x1038, 0x1294)
    configure.assert_called_once()
