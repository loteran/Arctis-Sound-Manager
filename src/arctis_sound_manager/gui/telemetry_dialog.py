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

from arctis_sound_manager.gui.theme import (
    ACCENT,
    BG_BUTTON,
    BG_BUTTON_HOVER,
    BG_CARD,
    BG_MAIN,
    BORDER,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)

_BTN = (
    "QPushButton {{ background-color: {bg}; color: {fg}; border: 1px solid {border}; "
    "border-radius: 6px; padding: 8px 22px; font-size: 10pt; }}"
    "QPushButton:hover {{ background-color: {hover}; }}"
)


class TelemetryConsentDialog(QDialog):
    """
    Returns QDialog.Accepted  → user opted in
    Returns QDialog.Rejected  → user opted out
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Arctis Sound Manager — Anonymous statistics")
        self.setMinimumSize(500, 300)
        self.setStyleSheet(f"background-color: {BG_MAIN}; color: {TEXT_PRIMARY};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 22)
        layout.setSpacing(14)

        # Title
        title = QLabel("Help improve ASM")
        title.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-size: 15pt; font-weight: bold; background: transparent;"
        )
        layout.addWidget(title)

        # Body
        body = QLabel(
            "Would you like to share anonymous usage data to help improve "
            "Arctis Sound Manager?\n\n"
            "The following information would be sent <b>once per day</b>:\n"
            "  • Your Linux distribution  (e.g. Arch Linux, Ubuntu 24.04)\n"
            "  • Your headset model  (e.g. Arctis Nova Pro Wired)\n"
            "  • The ASM version you are running\n\n"
            "<b>No personal data</b>, no IP address, no unique identifier."
        )
        body.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 10pt; background: transparent;"
        )
        body.setWordWrap(True)
        body.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(body, stretch=1)

        # Note about changing preference
        note = QLabel("You can change this at any time in <b>Settings → Telemetry</b>.")
        note.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 9pt; background: transparent;"
        )
        note.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(note)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        btn_row.addStretch()

        no_btn = QPushButton("No thanks")
        no_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        no_btn.setStyleSheet(
            _BTN.format(bg=BG_BUTTON, fg=TEXT_PRIMARY, border=BORDER, hover=BG_BUTTON_HOVER)
        )
        no_btn.clicked.connect(self.reject)
        btn_row.addWidget(no_btn)

        yes_btn = QPushButton("Yes, share anonymously")
        yes_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        yes_btn.setStyleSheet(
            _BTN.format(bg=ACCENT, fg="#ffffff", border=ACCENT, hover=BG_BUTTON_HOVER)
        )
        yes_btn.clicked.connect(self.accept)
        btn_row.addWidget(yes_btn)

        layout.addLayout(btn_row)
