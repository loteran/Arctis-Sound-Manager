from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import Qt


@pytest.fixture()
def widget(monkeypatch):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from arctis_sound_manager.gui import output_balance_widget as m
    sent = MagicMock()
    monkeypatch.setattr(m.DbusWrapper, 'change_setting', sent)
    w = m.QOutputBalanceWidget()
    w.sent = sent
    return w


def test_sliders_hidden_until_the_option_is_on_and_default_to_100(widget):
    widget.update_from_settings({})
    assert widget._sliders_box.isHidden()
    assert all(row[1].value() == 100 for row in widget._rows.values())
    widget.update_from_settings({'output_balance_enabled': True,
                                 'output_volume_left': 55, 'output_volume_right': 100})
    assert not widget._sliders_box.isHidden()
    assert widget._rows['left'][1].value() == 55 and widget._rows['left'][2].text() == '55%'


def test_toggle_and_slider_send_settings(widget):
    widget._on_toggled(Qt.CheckState.Checked)
    widget.sent.assert_called_with('output_balance_enabled', True)
    widget._rows['right'][1].setValue(70)
    widget._send_pending()                     # what the debounce timer does
    widget.sent.assert_called_with('output_volume_right', 70)
