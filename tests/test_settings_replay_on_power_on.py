# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""
A headset that is switched off and back on comes up on its firmware defaults:
sidetone, mic volume and the volume limiter live in the headset, not in the
dongle. ASM used to push its settings only when the USB device appeared, so on
every family whose dongle stays plugged in a power cycle silently reverted
every control the user had set (#221).

These tests pin the re-push: it happens on the offline -> online transition,
it sends what init sends, it also covers the settings a profile leaves out of
device_init, and it does not fire twice in a row for a flapping link.
"""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock

from ruamel.yaml import YAML

from arctis_sound_manager.config import DeviceConfiguration
from arctis_sound_manager.core import CoreEngine
from arctis_sound_manager.settings import DeviceSettings

DEVICES_DIR = Path(__file__).parent.parent / "src" / "arctis_sound_manager" / "devices"
_yaml = YAML(typ="safe")


def _load_config(name: str) -> DeviceConfiguration:
    return DeviceConfiguration(_yaml.load(DEVICES_DIR / name))


def _make_engine(cfg: DeviceConfiguration, ds: DeviceSettings) -> MagicMock:
    engine = MagicMock()
    engine._device_lock = threading.RLock()
    engine.device_config = cfg
    engine.device_settings = ds
    engine.usb_device = MagicMock()
    engine.logger = MagicMock()
    engine.sent = []
    engine._last_settings_push = 0.0
    engine._settings_replay_timer = None
    engine.get_command_endpoint_address.return_value = 0
    engine.send_command.side_effect = lambda cmd, endpoint: engine.sent.append(list(cmd))
    # Bind the real methods — a bare MagicMock would shadow them with mocks.
    engine.translate_init_bytes = lambda b: CoreEngine.translate_init_bytes(engine, b)
    engine._setting_default = lambda name: CoreEngine._setting_default(engine, name)
    engine._resolve_update_sequence = lambda c, v: CoreEngine._resolve_update_sequence(engine, c, v)
    engine._send_device_init_sequence = lambda context="init_device": \
        CoreEngine._send_device_init_sequence(engine, context)
    engine._replay_settings_missing_from_init = lambda: \
        CoreEngine._replay_settings_missing_from_init(engine)
    return engine


def test_power_on_replays_the_saved_sidetone():
    """The reported symptom: sidetone back to factory after a headset reboot."""
    cfg = _load_config("nova_3_wireless.yaml")
    ds = DeviceSettings(cfg.vendor_id, cfg.product_ids[0])
    ds.mic_side_tone = 7
    engine = _make_engine(cfg, ds)

    CoreEngine.replay_device_settings(engine)

    assert [0x39, 7] in engine.sent, engine.sent


def test_power_on_replay_sends_what_init_sends():
    cfg = _load_config("nova_3_wireless.yaml")
    ds = DeviceSettings(cfg.vendor_id, cfg.product_ids[0])
    ds.mic_side_tone = 4
    ds.mic_volume = 9

    replayed = _make_engine(cfg, ds)
    CoreEngine.replay_device_settings(replayed)

    at_init = _make_engine(cfg, ds)
    CoreEngine._send_device_init_sequence(at_init)

    # The replay is a superset: same init frames, plus anything device_init
    # leaves out. Nothing the profile author listed may be dropped.
    assert at_init.sent, "device_init sent nothing — test would prove nothing"
    for frame in at_init.sent:
        assert frame in replayed.sent, (frame, replayed.sent)


def test_replay_covers_settings_missing_from_device_init():
    """Nova Pro Omni's sidetone is not in device_init — it is still restored."""
    cfg = _load_config("nova_pro_omni.yaml")
    ds = DeviceSettings(cfg.vendor_id, cfg.product_ids[0])
    covered = {
        b.split('.', 1)[1]
        for seq in (cfg.device_init or [])
        for b in seq
        if isinstance(b, str) and b.startswith('settings.')
    }
    assert 'mic_side_tone' not in covered, "profile changed — pick another setting"

    ds.settings['mic_side_tone'] = 3
    ds._user_chosen.add('mic_side_tone')
    engine = _make_engine(cfg, ds)

    CoreEngine._replay_settings_missing_from_init(engine)

    setting = next(c for section in cfg.settings.values()
                   for c in section if c.name == 'mic_side_tone')
    assert CoreEngine._resolve_update_sequence(engine, setting, 3) in engine.sent


def test_replay_leaves_untouched_settings_to_the_headset():
    """A setting the user never chose is not forced back to a profile default:
    whatever the headset came up with is as good an answer, and overwriting it
    is how a device configured in GG got flattened (see _absorb_settings_readback).
    """
    cfg = _load_config("nova_pro_omni.yaml")
    ds = DeviceSettings(cfg.vendor_id, cfg.product_ids[0])
    engine = _make_engine(cfg, ds)

    CoreEngine._replay_settings_missing_from_init(engine)

    assert engine.sent == []


def test_online_transition_schedules_a_replay():
    cfg = _load_config("nova_3_wireless.yaml")
    ds = DeviceSettings(cfg.vendor_id, cfg.product_ids[0])
    engine = _make_engine(cfg, ds)
    engine.is_device_online.return_value = True

    CoreEngine.on_device_status_changed(engine, 'headset_power_status', 0x03)

    engine._schedule_settings_replay.assert_called_once()


def test_going_offline_does_not_schedule_a_replay():
    cfg = _load_config("nova_3_wireless.yaml")
    ds = DeviceSettings(cfg.vendor_id, cfg.product_ids[0])
    engine = _make_engine(cfg, ds)
    engine.is_device_online.return_value = False

    CoreEngine.on_device_status_changed(engine, 'headset_power_status', 0x02)

    engine._schedule_settings_replay.assert_not_called()


def test_a_flapping_link_replays_once():
    """Two connection events in a row must not turn into two bursts of writes."""
    import time as _time

    cfg = _load_config("nova_3_wireless.yaml")
    ds = DeviceSettings(cfg.vendor_id, cfg.product_ids[0])
    engine = _make_engine(cfg, ds)
    engine._last_settings_push = _time.monotonic()

    started: list[threading.Timer] = []
    real_timer = threading.Timer

    def _tracking_timer(*args, **kwargs):
        t = real_timer(*args, **kwargs)
        started.append(t)
        return t

    threading.Timer = _tracking_timer  # type: ignore[misc]
    try:
        CoreEngine._schedule_settings_replay(engine)
    finally:
        threading.Timer = real_timer  # type: ignore[misc]

    assert started == []
    assert engine.sent == []
