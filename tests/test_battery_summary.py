from arctis_sound_manager.battery_summary import buds_text, case_percent
from arctis_sound_manager.gui.systray_app import QSystrayApp

PCT = 'percentage'


def _status(left, right, case=None):
    s = {'headset': {'headset_power_status': {'value': 'on', 'type': 'label'},
                     'headset_battery_charge': {'value': 95, 'type': PCT},
                     'left_bud_battery': left, 'right_bud_battery': right}}
    if case is not None:
        s['charging_case'] = {'case_battery_charge': {'value': case, 'type': PCT}}
    return s


def test_both_buds():
    s = _status({'value': 95, 'type': PCT}, {'value': 100, 'type': PCT})
    assert buds_text(s) == 'L 95% · R 100%'
    assert case_percent(s) is None


def test_offline_bud_is_a_word_not_a_number():
    s = _status({'value': 'offline', 'type': 'label'}, {'value': 80, 'type': PCT})
    assert buds_text(s) == 'L Offline · R 80%'


def test_headset_without_buds_has_no_breakdown():
    assert buds_text({'headset': {'headset_battery_charge': {'value': 50, 'type': PCT}}}) is None
    assert buds_text({}) is None


def test_tray_tooltip_lists_buds_and_case(monkeypatch):
    monkeypatch.setattr('arctis_sound_manager.gui.systray_app._show_battery_in_tray', lambda: True)
    s = _status({'value': 95, 'type': PCT}, {'value': 100, 'type': PCT}, case=42)
    assert QSystrayApp._tooltip_text(s, 95) == 'Arctis — L 95% · R 100% · Case 42%'
    assert QSystrayApp._tooltip_text({}, None) == 'Arctis Sound Manager'
