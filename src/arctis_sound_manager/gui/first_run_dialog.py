# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
FirstRunDialog — runs `asm-setup` automatically on first GUI launch.

Triggered when ``~/.config/arctis_manager/.setup_done`` is missing, which is
the case for pipx installs that didn't go through a distro-package post-install
hook.  Runs asm-setup as a subprocess and streams its output into a read-only
text area so the user sees what's happening (HRIR download, services, udev
rules…). The udev step prompts for the admin password via pkexec/sudo.
"""
from __future__ import annotations

import shutil

from PySide6.QtCore import Qt, QProcess
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

import arctis_sound_manager.gui.theme as _theme
from arctis_sound_manager.i18n import I18n

_APP_NAME = "Arctis Sound Manager"

def _btn_ss(bg: str, fg: str, hover: str) -> str:
    return (
        f"QPushButton {{ background-color: {bg}; color: {fg}; border: none; "
        f"border-radius: 6px; padding: 8px 18px; font-size: 10pt; }}"
        f"QPushButton:hover {{ background-color: {hover}; }}"
        f"QPushButton:disabled {{ background-color: {bg}; color: #888; }}"
    )


class FirstRunDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"First-time setup — {_APP_NAME}")
        self.setMinimumSize(620, 440)
        self.setStyleSheet(
            f"background-color: {_theme.c('BG_MAIN')}; color: {_theme.c('TEXT_PRIMARY')};"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 20)
        layout.setSpacing(12)

        title_lbl = QLabel(I18n.translate('ui', 'first_run_welcome'))
        title_lbl.setStyleSheet(
            f"color: {_theme.c('TEXT_PRIMARY')}; font-size: 15pt; font-weight: bold; background: transparent;"
        )
        layout.addWidget(title_lbl)

        sub_lbl = QLabel(I18n.translate('ui', 'first_run_desc'))
        sub_lbl.setStyleSheet(
            f"color: {_theme.c('TEXT_SECONDARY')}; font-size: 10pt; background: transparent;"
        )
        sub_lbl.setWordWrap(True)
        layout.addWidget(sub_lbl)

        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setStyleSheet(
            f"QPlainTextEdit {{ background-color: #0f1216; color: {_theme.c('TEXT_SECONDARY')}; "
            f"border: 1px solid {_theme.c('BORDER')}; border-radius: 6px; padding: 8px; "
            f"font-family: 'JetBrains Mono', 'DejaVu Sans Mono', monospace; "
            f"font-size: 9pt; }}"
        )
        self._log.setVisible(False)
        layout.addWidget(self._log, stretch=1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        btn_row.addStretch()

        self._skip_btn = QPushButton(I18n.translate('ui', 'skip'))
        self._skip_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._skip_btn.setStyleSheet(
            _btn_ss(_theme.c('BG_BUTTON'), _theme.c('TEXT_PRIMARY'), _theme.c('BG_BUTTON_HOVER'))
        )
        self._skip_btn.clicked.connect(self.reject)
        btn_row.addWidget(self._skip_btn)

        self._action_btn = QPushButton(I18n.translate('ui', 'run_setup'))
        self._action_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._action_btn.setStyleSheet(
            _btn_ss(_theme.c('ACCENT'), "#ffffff", _theme.c('BG_BUTTON_HOVER'))
        )
        self._action_btn.clicked.connect(self._start)
        btn_row.addWidget(self._action_btn)

        layout.addLayout(btn_row)

        self._proc: QProcess | None = None

    def _start(self) -> None:
        cli = shutil.which('asm-setup')
        if not cli:
            self._log.setVisible(True)
            self._log.appendPlainText(
                "[!] asm-setup not found in PATH.\n"
                "    Run manually:  asm-setup"
            )
            return

        self._action_btn.setEnabled(False)
        self._action_btn.setText(I18n.translate('ui', 'running'))
        self._skip_btn.setEnabled(False)
        self._log.setVisible(True)
        self.adjustSize()

        self._proc = QProcess(self)
        self._proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self._proc.readyReadStandardOutput.connect(self._on_stdout)
        self._proc.finished.connect(self._on_finished)
        self._proc.errorOccurred.connect(self._on_error)
        self._proc.start(cli, [])

    def _on_stdout(self) -> None:
        if self._proc is None:
            return
        data = bytes(self._proc.readAllStandardOutput()).decode(errors="replace")
        self._log.moveCursor(self._log.textCursor().MoveOperation.End)
        self._log.insertPlainText(data)
        self._log.moveCursor(self._log.textCursor().MoveOperation.End)

    def _on_finished(self, exit_code: int, _exit_status) -> None:
        self._skip_btn.setEnabled(True)
        if exit_code == 0:
            self._action_btn.setText(I18n.translate('ui', 'close'))
            self._action_btn.setEnabled(True)
            self._action_btn.clicked.disconnect()
            self._action_btn.clicked.connect(self.accept)
            self._log.appendPlainText("\n[ok] Setup complete. You can close this window.")
        else:
            self._action_btn.setText(f"Retry (exit {exit_code})")
            self._action_btn.setEnabled(True)
            self._action_btn.clicked.disconnect()
            self._action_btn.clicked.connect(self._start)
            self._log.appendPlainText(
                f"\n[!] asm-setup exited with code {exit_code}. "
                "You can retry, or close this dialog and run `asm-setup` "
                "manually from a terminal."
            )

    def _on_error(self, _err) -> None:
        self._log.appendPlainText("\n[!] Could not launch asm-setup process.")
        self._action_btn.setText("Close")
        self._action_btn.setEnabled(True)
        self._action_btn.clicked.disconnect()
        self._action_btn.clicked.connect(self.reject)
