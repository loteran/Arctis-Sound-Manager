# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""A setting that resets on replug must leave a trace of who reset it (#376).

The reporter's mic gain went back to 1 after every replug, and the journal
held nothing about it: a base station reply equal to the saved value adopted
nothing and logged nothing, an unanswered readback was logged at debug, and
device_init never said which saved values it pushed. These three lines are
what tells "the base forgot it" from "ASM wrote it back".
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from unittest.mock import MagicMock

from ruamel.yaml import YAML

from arctis_sound_manager.config import DeviceConfiguration
from arctis_sound_manager.core import CoreEngine

DEVICES_DIR = Path(__file__).parent.parent / "src" / "arctis_sound_manager" / "devices"


def _engine() -> MagicMock:
    engine = MagicMock()
    engine.logger = logging.getLogger("test376")
    engine.device_config = DeviceConfiguration(
        YAML(typ="safe").load(DEVICES_DIR / "nova_pro_wireless.yaml"))
    engine.device_settings.settings = {"mic_volume": 4}
    engine.device_settings.get.side_effect = (
        lambda name, default=None: engine.device_settings.settings.get(name, default))
    engine.device_settings.was_chosen_by_user.return_value = True
    engine._readback_done = threading.Event()
    engine._readback_pending = {0, 1, 2}
    engine._readback_wins_window = True
    return engine


def _audio_settings_reply(mic_volume: int) -> list[int]:
    frame = [0x06, 0x20] + [0] * 62
    frame[0x11] = mic_volume
    return frame


def test_a_reply_equal_to_the_saved_value_is_still_logged(caplog):
    engine = _engine()
    with caplog.at_level(logging.INFO, logger="test376"):
        CoreEngine._absorb_settings_readback(engine, _audio_settings_reply(4))
    assert "Settings readback from the base station" in caplog.text
    assert "mic_volume=4" in caplog.text
    assert engine.device_settings.settings["mic_volume"] == 4


def test_init_logs_the_saved_values_it_sends(caplog, monkeypatch):
    monkeypatch.setattr("arctis_sound_manager.core.time.sleep", lambda s: None)
    engine = _engine()
    engine.send_command.return_value = True
    engine.translate_init_bytes.side_effect = lambda b: [x if isinstance(x, int) else 0 for x in b]
    engine._setting_default.return_value = 0
    with caplog.at_level(logging.INFO, logger="test376"):
        CoreEngine._send_device_init_sequence(engine)
    assert "init_device sent the saved settings" in caplog.text
    assert "mic_volume=4" in caplog.text


def test_an_unanswered_readback_is_logged_at_info(caplog, monkeypatch):
    engine = _engine()
    engine._request_settings_readback.return_value = threading.Event()  # never set
    engine._send_device_init_sequence.return_value = (0, 1)
    monkeypatch.setattr("arctis_sound_manager.core._READBACK_WAIT_S", 0.0)
    with caplog.at_level(logging.INFO, logger="test376"):
        CoreEngine.init_device(engine)
    assert "Settings readback: no reply to 06 20" in caplog.text
