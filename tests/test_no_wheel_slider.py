"""Sliders leave the wheel to the page until clicked (earbud volume went to 0)."""
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication

from arctis_sound_manager.gui.qt_widgets.no_wheel_slider import NoWheelSlider


def _wheel(widget, notches=-1):
    event = QWheelEvent(QPointF(5, 5), QPointF(widget.mapToGlobal(QPoint(5, 5))), QPoint(0, 0),
                        QPoint(0, 120 * notches), Qt.MouseButton.NoButton,
                        Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(widget, event)
    return event


def test_wheel_passes_by_until_clicked():
    QApplication.instance() or QApplication([])
    slider = NoWheelSlider(Qt.Orientation.Horizontal)
    slider.setRange(0, 15)
    slider.setValue(10)
    slider.show()
    event = _wheel(slider)
    assert slider.value() == 10 and not event.isAccepted()     # the page gets it
    assert slider.focusPolicy() == Qt.FocusPolicy.StrongFocus  # the wheel gives no focus

    slider.setFocus()
    QApplication.processEvents()
    if slider.hasFocus():                                      # offscreen may refuse focus
        _wheel(slider)
        assert slider.value() < 10
