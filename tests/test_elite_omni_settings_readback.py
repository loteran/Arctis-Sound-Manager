# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""
The Nova Elite and Pro Omni had every setting pushed from profile defaults at each
start, over whatever the base station held (set in GG on a dual-boot, or on
the base itself). It now reads them first: two ordinary replies (0x01b0,
0x0180) and one feature report (audio_settings, 0x20). The frames below are
an Omni's and an Elite's own, from probes posted on #331.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from unittest.mock import MagicMock

from ruamel.yaml import YAML

from arctis_sound_manager.config import DeviceConfiguration
from arctis_sound_manager.core import CoreEngine

ROOT = Path(__file__).parent
DEVICES_DIR = ROOT.parent / "src" / "arctis_sound_manager" / "devices"
PROBE = {name: [int(b, 16) for b in frame.split()]
         for name, frame in json.loads((ROOT / "data" / "omni_probe_331.json").read_text()).items()}


def _omni() -> DeviceConfiguration:
    return DeviceConfiguration(YAML(typ="safe").load(DEVICES_DIR / "nova_pro_omni.yaml"))


def _engine(chosen: set[str] = frozenset()) -> MagicMock:
    engine = MagicMock()
    engine.device_config = _omni()
    engine.device_settings.settings = {}
    engine.device_settings.get.side_effect = lambda name, default=None: engine.device_settings.settings.get(name, default)
    engine.device_settings.was_chosen_by_user.side_effect = lambda name: name in chosen
    engine._readback_done = threading.Event()
    engine.get_feature_report.return_value = PROBE["audio_settings"]
    engine._absorb_settings_readback.side_effect = (
        lambda response: CoreEngine._absorb_settings_readback(engine, response))
    return engine


def test_every_reply_is_adopted(monkeypatch):
    monkeypatch.setattr("arctis_sound_manager.core.time.sleep", lambda _s: None)
    engine = _engine()

    done = CoreEngine._request_settings_readback(engine)
    engine.get_feature_report.assert_called_once_with(0x01, 1036)
    assert not done.is_set()  # the two ordinary replies come on the listen loop
    CoreEngine._absorb_settings_readback(engine, PROBE["wireless_settings"])
    CoreEngine._absorb_settings_readback(engine, PROBE["ux_settings"])
    assert done.is_set()

    assert engine.device_settings.settings == {
        # wireless_settings
        "bluetooth_power_status": 0, "bluetooth_auto_mute": 0, "transparent_level": 5,
        "noise_cancelling": 2, "mic_led_brightness": 10, "pm_shutdown": 0, "anc_level": 3,
        # ux_settings
        "station_screen_saver_timeout": 1, "station_home_screen_view": 0,
        "station_screensaver": 1, "station_home_screen_options": 0,
        # audio_settings
        "volume_limiter": 0, "mic_volume": 8, "line_out": 1,
        "line_out_stream_main_left_volume": 100, "line_out_stream_aux_volume": 100,
        # stream_mix mic, 0 on this base: what ASM's 3-value 0x47 left (#337)
        "line_out_stream_mic_volume": 0,
        "mic_side_tone": 0,        # state off: the level byte (1) is not the value
        "mic_noise_reduction": 1,  # on, boom level 1
        "equalizer_mic": 0, "equalizer_bluetooth": 0,
    }


def test_a_setting_the_user_chose_is_left_alone(monkeypatch):
    monkeypatch.setattr("arctis_sound_manager.core.time.sleep", lambda _s: None)
    engine = _engine(chosen={"mic_volume"})
    engine.device_settings.settings["mic_volume"] = 3

    CoreEngine._request_settings_readback(engine)

    assert engine.device_settings.settings["mic_volume"] == 3


