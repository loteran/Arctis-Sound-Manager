"""One card's outputs are "USB Audio", "USB Audio #1", "USB Audio #2" — in the
order WirePlumber saw them, which changes between restarts. Not an identity."""
from types import SimpleNamespace

from arctis_sound_manager.dbus_service import build_sink_options
from arctis_sound_manager.pw_utils import sink_identity, unstable_nicks


def _sink(name, nick, description):
    return SimpleNamespace(name=name, description=description,
                           proplist={'node.name': name, 'node.nick': nick,
                                     'device.class': 'sound', 'media.class': 'Audio/Sink'})


SINKS = [
    _sink('alsa_output.usb-Generic_USB_Audio-00.HiFi__SPDIF__sink', 'USB Audio #2', 'USB Audio S/PDIF Output'),
    _sink('alsa_output.usb-Generic_USB_Audio-00.HiFi__Speaker__sink', 'USB Audio', 'USB Audio Speakers'),
    _sink('alsa_output.hdmi', 'Q95A', 'Q95A HDMI'),
]


def test_suffixed_siblings_are_unstable_and_others_are_not():
    assert unstable_nicks(SINKS) == {'USB Audio', 'USB Audio #2'}
    assert sink_identity(SINKS[1], unstable_nicks(SINKS)) == SINKS[1].name
    assert sink_identity(SINKS[2], unstable_nicks(SINKS)) == 'Q95A'


def test_the_list_stores_node_names_and_shows_descriptions(monkeypatch):
    import arctis_sound_manager.pw_utils as pw
    monkeypatch.setattr(pw, 'is_external_output_sink', lambda *a, **k: True)
    options = {o['id']: o['name'] for o in build_sink_options(SINKS, 'external_audio_devices')}
    assert options[SINKS[0].name] == 'USB Audio S/PDIF Output'
    assert options[SINKS[1].name] == 'USB Audio Speakers'
    assert options['Q95A'] == 'Q95A'
