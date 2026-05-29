# Copyright (C) 2022 Giacomo Furlan (elegos) — original work
# Copyright (C) 2026 loteran — modifications
# SPDX-License-Identifier: GPL-3.0-or-later

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget


class QMainAppProtoWidget(QWidget):
    visibilityChanged = Signal(bool)

    def hideEvent(self, event):
        super().hideEvent(event)

        self.visibilityChanged.emit(False)

    def showEvent(self, event):
        super().showEvent(event)

        self.visibilityChanged.emit(True)