def test_no_feature_reply_does_not_hold_up_device_init(monkeypatch):
    monkeypatch.setattr("arctis_sound_manager.core.time.sleep", lambda _s: None)
    engine = _engine()
    engine.get_feature_report.return_value = None

    done = CoreEngine._request_settings_readback(engine)
    CoreEngine._absorb_settings_readback(engine, PROBE["wireless_settings"])
    CoreEngine._absorb_settings_readback(engine, PROBE["ux_settings"])

    assert done.is_set()


def test_the_elite_reads_the_same_way(monkeypatch):
    """Same base spec as the Omni; frames from an Elite's probe on #331."""
    monkeypatch.setattr("arctis_sound_manager.core.time.sleep", lambda _s: None)
    probe = {name: [int(b, 16) for b in frame.split()]
             for name, frame in json.loads((ROOT / "data" / "elite_probe_331.json").read_text()).items()}
    engine = _engine()
    engine.device_config = DeviceConfiguration(YAML(typ="safe").load(DEVICES_DIR / "nova_elite.yaml"))
    engine.get_feature_report.return_value = probe["audio_settings"]

    done = CoreEngine._request_settings_readback(engine)
    CoreEngine._absorb_settings_readback(engine, probe["wireless_settings"])
    CoreEngine._absorb_settings_readback(engine, probe["ux_settings"])

    assert done.is_set()
    assert engine.device_settings.settings == {
        "bluetooth_default": 0, "bluetooth_auto_mute": 0,
        "transparent_noise_cancelling_level": 1, "noise_cancelling": 2,
        "mic_led_brightness": 10, "auto_off_time_minutes": 5, "wireless_mode": 0,
        "noise_cancelling_level": 3,
        "station_screen_saver_timeout": 1, "station_screen_brightness": 8,
        "station_home_screen_view": 0, "station_screensaver": 0,
        "station_home_screen_options": 0,
        "gain": 0, "mic_volume": 10, "line_out": 1, "equalizer_mic": 0,
        "equalizer_bluetooth": 1, "line_out_stream_main_left_volume": 100,
        "line_out_stream_aux_volume": 100,
        "line_out_stream_mic_volume": 0,
        "mic_sidetone_boom": 5,  # on, level 5
        "mic_sidetone_ear": 5,
    }


def test_at_start_the_base_station_wins_over_the_settings_file(monkeypatch):
    """#359 / #331: every key is in the file, so "in the file" is not "chosen"."""
    monkeypatch.setattr("arctis_sound_manager.core.time.sleep", lambda _s: None)
    engine = _engine(chosen={"mic_volume"})
    engine.device_settings.settings["mic_volume"] = 3
    engine._readback_wins_window = True
    CoreEngine._request_settings_readback(engine)
    assert engine.device_settings.settings["mic_volume"] == 8


def test_a_late_reply_does_not_undo_device_init(monkeypatch):
    monkeypatch.setattr("arctis_sound_manager.core.time.sleep", lambda _s: None)
    engine = _engine(chosen={"mic_volume"})
    engine.device_settings.settings["mic_volume"] = 3
    engine._readback_wins_window = False
    CoreEngine._request_settings_readback(engine)
    assert engine.device_settings.settings["mic_volume"] == 3


def test_only_base_stations_let_the_device_win():
    def wins(name):
        cfg = DeviceConfiguration(YAML(typ="safe").load(DEVICES_DIR / f"{name}.yaml"))
        return cfg.settings_readback_wins
    assert wins("nova_pro_wireless") and wins("nova_pro_omni") and wins("nova_elite")
    # Headsets that may power back on with factory values keep the user's file.
    assert not wins("nova_5") and not wins("nova_7_perc_battery")


def test_init_device_closes_the_window_before_device_init(monkeypatch):
    engine = MagicMock()
    seen = {}
    engine._request_settings_readback.side_effect = lambda: seen.setdefault(
        "during", engine._readback_wins_window) and None
    engine._send_device_init_sequence.side_effect = lambda **_k: (
        seen.setdefault("init", engine._readback_wins_window), (0, 0))[1]
    CoreEngine.init_device(engine)
    assert seen == {"during": True, "init": False}
