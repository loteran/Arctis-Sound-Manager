"""Changing the disconnect device while the headset is away moves the sound now."""
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock


def _engine(*, online=False, previous=None, default="", target="speakers"):
    from arctis_sound_manager.core import CoreEngine
    engine = CoreEngine.__new__(CoreEngine)
    engine._device_lock = threading.Lock()
    engine.logger = MagicMock()
    engine.device_config = None
    engine.is_device_online = MagicMock(return_value=online)
    engine.general_settings = SimpleNamespace(redirect_audio_on_disconnect=True,
                                              redirect_audio_on_disconnect_device=target)
    engine.pa_audio_manager = MagicMock()
    engine.pa_audio_manager.get_default_device.return_value = SimpleNamespace(
        name=default, proplist={})
    if previous:
        engine._disconnect_redirected_to = previous
    return engine


def test_moves_from_where_asm_put_the_sound():
    engine = _engine(previous="headphones", default="headphones")
    engine.follow_disconnect_target()
    engine.pa_audio_manager.redirect_audio.assert_called_once_with("speakers")


def test_leaves_an_output_the_user_chose_by_hand():
    engine = _engine(previous="headphones", default="hdmi")
    engine.follow_disconnect_target()
    engine.pa_audio_manager.redirect_audio.assert_not_called()


def test_nothing_while_the_headset_is_on():
    engine = _engine(online=True, previous="headphones", default="headphones")
    engine.follow_disconnect_target()
    engine.pa_audio_manager.redirect_audio.assert_not_called()


def test_off_the_headsets_own_channels_when_nothing_was_redirected_yet():
    engine = _engine(default="Arctis_Media")
    engine.follow_disconnect_target()
    engine.pa_audio_manager.redirect_audio.assert_called_once_with("speakers")
