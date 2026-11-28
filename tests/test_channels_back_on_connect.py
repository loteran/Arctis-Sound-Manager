"""On connection, channels sent to another device come back — when asked for.

A channel sent to another device on the Channels page stays there through
every connection unless redirect_channels_back_on_connect is on (it needs
redirect_audio_on_connect too); off by default, so a channel sent to the
speakers for good keeps going there.
"""
import json
import threading
from unittest.mock import MagicMock, patch

import pytest

from arctis_sound_manager import sonar_to_pipewire as stp


def _engine(*, online=True, on_connect=True, channels_back=True):
    from arctis_sound_manager.core import CoreEngine
    engine = CoreEngine.__new__(CoreEngine)
    engine._device_lock = threading.Lock()
    engine.is_device_online = MagicMock(return_value=online)
    engine.general_settings = MagicMock(redirect_audio_on_connect=on_connect,
                                        redirect_channels_back_on_connect=channels_back)
    engine.logger = MagicMock()
    return engine


@pytest.fixture()
def outputs(tmp_path, monkeypatch):
    path = tmp_path / "channel_output_devices.json"
    path.write_text(json.dumps({"media": "alsa_output.usb-Generic_USB_Audio-00.HiFi__Speaker__sink"}))
    monkeypatch.setattr(stp, "CHANNEL_OUTPUTS_FILE", path)
    return path


def test_connection_sends_channels_back_and_relinks(outputs):
    with patch.object(stp, "ensure_physical_output_links") as relink, \
         patch.object(stp, "ensure_spatial_eq_links"):
        _engine().hand_channels_back_to_headset()
    assert not outputs.exists()
    relink.assert_called_once()


@pytest.mark.parametrize("online,on_connect,channels_back",
                         [(True, False, True), (False, True, True), (True, True, False)])
def test_left_alone_without_the_settings_or_the_headset(outputs, online, on_connect, channels_back):
    with patch.object(stp, "ensure_physical_output_links") as relink:
        _engine(online=online, on_connect=on_connect,
                channels_back=channels_back).hand_channels_back_to_headset()
    assert outputs.exists()
    relink.assert_not_called()


def test_off_by_default():
    from arctis_sound_manager.settings import GeneralSettings
    config = {c.name: c for c in GeneralSettings.settings_config}['redirect_channels_back_on_connect']
    assert GeneralSettings.redirect_channels_back_on_connect is False
    assert config.default_value is False
    assert config.get_kwargs()['visible_when'] == {'redirect_audio_on_connect': 1}
