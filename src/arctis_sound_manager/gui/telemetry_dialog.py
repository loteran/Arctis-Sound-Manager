# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
TelemetryConsentDialog — shown once at first launch.

Asks the user to share anonymous usage data:
  • Linux distribution
  • Headset model
  • ASM version

No personal data is collected. Consent can be changed later in Settings.
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

import arctis_sound_manager.gui.theme as _theme
from arctis_sound_manager.i18n import I18n

_APP_NAME = "Arctis Sound Manager"

def _btn_ss(bg: str, fg: str, border: str, hover: str) -> str:
    return (
        f"QPushButton {{ background-color: {bg}; color: {fg}; border: 1px solid {border}; "
        f"border-radius: 6px; padding: 8px 22px; font-size: 10pt; }}"
        f"QPushButton:hover {{ background-color: {hover}; }}"
    )


class TelemetryConsentDialog(QDialog):
    """
    Returns QDialog.Accepted  → user opted in
    Returns QDialog.Rejected  → user opted out
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{_APP_NAME} — Anonymous statistics")
        self.setMinimumSize(500, 300)
        self.setStyleSheet(
            f"background-color: {_theme.c('BG_MAIN')}; color: {_theme.c('TEXT_PRIMARY')};"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 22)
        layout.setSpacing(14)

        # Title
        title = QLabel(I18n.translate('ui', 'telemetry_title'))
        title.setStyleSheet(
            f"color: {_theme.c('TEXT_PRIMARY')}; font-size: 15pt; font-weight: bold; background: transparent;"
        )
        layout.addWidget(title)

        # Body
        body = QLabel(I18n.translate('ui', 'telemetry_body'))
        body.setStyleSheet(
            f"color: {_theme.c('TEXT_SECONDARY')}; font-size: 10pt; background: transparent;"
        )
        body.setWordWrap(True)
        body.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(body, stretch=1)

        # Note about changing preference
        note = QLabel(I18n.translate('ui', 'telemetry_note'))
        note.setStyleSheet(
            f"color: {_theme.c('TEXT_SECONDARY')}; font-size: 9pt; background: transparent;"
        )
        note.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(note)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        btn_row.addStretch()

        no_btn = QPushButton(I18n.translate('ui', 'no_thanks'))
        no_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        no_btn.setStyleSheet(
            _btn_ss(
                _theme.c('BG_BUTTON'), _theme.c('TEXT_PRIMARY'),
                _theme.c('BORDER'), _theme.c('BG_BUTTON_HOVER'),
            )
        )
        no_btn.clicked.connect(self.reject)
        btn_row.addWidget(no_btn)

        yes_btn = QPushButton(I18n.translate('ui', 'yes_share'))
        yes_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        yes_btn.setStyleSheet(
            _btn_ss(
                _theme.c('ACCENT'), "#ffffff",
                _theme.c('ACCENT'), _theme.c('BG_BUTTON_HOVER'),
            )
        )
        yes_btn.clicked.connect(self.accept)
        btn_row.addWidget(yes_btn)

        layout.addLayout(btn_row)
