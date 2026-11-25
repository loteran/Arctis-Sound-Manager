"""The tray builds the main window in the background so the first open is instant."""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from arctis_sound_manager.gui import systray_app


@pytest.fixture()
def tray():
    t = SimpleNamespace(logger=MagicMock(), _ensure_main_app=MagicMock(return_value=object()),
                        is_stopping=lambda: False)
    return t


def _run(tray):
    systray_app.QSystrayApp._prebuild_main_window(tray)


def test_builds_when_clips_is_off(tray, monkeypatch):
    monkeypatch.setattr('arctis_sound_manager.gui.clips_setup.clips_active', lambda: False)
    _run(tray)
    tray._ensure_main_app.assert_called_once()


def test_skipped_while_clips_is_active(tray, monkeypatch):
    """Its page binds the global shortcut and follows games once it exists —
    that must keep waiting for the window to be opened."""
    monkeypatch.setattr('arctis_sound_manager.gui.clips_setup.clips_active', lambda: True)
    _run(tray)
    tray._ensure_main_app.assert_not_called()


def test_not_rebuilt_and_not_while_stopping(tray, monkeypatch):
    monkeypatch.setattr('arctis_sound_manager.gui.clips_setup.clips_active', lambda: False)
    tray._main_app = object()
    _run(tray)
    tray._ensure_main_app.assert_not_called()
    del tray._main_app
    tray.is_stopping = lambda: True
    _run(tray)
    tray._ensure_main_app.assert_not_called()


def test_a_failed_build_is_left_to_the_first_open(tray, monkeypatch):
    monkeypatch.setattr('arctis_sound_manager.gui.clips_setup.clips_active', lambda: False)
    tray._ensure_main_app.side_effect = RuntimeError('boom')
    _run(tray)                                          # must not raise
    tray.logger.warning.assert_called_once()


def test_first_device_status_schedules_the_build(monkeypatch):
    shots = []
    monkeypatch.setattr(systray_app.QTimer, 'singleShot', lambda ms, fn: shots.append(ms))
    t = SimpleNamespace(last_device_status=None, _update_tray_icon=MagicMock(),
                        _prebuild_main_window=MagicMock())
    systray_app.QSystrayApp.on_new_status(t, {})                  # daemon up, no device yet
    assert shots == []
    systray_app.QSystrayApp.on_new_status(t, {'headset': {'x': {'value': 1}}})
    systray_app.QSystrayApp.on_new_status(t, {'headset': {'x': {'value': 2}}})
    assert shots == [systray_app._PREBUILD_AFTER_READY_MS]       # once, not per update
