# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

from PySide6.QtCore import QObject, Slot


class QBaseDesktopApp(QObject):
    @Slot()
    def sig_stop(self):
        raise NotImplementedError("This method should be implemented by subclasses")

    def start(self):
        raise NotImplementedError("This method should be implemented by subclasses")
    