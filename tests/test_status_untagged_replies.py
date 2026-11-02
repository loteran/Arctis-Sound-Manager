# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""
The Arctis Pro Wireless showed 50% battery whatever the charge (#305). Its
profile read the battery from byte[1] of the rf_status reply (0x41aa), which
the siberia-840 spec lays out as [rf_mode, rf_status]: rf_status sat at 2,
read as 2/4. The battery has its own read, battery_status (0x40aa), whose
reply is [battery_level] — and neither reply echoes its command, so the first
byte cannot say which question a frame answers. Mappings with `reply_to` take
the answer from the order the requests went out.
"""

from __future__ import annotations

import asyncio
import threading
from collections import deque
from pathlib import Path
from unittest.mock import MagicMock

from ruamel.yaml import YAML

from arctis_sound_manager.config import DeviceConfiguration, parsed_status
from arctis_sound_manager.core import CoreEngine

DEVICES_DIR = Path(__file__).parent.parent / "src" / "arctis_sound_manager" / "devices"
_yaml = YAML(typ="safe")


def _pro_wireless() -> DeviceConfiguration:
    return DeviceConfiguration(_yaml.load(DEVICES_DIR / "arctis_pro_wireless.yaml"))


def _make_engine(cfg: DeviceConfiguration) -> MagicMock:
    engine = MagicMock()
    engine.device_config = cfg
    engine.device_status = {}
    engine._device_lock = threading.RLock()
    engine._usb_write_lock = threading.Lock()
    engine._last_usb_write_monotonic = 0.0
    engine._untagged_requests_pending = deque()
    engine.guess_interface_endpoint.return_value = (0x81, 32)
    engine.get_command_endpoint_address.return_value = 0
    return engine


def _send(engine: MagicMock, request: int) -> None:
    CoreEngine.send_command(engine, [request], 0)


def _read(engine: MagicMock, frame: list[int]) -> None:
    engine.usb_device.read.return_value = frame + [0] * (32 - len(frame))
    asyncio.run(CoreEngine.listen_endpoint_loop(engine, 0))


def test_battery_comes_from_its_own_reply_not_rf_status():
    engine = _make_engine(_pro_wireless())

    _send(engine, 0x41aa)
    _send(engine, 0x40aa)
    _read(engine, [0x04, 0x02, 0x00])  # rf_status: linked, rf_status 2
    _read(engine, [0x03])              # battery_status: 3/4

    assert engine.device_status == {"headset_power_status": 0x04,
                                    "headset_battery_charge": 0x03}
    parsed = parsed_status(engine.device_status, engine.device_config)
    assert parsed["headset_battery_charge"] == 75


def test_the_spare_battery_reply_lands_on_its_own_request():
    """charger_status (0x42aa) is the spare battery's slot in the base. Its
    value is a presence state, not a level (#363)."""
    engine = _make_engine(_pro_wireless())

    for request in (0x41aa, 0x40aa, 0x42aa):
        _send(engine, request)
    _read(engine, [0x04, 0x02, 0x00])
    _read(engine, [0x01])
    _read(engine, [0x04])

    assert engine.device_status["headset_battery_charge"] == 0x01
    assert engine.device_status["charge_slot_battery"] == 0x04
    cfg = engine.device_config
    assert "charge_slot_battery" in cfg.status.representation["gamedac"]
    assert parsed_status(engine.device_status, cfg)["charge_slot_battery"] == "battery_in_base"


def test_a_frame_nobody_asked_for_is_not_read_as_battery():
    engine = _make_engine(_pro_wireless())

    _read(engine, [0x02, 0x02, 0x00])

    assert engine.device_status == {}


def test_the_poll_drops_requests_whose_reply_was_lost():
    """A stale entry would shift every later reply onto the wrong request."""
    engine = _make_engine(_pro_wireless())
    engine._untagged_requests_pending.append(0x40aa)
    engine.send_command.side_effect = lambda cmd, endpoint: _send(engine, cmd[0])

    CoreEngine.request_device_status(engine)

    assert list(engine._untagged_requests_pending) == [0x41aa, 0x40aa, 0x42aa]


def test_tagged_profiles_queue_nothing():
    engine = _make_engine(DeviceConfiguration(_yaml.load(DEVICES_DIR / "arctis_7.yaml")))

    _send(engine, 0x0618)

    assert not engine._untagged_requests_pending
