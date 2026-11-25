"""Separate left/right output level on the headset sink."""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from arctis_sound_manager import output_balance as ob


def _gs(enabled=True, left=100, right=100):
    return SimpleNamespace(output_balance_enabled=enabled,
                           output_volume_left=left, output_volume_right=right)


def _sink(values, channels=('front-left', 'front-right'), name='alsa_output.headset'):
    return SimpleNamespace(name=name, proplist={'node.name': name},
                           channel_list=list(channels),
                           volume=SimpleNamespace(values=list(values),
                                                  value_flat=sum(values) / len(values)))


def test_levels_default_to_even_and_ignore_values_while_off():
    assert ob.levels(_gs(enabled=False, left=20, right=70)) == (1.0, 1.0)
    assert ob.levels(_gs(left=60, right=100)) == (0.6, 1.0)
    assert ob.levels(_gs(left=150, right=-3)) == (1.0, 0.0)    # clamped


def test_channel_values_scale_each_side_and_leave_the_rest():
    names = ['front-left', 'front-right', 'front-center', 'rear-left']
    assert ob.channel_values(names, 0.8, (0.5, 1.0)) == [0.4, 0.8, 0.8, 0.4]
    assert ob.channel_values(['mono'], 0.8, (0.5, 1.0)) == [0.8]


def test_main_volume_is_the_louder_side():
    assert ob.main_volume(_sink([0.4, 0.8])) == 0.8


def test_set_main_volume_keeps_the_balance():
    pulse = MagicMock()
    ob.set_main_volume(pulse, _sink([0.3, 0.6]), 0.5, (0.5, 1.0))
    assert list(pulse.volume_set.call_args.args[1].values) == pytest.approx([0.25, 0.5])


def test_apply_only_touches_named_unbalanced_sinks():
    headset = _sink([0.8, 0.8])
    other = _sink([0.5, 0.5], name='alsa_output.speakers')
    pulse = MagicMock()
    pulse.sink_list.return_value = [headset, other]
    assert ob.apply(pulse, {'alsa_output.headset'}, (0.5, 1.0)) is True
    sink, vol = pulse.volume_set.call_args.args
    assert sink is headset and list(vol.values) == pytest.approx([0.4, 0.8])

    pulse.reset_mock()
    pulse.sink_list.return_value = [_sink([0.4, 0.8])]       # already there
    assert ob.apply(pulse, {'alsa_output.headset'}, (0.5, 1.0)) is False
    pulse.volume_set.assert_not_called()


def test_turning_it_off_evens_the_sides_at_the_louder_level():
    pulse = MagicMock()
    pulse.sink_list.return_value = [_sink([0.4, 0.8])]
    assert ob.apply(pulse, {'alsa_output.headset'}, (1.0, 1.0)) is True
    assert list(pulse.volume_set.call_args.args[1].values) == pytest.approx([0.8, 0.8])


@pytest.mark.parametrize('setting, value, ok', [
    ('output_balance_enabled', True, True),
    ('output_balance_enabled', 1, False),
    ('output_volume_left', 60, True),
    ('output_volume_right', 101, False),
    ('output_volume_right', True, False),
])
def test_dbus_validates_and_applies(monkeypatch, setting, value, ok):
    from arctis_sound_manager.dbus_service import ArctisManagerDbusSettingsService
    service = ArctisManagerDbusSettingsService.__new__(ArctisManagerDbusSettingsService)
    service.logger = MagicMock()
    gs = _gs(enabled=False)
    on_disk = _gs(enabled=False)
    on_disk.write_to_file = MagicMock()
    monkeypatch.setattr('arctis_sound_manager.settings.GeneralSettings.read_from_file',
                        staticmethod(lambda: on_disk))
    service.core_engine = SimpleNamespace(general_settings=gs, apply_output_balance=MagicMock())
    started = MagicMock()
    monkeypatch.setattr('threading.Thread', lambda **kw: SimpleNamespace(start=started))
    fn = ArctisManagerDbusSettingsService.set_setting.__dict__['__DBUS_METHOD'].fn
    assert fn(service, setting, json.dumps(value)) is ok
    # Read-modify-write of the file, not the daemon's start-up copy
    assert on_disk.write_to_file.called is ok and started.called is ok
    if ok:
        assert getattr(gs, setting) == value and getattr(on_disk, setting) == value


def test_both_sides_below_full_is_stable():
    """Main 0.8 with left 80 %, right 60 %: read back as 0.8, not shrunk on
    every watchdog pass towards silence."""
    sink = _sink([0.64, 0.48])
    assert ob.main_volume(sink, (0.8, 0.6)) == pytest.approx(0.8)
    assert ob.is_balanced(sink, (0.8, 0.6))
    pulse = MagicMock()
    pulse.sink_list.return_value = [sink]
    assert ob.apply(pulse, {'alsa_output.headset'}, (0.8, 0.6)) is False


def test_a_slider_change_keeps_the_main_volume():
    """Left moved 80 % -> 60 % at main 0.5: read back under the old balance."""
    pulse = MagicMock()
    pulse.sink_list.return_value = [_sink([0.4, 0.5])]
    ob.apply(pulse, {'alsa_output.headset'}, (0.6, 1.0), current=(0.8, 1.0))
    assert list(pulse.volume_set.call_args.args[1].values) == pytest.approx([0.3, 0.5])
