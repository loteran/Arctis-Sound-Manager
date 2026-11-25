"""A switch created with a value shows it; only a change you can see slides."""
from PySide6.QtCore import QAbstractAnimation
from PySide6.QtWidgets import QApplication, QWidget

from arctis_sound_manager.gui.qt_widgets.q_dual_state import QDualState
from arctis_sound_manager.gui.qt_widgets.q_toggle import QToggle


def _app():
    return QApplication.instance() or QApplication([])


def test_created_on_does_not_slide_over_from_off():
    _app()
    w = QDualState(off_text='Off', on_text='On', init_state='right')
    t = w.toggle
    assert t.animation.state() != QAbstractAnimation.State.Running
    assert t.circle_position == t._rest_position(True)


def test_a_change_on_a_hidden_page_snaps():
    _app()
    t = QToggle()
    t.setChecked(True)
    assert t.animation.state() != QAbstractAnimation.State.Running
    assert t.circle_position == t._rest_position(True)


def test_a_visible_change_still_slides():
    app = _app()
    host = QWidget(); t = QToggle(parent=host); host.show(); app.processEvents()
    t.setChecked(True)
    assert t.animation.state() == QAbstractAnimation.State.Running
    host.hide()
