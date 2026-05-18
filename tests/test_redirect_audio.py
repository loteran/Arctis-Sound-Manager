"""Tests for redirect_audio_on_disconnect() and redirect_audio() — issue #50.

User reported "Log is redirecting but I need to change in Channels to get
sound on speakers": pulse.default_set() changes the default sink but leaves
active streams glued to the dead ASM loopbacks. Fixed by migrating streams
via sink_input_move() and persisting the new default in pw-metadata.

Second bug: the disconnect guard only checked for Arctis_Game / Arctis_Chat,
silently skipping the case where the default was Arctis_Media or the raw
SteelSeries ALSA sink.
"""

from unittest.mock import MagicMock, call, patch

import pytest


# ── helpers ────────────────────────────────────────────────────────────────

def _make_sink(node_name, index, vendor_id_hex='', nick=''):
    s = MagicMock()
    s.name = node_name
    s.index = index
    s.proplist = {
        'node.name': node_name,
        'node.nick': nick or node_name,
        'device.vendor.id': vendor_id_hex,
    }
    return s


def _make_engine(default_name, default_vendor_hex=''):
    from arctis_sound_manager.core import CoreEngine

    engine = CoreEngine.__new__(CoreEngine)
    engine.general_settings = MagicMock(
        redirect_audio_on_disconnect=True,
        redirect_audio_on_disconnect_device='speakers',
    )
    pa = MagicMock()
    if default_name is None:
        pa.get_default_device.return_value = None
    else:
        pa.get_default_device.return_value = _make_sink(
            default_name, 99, vendor_id_hex=default_vendor_hex
        )
    engine.pa_audio_manager = pa
    return engine, pa


def _make_pa_manager():
    from arctis_sound_manager.pactl import PulseAudioManager

    pa = PulseAudioManager.__new__(PulseAudioManager)
    pa.logger = MagicMock()
    pa.pulse = MagicMock()
    return pa


# ── CoreEngine.redirect_audio_on_disconnect() ──────────────────────────────

@pytest.mark.parametrize('default_name', [
    'Arctis_Game',
    'Arctis_Chat',
    'Arctis_Media',
    'effect_input.sonar-game-eq',
    'effect_input.sonar-chat-eq',
    'effect_input.sonar-media-eq',
    'effect_input.sonar-output-eq',
    'effect_input.virtual-surround-7.1-hesuvi',
])
def test_disconnect_redirects_when_default_is_arctis_owned(default_name):
    engine, pa = _make_engine(default_name)
    engine.redirect_audio_on_disconnect()
    pa.redirect_audio.assert_called_once_with('speakers')


def test_disconnect_redirects_when_default_is_raw_steelseries_alsa():
    engine, pa = _make_engine(
        'alsa_output.usb-SteelSeries_Arctis_Nova_5-00.analog-stereo',
        default_vendor_hex='0x1038',
    )
    engine.redirect_audio_on_disconnect()
    pa.redirect_audio.assert_called_once_with('speakers')


def test_disconnect_skips_when_default_is_unrelated_sink():
    engine, pa = _make_engine(
        'alsa_output.pci-0000_00_1f.3.analog-stereo',
        default_vendor_hex='0x8086',
    )
    engine.redirect_audio_on_disconnect()
    pa.redirect_audio.assert_not_called()


def test_disconnect_skips_when_setting_disabled():
    engine, pa = _make_engine('Arctis_Game')
    engine.general_settings.redirect_audio_on_disconnect = False
    engine.redirect_audio_on_disconnect()
    pa.redirect_audio.assert_not_called()


def test_disconnect_skips_when_no_target_device_configured():
    engine, pa = _make_engine('Arctis_Game')
    engine.general_settings.redirect_audio_on_disconnect_device = None
    engine.redirect_audio_on_disconnect()
    pa.redirect_audio.assert_not_called()


def test_disconnect_redirects_when_no_default_sink_at_all():
    engine, pa = _make_engine(None)
    engine.redirect_audio_on_disconnect()
    pa.redirect_audio.assert_called_once_with('speakers')


# ── PulseAudioManager.redirect_audio() — stream migration ──────────────────

def test_redirect_audio_moves_streams_off_asm_loopbacks():
    pa = _make_pa_manager()

    speakers = _make_sink('alsa_output.speakers', 10, nick='speakers')
    arctis_game = _make_sink('Arctis_Game', 20)
    arctis_chat = _make_sink('Arctis_Chat', 21)

    si_discord = MagicMock(index=100, sink=21, proplist={'application.name': 'Discord'})
    si_firefox = MagicMock(index=101, sink=20, proplist={'application.name': 'firefox'})
    si_already = MagicMock(index=102, sink=10, proplist={'application.name': 'pre-routed'})

    pa.sink_list_wrapper = MagicMock(return_value=[speakers, arctis_game, arctis_chat])
    pa.pulse.sink_input_list.return_value = [si_discord, si_firefox, si_already]

    with patch('shutil.which', return_value=None):
        pa.redirect_audio('speakers')

    pa.pulse.default_set.assert_called_once_with(speakers)
    moves = pa.pulse.sink_input_move.call_args_list
    assert call(100, 10) in moves
    assert call(101, 10) in moves
    assert call(102, 10) not in moves  # already on speakers


def test_redirect_audio_unknown_sink_logs_error_and_skips():
    pa = _make_pa_manager()
    pa.sink_list_wrapper = MagicMock(return_value=[])
    pa.redirect_audio('nope')
    pa.pulse.default_set.assert_not_called()
    pa.logger.error.assert_called()


def test_redirect_audio_writes_pw_metadata_when_available():
    pa = _make_pa_manager()
    target = _make_sink('alsa_output.speakers', 10, nick='speakers')
    pa.sink_list_wrapper = MagicMock(return_value=[target])
    pa.pulse.sink_input_list.return_value = []

    with patch('shutil.which', return_value='/usr/bin/pw-metadata'), \
         patch('subprocess.run') as mock_run:
        pa.redirect_audio('speakers')

    keys = [c.args[0][2] for c in mock_run.call_args_list]
    assert 'default.configured.audio.sink' in keys
    assert 'default.audio.sink' in keys


def test_redirect_audio_skips_pw_metadata_when_not_available():
    pa = _make_pa_manager()
    target = _make_sink('alsa_output.speakers', 10, nick='speakers')
    pa.sink_list_wrapper = MagicMock(return_value=[target])
    pa.pulse.sink_input_list.return_value = []

    with patch('shutil.which', return_value=None), \
         patch('subprocess.run') as mock_run:
        pa.redirect_audio('speakers')

    mock_run.assert_not_called()
