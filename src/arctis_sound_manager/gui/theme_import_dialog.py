# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
theme_import_dialog.py — Dialog for importing themes via deep link or file.

Accepts:
  - arctis-asm://import-theme?data=<base64>   (ASM self-contained theme link)
  - a local theme .ini file (same format as theme.export_theme_to_file)
"""
from __future__ import annotations

import webbrowser
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from arctis_sound_manager.gui.theme import import_theme_from_file, save_user_theme
from arctis_sound_manager.gui.theme_share import (
    ThemeImportError,
    decode_theme_link,
    is_theme_link,
)
import arctis_sound_manager.gui.theme as _theme
from arctis_sound_manager.i18n import I18n


def _t(key: str) -> str:
    return I18n.translate("ui", key)


# ── Import dialog ─────────────────────────────────────────────────────────────

class ThemeImportDialog(QDialog):
    """Dialog that accepts an ASM theme deep link (or .ini file) and saves it."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)

        self.imported_theme_id: str | None = None

        self.setWindowTitle(_t("theme_import_title"))
        self.setMinimumWidth(480)
        self.setStyleSheet(
            f"background-color: {_theme.c('BG_MAIN')}; color: {_theme.c('TEXT_PRIMARY')};"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        layout.addWidget(QLabel(
            _t("theme_import_label"),
            styleSheet=f"color: {_theme.c('TEXT_PRIMARY')}; font-size: 11pt; background: transparent;"
        ))

        self._url_edit = QLineEdit()
        self._url_edit.setPlaceholderText(_t("theme_import_url_placeholder"))
        self._url_edit.setStyleSheet(
            f"QLineEdit {{ background: {_theme.c('BG_BUTTON')}; border: 1px solid {_theme.c('BORDER')}; "
            f"border-radius: 6px; color: {_theme.c('TEXT_PRIMARY')}; padding: 6px 10px; font-size: 10pt; }}"
            f"QLineEdit:focus {{ border-color: {_theme.c('ACCENT')}; }}"
        )
        layout.addWidget(self._url_edit)

        self._browse_file_btn = QPushButton(_t("theme_import_from_file"))
        self._browse_file_btn.setStyleSheet(
            f"QPushButton {{ background: {_theme.c('BG_BUTTON')}; border: 1px solid {_theme.c('BORDER')}; "
            f"border-radius: 6px; color: {_theme.c('TEXT_PRIMARY')}; padding: 6px 14px; font-size: 10pt; }}"
            f"QPushButton:hover {{ background: {_theme.c('ACCENT')}33; }}"
        )
        self._browse_file_btn.clicked.connect(self._on_browse_file)
        layout.addWidget(self._browse_file_btn)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setStyleSheet(
            f"color: {_theme.c('TEXT_SECONDARY')}; font-size: 10pt; background: transparent;"
        )
        layout.addWidget(self._status)

        self._import_btn = QPushButton(_t("theme_import"))
        self._import_btn.setStyleSheet(f"""
            QPushButton {{
                background: {_theme.c('ACCENT')};
                border: none;
                border-radius: 6px;
                color: #fff;
                padding: 8px 20px;
                font-size: 11pt;
            }}
            QPushButton:hover {{ background: {_theme.c('ACCENT')}cc; }}
            QPushButton:disabled {{ background: {_theme.c('BG_BUTTON')}; color: {_theme.c('TEXT_SECONDARY')}; }}
        """)
        self._import_btn.clicked.connect(self._on_import)
        layout.addWidget(self._import_btn)

        self._browse_community_btn = QPushButton(_t("theme_browse_community"))
        self._browse_community_btn.setStyleSheet(
            f"QPushButton {{ background: {_theme.c('BG_BUTTON')}; border: 1px solid {_theme.c('BORDER')}; "
            f"border-radius: 6px; color: {_theme.c('TEXT_PRIMARY')}; padding: 6px 14px; font-size: 10pt; }}"
            f"QPushButton:hover {{ background: {_theme.c('ACCENT')}33; }}"
        )
        self._browse_community_btn.clicked.connect(self._on_browse_community)
        layout.addWidget(self._browse_community_btn)

        cancel = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        cancel.rejected.connect(self.reject)
        layout.addWidget(cancel)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _set_status(self, msg: str, error: bool = False) -> None:
        color = "#e05252" if error else _theme.c("TEXT_SECONDARY")
        self._status.setStyleSheet(
            f"color: {color}; font-size: 10pt; background: transparent;"
        )
        self._status.setText(msg)

    def _on_browse_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            _t("theme_import_from_file"),
            str(Path.home()),
            I18n.translate("ui", "theme_ini_filter"),
        )
        if not path:
            return
        try:
            tid = import_theme_from_file(Path(path))
        except (ValueError, OSError) as e:
            self._set_status(f"{_t('theme_import_invalid')}: {e}", error=True)
            return
        self.imported_theme_id = tid
        self.accept()

    def _on_browse_community(self) -> None:
        webbrowser.open("https://loteran.github.io/asm-presets/")

    def _on_import(self) -> None:
        url = self._url_edit.text().strip()
        if not url:
            self._set_status(_t("theme_import_invalid"), error=True)
            return

        if not is_theme_link(url):
            self._set_status(_t("theme_import_invalid"), error=True)
            return

        try:
            parsed = decode_theme_link(url)
            tid = save_user_theme(parsed["name"], parsed["colors"])
        except (ThemeImportError, ValueError, OSError) as e:
            self._set_status(f"{_t('theme_import_invalid')}: {e}", error=True)
            return

        self.imported_theme_id = tid
        self._set_status(_t("theme_import_success").format(name=parsed["name"]))
        self.accept()
