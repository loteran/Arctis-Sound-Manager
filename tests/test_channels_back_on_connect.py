"""A headset connecting with "move the sound" on gets every channel back.

A channel sent to another device on the Channels page used to stay there
through every connection, so the Events card's "sound moves to the earbuds"
was false and what a connection would do could not be known.
"""
import json
import threading
from unittest.mock import MagicMock, patch

import pytest

from arctis_sound_manager import sonar_to_pipewire as stp


def _engine(*, online=True, on_connect=True):
    from arctis_sound_manager.core import CoreEngine
    engine = CoreEngine.__new__(CoreEngine)
    engine._device_lock = threading.Lock()
    engine.is_device_online = MagicMock(return_value=online)
    engine.general_settings = MagicMock(redirect_audio_on_connect=on_connect)
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


@pytest.mark.parametrize("online,on_connect", [(True, False), (False, True)])
def test_left_alone_without_the_setting_or_the_headset(outputs, online, on_connect):
    with patch.object(stp, "ensure_physical_output_links") as relink:
        _engine(online=online, on_connect=on_connect).hand_channels_back_to_headset()
    assert outputs.exists()
    relink.assert_not_called()
