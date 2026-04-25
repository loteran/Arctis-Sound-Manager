"""
UdevRulesDialog — shown at startup when udev rules are missing or invalid,
or at runtime when the daemon reports an EACCES on the USB device.
"""
import shutil
import subprocess
from typing import Literal

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

# Mode → (title, body, button label, asm-cli udev subcommand args)
_MODES: dict[str, tuple[str, str, str, list[str]]] = {
    "write": (
        "USB device permissions not configured",
        "The udev rules required to access your Arctis headset without root "
        "privileges are missing or incomplete.\n\n"
        "Without them, the daemon cannot open the USB device "
        "(Error 13: Access denied).\n\n"
        "Click \"Install rules\" to fix this automatically "
        "(requires administrator password).",
        "Install rules",
        ["udev", "write-rules", "--force", "--reload"],
    ),
    "reload": (
        "USB device permissions not applied",
        "The udev rules are installed but the currently-attached Arctis "
        "device was plugged in before they took effect.\n\n"
        "The daemon cannot open the USB device "
        "(Error 13: Access denied).\n\n"
        "Click \"Apply now\" to reload the rules and trigger them on the "
        "current device (requires administrator password). Alternatively, "
        "you can simply unplug and replug the dongle.",
        "Apply now",
        ["udev", "reload-rules"],
    ),
}


class UdevRulesDialog(QDialog):
    def __init__(self, parent=None, mode: Literal["write", "reload"] = "write"):
        super().__init__(parent)
        title, body, btn_label, self._cli_args = _MODES[mode]

        self.setWindowTitle("USB permissions — Arctis Sound Manager")
        self.setMinimumSize(540, 300)
        self.setStyleSheet(f"background-color: {BG_MAIN}; color: {TEXT_PRIMARY};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 20)
        layout.setSpacing(14)

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-size: 15pt; font-weight: bold; background: transparent;"
        )
        layout.addWidget(title_lbl)

        sub_lbl = QLabel(body)
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

        self._action_btn = QPushButton(btn_label)
        self._action_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._action_btn.setStyleSheet(_BTN.format(bg=ACCENT, fg="#ffffff", hover=BG_BUTTON_HOVER))
        self._action_btn.clicked.connect(self._run)
        btn_row.addWidget(self._action_btn)

        layout.addLayout(btn_row)

    def _run(self) -> None:
        cli = shutil.which('asm-cli')
        if not cli:
            self._status_lbl.setText(
                "asm-cli not found in PATH. Run manually: "
                f"asm-cli {' '.join(self._cli_args)}"
            )
            return

        original_label = self._action_btn.text()
        self._action_btn.setEnabled(False)
        self._action_btn.setText("Working…")

        try:
            subprocess.run([cli, *self._cli_args], check=True)
            self.accept()
        except subprocess.CalledProcessError as e:
            self._status_lbl.setText(
                f"Failed (code {e.returncode}). Try: sudo asm-cli "
                f"{' '.join(self._cli_args)}"
            )
            self._action_btn.setEnabled(True)
            self._action_btn.setText(original_label)
