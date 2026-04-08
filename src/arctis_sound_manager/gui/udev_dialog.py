"""
UdevRulesDialog — shown at startup when udev rules are missing or invalid.
"""
import shutil
import subprocess

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
    BG_MAIN,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)

_BTN = (
    "QPushButton {{ background-color: {bg}; color: {fg}; border: none; "
    "border-radius: 6px; padding: 8px 18px; font-size: 10pt; }}"
    "QPushButton:hover {{ background-color: {hover}; }}"
)


class UdevRulesDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("USB permissions — Arctis Sound Manager")
        self.setMinimumSize(520, 280)
        self.setStyleSheet(f"background-color: {BG_MAIN}; color: {TEXT_PRIMARY};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 20)
        layout.setSpacing(14)

        title_lbl = QLabel("USB device permissions not configured")
        title_lbl.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-size: 15pt; font-weight: bold; background: transparent;"
        )
        layout.addWidget(title_lbl)

        sub_lbl = QLabel(
            "The udev rules required to access your Arctis headset without root "
            "privileges are missing or incomplete.\n\n"
            "Without them, the daemon cannot open the USB device "
            "(Error 13: Access denied).\n\n"
            "Click \"Install rules\" to fix this automatically "
            "(requires administrator password)."
        )
        sub_lbl.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 10pt; background: transparent;"
        )
        sub_lbl.setWordWrap(True)
        layout.addWidget(sub_lbl, stretch=1)

        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet(
            f"color: {ACCENT}; font-size: 9pt; background: transparent;"
        )
        self._status_lbl.setWordWrap(True)
        layout.addWidget(self._status_lbl)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        btn_row.addStretch()

        ignore_btn = QPushButton("Ignore")
        ignore_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        ignore_btn.setStyleSheet(_BTN.format(bg=BG_BUTTON, fg=TEXT_PRIMARY, hover=BG_BUTTON_HOVER))
        ignore_btn.clicked.connect(self.reject)
        btn_row.addWidget(ignore_btn)

        self._install_btn = QPushButton("Install rules")
        self._install_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._install_btn.setStyleSheet(_BTN.format(bg=ACCENT, fg="#ffffff", hover=BG_BUTTON_HOVER))
        self._install_btn.clicked.connect(self._install)
        btn_row.addWidget(self._install_btn)

        layout.addLayout(btn_row)

    def _install(self) -> None:
        cli = shutil.which('asm-cli')
        if not cli:
            self._status_lbl.setText("asm-cli not found. Run: asm-cli udev write-rules --force --reload")
            return

        self._install_btn.setEnabled(False)
        self._install_btn.setText("Installing...")

        try:
            subprocess.run(
                [cli, 'udev', 'write-rules', '--force', '--reload'],
                check=True,
            )
            self.accept()
        except subprocess.CalledProcessError as e:
            self._status_lbl.setText(f"Installation failed (code {e.returncode}). Try: sudo asm-cli udev write-rules --force --reload")
            self._install_btn.setEnabled(True)
            self._install_btn.setText("Install rules")
