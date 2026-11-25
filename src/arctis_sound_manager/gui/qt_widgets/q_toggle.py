# Copyright (C) 2022 Giacomo Furlan (elegos) — original work
# Copyright (C) 2026 loteran — modifications
# SPDX-License-Identifier: GPL-3.0-or-later

from PySide6.QtCore import (Property, QAbstractAnimation, QEasingCurve, QPoint,
                            QPropertyAnimation, QRect, Qt)
from PySide6.QtGui import QColor, QPainter, QPaintEvent
from PySide6.QtWidgets import QCheckBox, QWidget

import arctis_sound_manager.gui.theme as _theme

LEFT_MARGIN = 3

class QToggle(QCheckBox):
    def __init__(self, parent: QWidget|None = None, width: int = 60, is_checkbox: bool = False):
        '''
        Args:
            parent: The parent widget
            width: The width of the toggle
            is_checkbox: If the toggle is a checkbox, then it will use an accent color when enabled
        '''
        super().__init__(parent)

        self.setFixedSize(width, 28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.is_checkbox = is_checkbox

        self._circle_position = LEFT_MARGIN
        self.animation = QPropertyAnimation(self, b'circle_position', self)
        self.animation.setEasingCurve(QEasingCurve.Type.OutExpo)
        self.animation.setDuration(500)

        self.checkStateChanged.connect(self.start_animation_transition)

    def start_animation_transition(self, value: bool):
        self.animation.stop()
        if not self.isVisible():
            # Being created with its value, or updated on a page not on
            # screen: nothing to watch, so no slide. A switch made "on" used to
            # slide over from "off" the first time its page was looked at.
            self._circle_position = self._rest_position(value == Qt.CheckState.Checked)
            self.update()
            return

        self.animation.setEndValue(self._rest_position(value == Qt.CheckState.Checked))

        self.animation.start()

    @Property(float)
    def circle_position(self) -> float:
        return self._circle_position
    
    @circle_position.setter
    def circle_position(self, value: float):
        self._circle_position = value
        self.update()

    
    def _rest_position(self, checked: bool) -> float:
        return self.width() - LEFT_MARGIN - 22 if checked else LEFT_MARGIN

    def hitButton(self, pos: QPoint) -> bool:
        return self.contentsRect().contains(pos)

    def paintEvent(self, event: QPaintEvent):
        # setChecked() under blockSignals() never starts the animation: snap
        # the knob to the real state instead of leaving it on the wrong side
        if self.animation.state() != QAbstractAnimation.State.Running:
            self._circle_position = self._rest_position(self.isChecked())

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        
        box = QRect(0, 0, self.width(), self.height())

        # Background: theme colours, read at paint time so a theme switch or the
        # editor's live preview recolours the switch on its next repaint
        on = self.is_checkbox and self.isChecked()
        painter.setBrush(QColor(_theme.c("TOGGLE_ON" if on else "TOGGLE_OFF")))
        painter.drawRoundedRect(0, 0, self.width(), self.height(), self.height() / 2, self.height() / 2)

        # Status circle
        painter.setBrush(self.palette().buttonText())
        painter.drawEllipse(self._circle_position, 3, 22, 22)

        painter.end()
