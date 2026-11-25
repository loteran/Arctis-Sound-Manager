# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QSlider


class NoWheelSlider(QSlider):
    """QSlider that leaves the wheel to the page until it is clicked.

    A stock slider takes the wheel whenever the pointer passes over it, three
    steps a notch: scrolling the Devices page with the pointer crossing the
    earbuds' 2.4 GHz volume moved it 10 → 7 → 4 → 1 → 0, sent each step to the
    earbuds, and saved 0 — silence on the next connection, with nothing on
    screen to say why. Bluetooth volume went the same way earlier. The page
    now scrolls instead; once the slider has been clicked (it has focus) the
    wheel adjusts it as before. Strong focus, not wheel focus: the wheel
    itself must not hand it the focus.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()
