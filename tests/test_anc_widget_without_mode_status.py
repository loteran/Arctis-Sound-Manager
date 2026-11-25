"""GameBuds: the status never reports the ANC mode. The widget used to read
that as "off", snap back to Off after every change, and then drop a click on
Off as "already active" — so Off could never be applied."""
from unittest.mock import MagicMock

import pytest

from arctis_sound_manager.gui import anc_widget

GAMEBUDS_STATUS = {'headset': {'headset_power_status': {'value': 'on', 'type': 'label'}}}


@pytest.fixture()
def widget(monkeypatch):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    sent = MagicMock()
    monkeypatch.setattr(anc_widget.DbusWrapper, 'change_setting', sent)
    w = anc_widget.QAncWidget()
    w.sent = sent
    return w


def test_off_is_sent_after_transparent(widget):
    widget.apply_saved_mode(0)
    widget.update_status(GAMEBUDS_STATUS)
    widget._on_mode_clicked('transparent', 1)
    widget.update_status(GAMEBUDS_STATUS)           # no mode in it: keep Transparent
    assert widget._current_mode == 'transparent'
    widget._on_mode_clicked('off', 0)
    assert widget.sent.call_args_list[-1].args == ('noise_cancelling', 0)


def test_saved_mode_shown_when_settings_arrive_first(widget):
    widget.apply_saved_mode(2)                       # before any status
    widget.update_status(GAMEBUDS_STATUS)
    assert widget._current_mode == 'on'


def test_a_status_that_reports_the_mode_still_wins(widget):
    widget.update_status({'headset': {'noise_cancelling': {'value': 'transparent', 'type': 'label'}}})
    widget.apply_saved_mode(0)
    assert widget._current_mode == 'transparent'
