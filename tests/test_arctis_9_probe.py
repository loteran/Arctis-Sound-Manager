# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""
An Arctis 9 probe (#331) showed two profile mistakes. The info_table reply
does not echo its 0x20 request — it starts with `ready` (0xaa) — so the
`starts_with: 0x20` mapping never matched and ASM never read the battery,
the ChatMix dial or whether the headset was on. And the inactivity timer is
big-endian seconds on two bytes: ASM sent the chosen minutes as is, so
"90 minutes" switched the headset off after 90 seconds.
"""

from __future__ import annotations

from ruamel.yaml import YAML

from arctis_sound_manager.config import DeviceConfiguration, parsed_status
from arctis_sound_manager.core import CoreEngine
from tests.test_status_untagged_replies import DEVICES_DIR, _make_engine, _read, _send

_yaml = YAML(typ="safe")

# The info_table reply from the probe: rf_status 0x03, a stale status.
_PROBE = [0xaa, 0x03, 0x01, 0x9c, 0x00, 0x00, 0x00, 0x00, 0xe8, 0x00, 0x00, 0x00]
# The same with the headset connected (rf_status 0x01), as the mainline
# driver reads it.
_INFO_TABLE = [0xaa, 0x01] + _PROBE[2:]


def _arctis_9() -> DeviceConfiguration:
    return DeviceConfiguration(_yaml.load(DEVICES_DIR / "arctis_9.yaml"))


def test_the_info_table_reply_is_read():
    engine = _make_engine(_arctis_9())

    _send(engine, 0x20)
    _read(engine, _INFO_TABLE)

    parsed = parsed_status(engine.device_status, engine.device_config)
    assert parsed["headset_power_status"] == "on"
    assert parsed["headset_battery_charge"] == 100  # 0x9c, past 0x9a


def test_a_stale_status_is_offline():
    engine = _make_engine(_arctis_9())

    _send(engine, 0x20)
    _read(engine, _PROBE)

    parsed = parsed_status(engine.device_status, engine.device_config)
    assert parsed["headset_power_status"] == "off"


def test_shutdown_delay_goes_out_as_big_endian_seconds():
    engine = _make_engine(_arctis_9())
    engine.device_config = _arctis_9()
    config = next(c for section in engine.device_config.settings.values()
                  for c in section if c.name == "pm_shutdown")

    assert CoreEngine._resolve_update_sequence(engine, config, 90) == [0x04, 0x00, 0x15, 0x18]
    assert CoreEngine._resolve_update_sequence(engine, config, 10) == [0x04, 0x00, 0x02, 0x58]
    assert CoreEngine._resolve_update_sequence(engine, config, 0) == [0x04, 0x00, 0x00, 0x00]


def test_the_chatmix_dial_reads_as_attenuation():
    """#352 / #331: 0 is full volume, 19 the end of the dial, not the reverse."""
    def mix(chat_gain, game_gain):
        engine = _make_engine(_arctis_9())
        frame = list(_INFO_TABLE)
        frame[9], frame[10] = chat_gain, game_gain
        _send(engine, 0x20)
        _read(engine, frame)
        parsed = parsed_status(engine.device_status, engine.device_config)
        return parsed["chat_mix"], parsed["media_mix"]

    assert mix(0, 0) == (100, 100)       # dial centred
    assert mix(19, 0) == (0, 100)        # fully to one side
    assert mix(0, 19) == (100, 0)        # fully to the other
    chat, game = mix(10, 0)              # halfway: not snapped to an end
    assert 0 < chat < 100 and game == 100
