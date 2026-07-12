# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
theme_export_dialog.py — Dialog for exporting/sharing a theme.

Shows three sections:
  - Share Link      : arctis-asm://import-theme?data=… (copy button)
  - Theme file .ini : same format as theme.export_theme_to_file (copy + save)
  - Share to community : opens the community submission page in the browser
"""
from __future__ import annotations

import configparser
import io
import urllib.parse
import webbrowser
from pathlib import Path

from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import QTimer

import arctis_sound_manager.gui.theme as _theme
from arctis_sound_manager.gui.theme import THEME_KEYS
from arctis_sound_manager.gui.theme_share import build_theme_link
from arctis_sound_manager.i18n import I18n


def _t(key: str) -> str:
    return I18n.translate("ui", key)


class ThemeExportDialog(QDialog):
    """Dialog showing the share link and .ini file of an exported theme."""

    def __init__(
        self,
        theme_name: str,
        colors: dict,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._theme_name = theme_name
        self._colors = dict(colors)
        self._link = build_theme_link(theme_name, self._colors)
        self._ini_text = self._build_ini_text()

        self.setWindowTitle(_t("theme_export_title"))
        self.setMinimumWidth(520)
        self.setStyleSheet(
            f"background-color: {_theme.c('BG_MAIN')}; color: {_theme.c('TEXT_PRIMARY')};"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(16)

        # ── Header ────────────────────────────────────────────────────────────
        header = QLabel(f"{_t('theme_export_title')}: <b>{theme_name}</b>")
        header.setStyleSheet(
            f"color: {_theme.c('TEXT_PRIMARY')}; font-size: 11pt; background: transparent;"
        )
        root.addWidget(header)

        # ── Share Link section ────────────────────────────────────────────────
        root.addWidget(self._section_label(_t("theme_export_share_link")))

        link_row = QHBoxLayout()
        link_row.setSpacing(8)

        self._link_edit = QLineEdit(self._link)
        self._link_edit.setReadOnly(True)
        self._link_edit.setStyleSheet(self._input_ss())
        link_row.addWidget(self._link_edit)

        self._copy_link_btn = QPushButton(_t("export_copy_link"))
        self._copy_link_btn.setFixedWidth(110)
        self._copy_link_btn.setStyleSheet(self._btn_ss(accent=True))
        self._copy_link_btn.clicked.connect(self._copy_link)
        link_row.addWidget(self._copy_link_btn)

        root.addLayout(link_row)

        # ── Theme file (.ini) section ─────────────────────────────────────────
        root.addWidget(self._section_label(_t("theme_export_ini_file")))

        ini_btns = QHBoxLayout()
        ini_btns.setSpacing(8)
        ini_btns.addStretch()

        self._copy_ini_btn = QPushButton(_t("theme_export_copy_ini"))
        self._copy_ini_btn.setStyleSheet(self._btn_ss())
        self._copy_ini_btn.clicked.connect(self._copy_ini)
        ini_btns.addWidget(self._copy_ini_btn)

        self._save_btn = QPushButton(_t("export_save_file"))
        self._save_btn.setStyleSheet(self._btn_ss(accent=True))
        self._save_btn.clicked.connect(self._save_file)
        ini_btns.addWidget(self._save_btn)

        root.addLayout(ini_btns)

        # ── Share to community section ────────────────────────────────────────
        root.addWidget(self._section_label(_t("theme_share_to_community")))

        community_desc = QLabel(_t("theme_share_community_desc"))
        community_desc.setWordWrap(True)
        community_desc.setStyleSheet(
            f"color: {_theme.c('TEXT_SECONDARY')}; font-size: 9pt; background: transparent;"
        )
        root.addWidget(community_desc)

        community_row = QHBoxLayout()
        community_row.setSpacing(8)
        community_row.addStretch()

        self._publish_btn = QPushButton(_t("theme_share_community_btn"))
        self._publish_btn.setStyleSheet(self._btn_ss(accent=True))
        self._publish_btn.clicked.connect(self._share_community)
        community_row.addWidget(self._publish_btn)

        root.addLayout(community_row)

        # ── Status label ──────────────────────────────────────────────────────
        self._status = QLabel("")
        self._status.setStyleSheet(
            f"color: {_theme.c('TEXT_SECONDARY')}; font-size: 9pt; background: transparent;"
        )
        root.addWidget(self._status)

        # ── Close button ──────────────────────────────────────────────────────
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _build_ini_text(self) -> str:
        """Serialize name + colors as the same .ini format as export_theme_to_file."""
        parser = configparser.ConfigParser()
        parser.optionxform = str
        parser["theme"] = {"name": self._theme_name}
        parser["colors"] = {k: self._colors.get(k, "#000000") for k in THEME_KEYS}
        buf = io.StringIO()
        parser.write(buf)
        return buf.getvalue()

    def _section_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"color: {_theme.c('TEXT_SECONDARY')}; font-size: 9pt; font-weight: bold;"
            f" text-transform: uppercase; background: transparent;"
        )
        return lbl

    def _input_ss(self) -> str:
        return (
            f"QLineEdit {{"
            f"  background: {_theme.c('BG_BUTTON')}; border: 1px solid {_theme.c('BORDER')};"
            f"  border-radius: 6px; color: {_theme.c('TEXT_PRIMARY')}; padding: 6px 10px; font-size: 9pt;"
            f"}}"
        )

    def _btn_ss(self, *, accent: bool = False) -> str:
        bg = _theme.c('ACCENT') if accent else _theme.c('BG_BUTTON')
        fg = "#fff" if accent else _theme.c('TEXT_PRIMARY')
        border = "none" if accent else f"1px solid {_theme.c('BORDER')}"
        return (
            f"QPushButton {{"
            f"  background: {bg}; border: {border}; border-radius: 6px;"
            f"  color: {fg}; padding: 6px 14px; font-size: 10pt;"
            f"}}"
            f"QPushButton:hover {{ opacity: 0.85; }}"
        )

    def _flash_status(self, msg: str) -> None:
        self._status.setText(msg)
        # Bind the timer to `self` (the dialog) as context: if the dialog is
        # closed/destroyed before it fires, Qt cancels it instead of calling the
        # lambda on a deleted C++ widget (RuntimeError).
        QTimer.singleShot(2500, self, lambda: self._status.setText(""))

    # ── Actions ───────────────────────────────────────────────────────────────

    def _copy_link(self) -> None:
        QApplication.clipboard().setText(self._link)
        self._copy_link_btn.setText("✓ " + _t("export_copied_short"))
        QTimer.singleShot(2000, self, lambda: self._copy_link_btn.setText(_t("export_copy_link")))

    def _copy_ini(self) -> None:
        QApplication.clipboard().setText(self._ini_text)
        self._copy_ini_btn.setText("✓ " + _t("export_copied_short"))
        QTimer.singleShot(2000, self, lambda: self._copy_ini_btn.setText(_t("theme_export_copy_ini")))

    def _share_community(self) -> None:
        params: dict[str, str] = {
            "submit": "1",
            "type":   "theme",
            "data":   self._link,
            "name":   self._theme_name,
        }
        url = "https://loteran.github.io/asm-presets/?" + urllib.parse.urlencode(params)
        webbrowser.open(url)

    def _save_file(self) -> None:
        safe_name = self._theme_name.replace("/", "-").replace("\\", "-")
        default_path = str(Path.home() / f"{safe_name}.ini")
        path, _ = QFileDialog.getSaveFileName(
            self,
            _t("export_save_file"),
            default_path,
            "Theme Files (*.ini)",
        )
        if path:
            Path(path).write_text(self._ini_text, encoding="utf-8")
            self._flash_status(f"✓ {_t('export_file_saved')}: {Path(path).name}")
