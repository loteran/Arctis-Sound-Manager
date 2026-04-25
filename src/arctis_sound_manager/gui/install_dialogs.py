"""
install_dialogs.py — Shared dialogs for install-method conflicts and post-update setup.

Used by `home_page.py` and `device_page.py` (both surface "Update available" CTAs).
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QClipboard
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
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
from arctis_sound_manager.update_checker import InstallMethod

_BTN = (
    "QPushButton {{ background-color: {bg}; color: {fg}; border: none; "
    "border-radius: 6px; padding: 8px 18px; font-size: 10pt; }}"
    "QPushButton:hover {{ background-color: {hover}; }}"
)

_METHOD_LABELS = {
    InstallMethod.RPM:    "RPM (dnf / Fedora / Nobara / COPR)",
    InstallMethod.PACMAN: "Pacman (Arch / CachyOS / AUR)",
    InstallMethod.APT:    "APT (Debian / Ubuntu / Mint / PPA)",
    InstallMethod.PIPX:   "pipx (~/.local/bin)",
    InstallMethod.PIP:    "pip --user",
}

_CLEAN_REINSTALL_URL = (
    "https://raw.githubusercontent.com/loteran/Arctis-Sound-Manager/main/"
    "scripts/clean-reinstall.sh"
)
_CLEAN_REINSTALL_CMD = f"curl -fsSL {_CLEAN_REINSTALL_URL} | bash"


def show_multi_install_warning(parent: QWidget | None, methods: list[InstallMethod]) -> None:
    """Block the update flow with a clear dialog when ASM is installed by more than one method."""
    dlg = QDialog(parent)
    dlg.setWindowTitle("Multiple ASM installations detected")
    dlg.setMinimumWidth(560)
    dlg.setStyleSheet(f"background-color: {BG_MAIN}; color: {TEXT_PRIMARY};")

    layout = QVBoxLayout(dlg)
    layout.setContentsMargins(24, 22, 24, 18)
    layout.setSpacing(12)

    title = QLabel("Multiple installations of Arctis Sound Manager were found")
    title.setStyleSheet(
        f"color: {TEXT_PRIMARY}; font-size: 13pt; font-weight: bold; background: transparent;"
    )
    title.setWordWrap(True)
    layout.addWidget(title)

    body = QLabel(
        "Updating now would install on top of one of these without removing the others, "
        "which is the cause of \"no device detected\", wrong-version daemons running in the "
        "background and stale audio routing.\n\n"
        "Detected install methods:"
    )
    body.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 10pt; background: transparent;")
    body.setWordWrap(True)
    layout.addWidget(body)

    methods_text = "\n".join(f"  • {_METHOD_LABELS.get(m, m.name)}" for m in methods)
    methods_lbl = QLabel(methods_text)
    methods_lbl.setStyleSheet(
        f"background-color: {BG_CARD}; color: {TEXT_PRIMARY}; "
        f"font-family: monospace; font-size: 10pt; padding: 10px; "
        f"border-radius: 6px; border: 1px solid {BORDER};"
    )
    layout.addWidget(methods_lbl)

    fix_intro = QLabel(
        "Run the clean-reinstall script to remove every copy and install one "
        "fresh version via your preferred method:"
    )
    fix_intro.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 10pt; background: transparent;")
    fix_intro.setWordWrap(True)
    layout.addWidget(fix_intro)

    cmd_lbl = QLabel(_CLEAN_REINSTALL_CMD)
    cmd_lbl.setStyleSheet(
        f"background-color: {BG_CARD}; color: {TEXT_PRIMARY}; "
        f"font-family: monospace; font-size: 10pt; padding: 10px; "
        f"border-radius: 6px; border: 1px solid {BORDER};"
    )
    cmd_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    cmd_lbl.setWordWrap(True)
    layout.addWidget(cmd_lbl)

    btn_row = QHBoxLayout()
    btn_row.addStretch()

    copy_btn = QPushButton("Copy command")
    copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    copy_btn.setStyleSheet(_BTN.format(bg=ACCENT, fg="#ffffff", hover=BG_BUTTON_HOVER))
    def _copy():
        QApplication.clipboard().setText(_CLEAN_REINSTALL_CMD, QClipboard.Mode.Clipboard)
        copy_btn.setText("Copied!")
        copy_btn.setEnabled(False)
    copy_btn.clicked.connect(_copy)
    btn_row.addWidget(copy_btn)

    close_btn = QPushButton("Close")
    close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    close_btn.setStyleSheet(_BTN.format(bg=BG_BUTTON, fg=TEXT_PRIMARY, hover=BG_BUTTON_HOVER))
    close_btn.clicked.connect(dlg.accept)
    btn_row.addWidget(close_btn)

    layout.addLayout(btn_row)
    dlg.exec()
