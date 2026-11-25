"""The Headset page's Events card: which events show, and how they're worded."""
from unittest.mock import MagicMock

import pytest

from arctis_sound_manager.gui import events_section as ev


@pytest.fixture()
def section(monkeypatch):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(ev.DbusWrapper, 'request_list_options', MagicMock())
    monkeypatch.setattr(ev.QEventsSection, '_read_autostart', staticmethod(lambda: True))
    monkeypatch.setattr(ev, '_t', lambda key, **kw: key + (str(sorted(kw.items())) if kw else ''))
    return ev.QEventsSection()


def _settings(general=None, device=None, config=None):
    g = {'redirect_audio_on_connect': True, 'redirect_audio_on_connect_channel': 0,
         'redirect_audio_on_disconnect': True, 'redirect_audio_on_disconnect_device': 'usb',
         'micro_autoswitch': 1, 'micro_alt_source': 'mic2', 'preset_sync_enabled': True}
    g.update(general or {})
    return {'general': g, 'device': device or {}, 'settings_config': config or {},
            'device_name': 'Arctis GameBuds'}


def _texts(row):
    return [(t, on) for t, on in row._state[2]]


def test_buds_wording_and_outcomes(section):
    section.update_status({'headset': {'left_bud_battery': {}}})
    section.update_settings(_settings(device={'pm_shutdown': 30, 'wear_sense': 1},
                                      config={'pm_shutdown': {'type': 'button_group', 'default_value': 30,
                                                              'values_mapping': {'30': '30_minutes'}},
                                              'wear_sense': {'type': 'toggle', 'default_value': 1,
                                                             'values': {'on': 1, 'off': 0},
                                                             'only_over': 'bluetooth'}}))
    rows = section._rows
    assert rows['connect']._state[0] == 'event_connect_title_buds'
    assert [on for _, on in _texts(rows['connect'])] == [True, True]
    assert _texts(rows['disconnect'])[1][0].startswith('event_mic_to')
    assert rows['idle']._state[0].startswith('event_idle_title_buds')
    assert _texts(rows['wear']) == [('event_wear_bt_only', False)]     # greyed: Bluetooth only
    assert rows['mute'].isHidden()                                      # no mic_status reported


def test_nothing_promised_that_will_not_happen(section):
    section.update_settings(_settings(general={'redirect_audio_on_disconnect_device': '',
                                               'micro_alt_source': ''},
                                      device={'pm_shutdown': 0},
                                      config={'pm_shutdown': {'type': 'button_group', 'default_value': 30,
                                                              'values_mapping': {0: 'never'}}}))
    rows = section._rows
    assert _texts(rows['disconnect']) == [('event_sound_no_device', False), ('event_mic_no_alt', False)]
    assert rows['idle']._state[0] == 'event_idle_title_never_headset'
    assert _texts(rows['idle']) == [('event_idle_stays_on_headset', False)]
    assert rows['wear'].isHidden()                                      # profile has no wear_sense


def test_mute_event_only_where_the_headset_reports_it(section):
    section.update_status({'headset': {'mic_status': {}}})
    section.update_settings(_settings(general={'micro_autoswitch': 2}))
    assert not section._rows['mute'].isHidden()
    assert _texts(section._rows['mute'])[0][1] is True
    assert _texts(section._rows['connect'])[1] == ('event_mic_unchanged', False)


def test_ids_are_shown_by_name_once_the_list_arrives(section):
    section.update_settings(_settings())
    assert "mic2" in _texts(section._rows['disconnect'])[1][0]
    section._on_options({'name': 'pulse_audio_sources', 'list': [{'id': 'mic2', 'name': 'Desk mic'}]})
    assert "Desk mic" in _texts(section._rows['disconnect'])[1][0]


def test_each_mic_switch_flips_only_its_own_bit(section, monkeypatch):
    from PySide6.QtCore import Qt
    sent = MagicMock()
    monkeypatch.setattr(ev.DbusWrapper, 'change_setting', sent)
    section.update_settings(_settings(general={'micro_autoswitch': 2}))   # mute only
    section._on_mic_toggled(ev._MIC_CONNECTION_BIT, Qt.CheckState.Checked)
    sent.assert_called_with('micro_autoswitch', 3)                       # mute + connection
    section._on_mic_toggled(ev._MIC_MUTE_BIT, Qt.CheckState.Unchecked)
    sent.assert_called_with('micro_autoswitch', 1)                       # connection only
    assert [t.toggle.isChecked() for t, _ in section._mic_toggles] == [True, True, False]
