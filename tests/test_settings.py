"""Tests for settings — DeviceSettings, GeneralSettings."""

from pathlib import Path
from unittest.mock import patch

from arctis_sound_manager.settings import DeviceSettings, GeneralSettings


def test_device_settings_get_default():
    ds = DeviceSettings(0x1038, 0x12e0)
    assert ds.get("nonexistent", 42) == 42


def test_device_settings_setattr():
    ds = DeviceSettings(0x1038, 0x12e0)
    ds.gain = 2
    assert ds.settings["gain"] == 2
    assert ds.get("gain") == 2


def test_device_settings_write_read(tmp_path):
    with patch("arctis_sound_manager.settings.SETTINGS_FOLDER", tmp_path):
        ds = DeviceSettings(0x1038, 0x12e0)
        ds.gain = 2
        ds.mic_volume = 5
        ds.write_to_file()

        ds2 = DeviceSettings(0x1038, 0x12e0)
        ds2.gain = 0  # pre-populate keys
        ds2.mic_volume = 0
        ds2.read_from_file()
        assert ds2.get("gain") == 2
        assert ds2.get("mic_volume") == 5


def test_device_settings_read_nonexistent(tmp_path):
    with patch("arctis_sound_manager.settings.SETTINGS_FOLDER", tmp_path):
        ds = DeviceSettings(0x1038, 0x12e0)
        ds.read_from_file()  # should not raise


def test_general_settings_defaults():
    gs = GeneralSettings()
    assert gs.redirect_audio_on_connect is False
    assert gs.redirect_audio_on_disconnect is False
    assert gs.redirect_audio_on_disconnect_device is None


def test_general_settings_write_read(tmp_path):
    with patch("arctis_sound_manager.settings.SETTINGS_FOLDER", tmp_path):
        gs = GeneralSettings(redirect_audio_on_connect=True)
        gs.write_to_file()

        gs2 = GeneralSettings.read_from_file()
        assert gs2.redirect_audio_on_connect is True


def test_general_settings_read_nonexistent(tmp_path):
    with patch("arctis_sound_manager.settings.SETTINGS_FOLDER", tmp_path / "nope"):
        gs = GeneralSettings.read_from_file()
        assert gs.redirect_audio_on_connect is False


def test_general_settings_ignores_unknown_keys():
    gs = GeneralSettings(redirect_audio_on_connect=True, unknown_key="ignored")
    assert gs.redirect_audio_on_connect is True
    assert not hasattr(gs, "unknown_key")
