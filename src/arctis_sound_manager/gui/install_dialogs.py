# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

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

import arctis_sound_manager.gui.theme as _theme
from arctis_sound_manager.i18n import I18n
from arctis_sound_manager.update_checker import InstallMethod

_APP_NAME = "Arctis Sound Manager"

def _btn_ss(bg: str, fg: str, hover: str) -> str:
    return (
        f"QPushButton {{ background-color: {bg}; color: {fg}; border: none; "
        f"border-radius: 6px; padding: 8px 18px; font-size: 10pt; }}"
        f"QPushButton:hover {{ background-color: {hover}; }}"
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
    dlg.setWindowTitle("Multiple ASM installations detected")  # brand name stays fixed
    dlg.setMinimumWidth(560)
    dlg.setStyleSheet(
        f"background-color: {_theme.c('BG_MAIN')}; color: {_theme.c('TEXT_PRIMARY')};"
    )

    layout = QVBoxLayout(dlg)
    layout.setContentsMargins(24, 22, 24, 18)
    layout.setSpacing(12)

    title = QLabel(I18n.translate('ui', 'multi_install_title'))
    title.setStyleSheet(
        f"color: {_theme.c('TEXT_PRIMARY')}; font-size: 13pt; font-weight: bold; background: transparent;"
    )
    title.setWordWrap(True)
    layout.addWidget(title)

    body = QLabel(I18n.translate('ui', 'multi_install_body'))
    body.setStyleSheet(
        f"color: {_theme.c('TEXT_SECONDARY')}; font-size: 10pt; background: transparent;"
    )
    body.setWordWrap(True)
    layout.addWidget(body)

    detected_lbl = QLabel(I18n.translate('ui', 'detected_install_methods'))
    detected_lbl.setStyleSheet(
        f"color: {_theme.c('TEXT_SECONDARY')}; font-size: 10pt; background: transparent;"
    )
    layout.addWidget(detected_lbl)

    methods_text = "\n".join(f"  • {_METHOD_LABELS.get(m, m.name)}" for m in methods)
    methods_lbl = QLabel(methods_text)
    methods_lbl.setStyleSheet(
        f"background-color: {_theme.c('BG_CARD')}; color: {_theme.c('TEXT_PRIMARY')}; "
        f"font-family: monospace; font-size: 10pt; padding: 10px; "
        f"border-radius: 6px; border: 1px solid {_theme.c('BORDER')};"
    )
    layout.addWidget(methods_lbl)

    fix_intro = QLabel(I18n.translate('ui', 'multi_install_fix_intro'))
    fix_intro.setStyleSheet(
        f"color: {_theme.c('TEXT_SECONDARY')}; font-size: 10pt; background: transparent;"
    )
    fix_intro.setWordWrap(True)
    layout.addWidget(fix_intro)

    cmd_lbl = QLabel(_CLEAN_REINSTALL_CMD)
    cmd_lbl.setStyleSheet(
        f"background-color: {_theme.c('BG_CARD')}; color: {_theme.c('TEXT_PRIMARY')}; "
        f"font-family: monospace; font-size: 10pt; padding: 10px; "
        f"border-radius: 6px; border: 1px solid {_theme.c('BORDER')};"
    )
    cmd_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    cmd_lbl.setWordWrap(True)
    layout.addWidget(cmd_lbl)

    btn_row = QHBoxLayout()
    btn_row.addStretch()

    copy_btn = QPushButton(I18n.translate('ui', 'copy_command'))
    copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    copy_btn.setStyleSheet(
        _btn_ss(_theme.c('ACCENT'), "#ffffff", _theme.c('BG_BUTTON_HOVER'))
    )
    def _copy():
        QApplication.clipboard().setText(_CLEAN_REINSTALL_CMD, QClipboard.Mode.Clipboard)
        copy_btn.setText(I18n.translate('ui', 'copied'))
        copy_btn.setEnabled(False)
    copy_btn.clicked.connect(_copy)
    btn_row.addWidget(copy_btn)

    close_btn = QPushButton(I18n.translate('ui', 'close'))
    close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    close_btn.setStyleSheet(
        _btn_ss(_theme.c('BG_BUTTON'), _theme.c('TEXT_PRIMARY'), _theme.c('BG_BUTTON_HOVER'))
    )
    close_btn.clicked.connect(dlg.accept)
    btn_row.addWidget(close_btn)

    layout.addLayout(btn_row)
    dlg.exec()
