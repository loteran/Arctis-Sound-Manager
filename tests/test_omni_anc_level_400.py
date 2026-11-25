# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Nova Pro Omni's ANC level must not be forced back to HIGH (#400).

`anc_level` is a hidden setting that follows whatever the base reports. A
level byte outside 1-3 (the spec's range) must neither be adopted nor pushed
back: the headset answers an invalid level by falling back to HIGH.
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


def _engine(settings: dict) -> MagicMock:
    engine = MagicMock()
    engine.logger = logging.getLogger("test400")
    engine.device_config = DeviceConfiguration(
        YAML(typ="safe").load(DEVICES_DIR / "nova_pro_omni.yaml"))
    engine.device_settings.settings = dict(settings)
    engine.device_settings.get.side_effect = (
        lambda name, default=None: engine.device_settings.settings.get(name, default))
    engine.device_settings.was_chosen_by_user.return_value = False
    engine._setting_accepts.side_effect = (
        lambda name, value: CoreEngine._setting_accepts(engine, name, value))
    engine._setting_default.side_effect = (
        lambda name: CoreEngine._setting_default(engine, name))
    engine._readback_done = threading.Event()
    engine._readback_pending = set()
    engine._readback_wins_window = False
    engine._readback_late_wins = set()
    return engine


def _wireless_settings_reply(anc_level: int) -> list[int]:
    frame = [0x01, 0xb0] + [0] * 62
    frame[0x0a] = 0x02          # ANC on
    frame[0x10] = anc_level
    return frame


def _setting(name: str):
    config = DeviceConfiguration(YAML(typ="safe").load(DEVICES_DIR / "nova_pro_omni.yaml"))
    return next(s for section in config.settings.values() for s in section if s.name == name)


def test_anc_level_accepts_only_the_spec_range():
    anc = _setting("anc_level")
    assert [v for v in range(0, 6) if anc.accepts(v)] == [1, 2, 3]


def test_a_valid_level_from_the_base_is_adopted():
    engine = _engine({"anc_level": 3})
    CoreEngine._absorb_settings_readback(engine, _wireless_settings_reply(1))
    assert engine.device_settings.settings["anc_level"] == 1


def test_an_out_of_range_level_is_not_adopted():
    engine = _engine({"anc_level": 1})
    CoreEngine._absorb_settings_readback(engine, _wireless_settings_reply(0))
    assert engine.device_settings.settings["anc_level"] == 1


def _sent_frames(engine) -> list[list]:
    return [call.args[0] for call in engine.translate_init_bytes.call_args_list]


def test_init_does_not_push_an_invalid_saved_level(monkeypatch):
    monkeypatch.setattr("arctis_sound_manager.core.time.sleep", lambda s: None)
    engine = _engine({"anc_level": 0})
    engine.send_command.return_value = True
    engine.translate_init_bytes.side_effect = lambda b: [x if isinstance(x, int) else 0 for x in b]
    CoreEngine._send_device_init_sequence(engine, context="settings replay")
    frames = _sent_frames(engine)
    assert not any("settings.anc_level" in f for f in frames)
    # The rest of the init still goes out.
    assert any("settings.noise_cancelling" in f for f in frames)


def test_init_pushes_a_valid_saved_level(monkeypatch):
    monkeypatch.setattr("arctis_sound_manager.core.time.sleep", lambda s: None)
    engine = _engine({"anc_level": 2})
    engine.send_command.return_value = True
    engine.translate_init_bytes.side_effect = lambda b: [x if isinstance(x, int) else 0 for x in b]
    CoreEngine._send_device_init_sequence(engine, context="settings replay")
    assert any("settings.anc_level" in f for f in _sent_frames(engine))


def _status(mode: str, anc_level: str | None) -> dict:
    headset = {"noise_cancelling": {"value": mode}}
    if anc_level is not None:
        headset["anc_level"] = {"value": anc_level}
    return {"headset": headset}


def test_widget_offers_the_anc_level_when_the_status_carries_it(monkeypatch):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import pytest
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])  # noqa: F841
    from arctis_sound_manager.gui import anc_widget

    sent = []
    monkeypatch.setattr(anc_widget.DbusWrapper, "change_setting",
                        lambda name, value: sent.append((name, value)))
    widget = anc_widget.QAncWidget()
    widget.update_status(_status("on", "medium"))
    assert not widget._anc_level_row.isHidden()
    assert widget._anc_level_pills["medium"]._active

    widget._anc_level_pills["low"].click()
    assert sent == [("anc_level", 1)]

    # A headset whose status has no anc_level (the Elite: its own card).
    other = anc_widget.QAncWidget()
    other.update_status(_status("on", None))
    assert other._anc_level_row.isHidden()
