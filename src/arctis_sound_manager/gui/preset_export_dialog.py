"""
preset_export_dialog.py — Dialog for exporting an EQ preset.

Shows two sections:
  - Share Link : arctis-asm://import?data=… (copy button)
  - JSON File  : pretty-printed JSON (copy button + save-to-file button)
"""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import QTimer

from arctis_sound_manager.gui.theme import (
    ACCENT,
    BG_BUTTON,
    BG_MAIN,
    BORDER,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)
from arctis_sound_manager.i18n import I18n


def _t(key: str) -> str:
    return I18n.translate("ui", key)


class PresetExportDialog(QDialog):
    """Dialog showing the share link and JSON of an exported preset."""

    def __init__(
        self,
        preset_name: str,
        link: str,
        preset_data: dict,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._preset_name = preset_name
        self._link = link
        self._json_text = json.dumps(preset_data, indent=2, ensure_ascii=False)

        self.setWindowTitle(_t("export_dialog_title"))
        self.setMinimumWidth(520)
        self.setStyleSheet(f"background-color: {BG_MAIN}; color: {TEXT_PRIMARY};")

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(16)

        # ── Header ────────────────────────────────────────────────────────────
        header = QLabel(f"{_t('export_dialog_title')}: <b>{preset_name}</b>")
        header.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-size: 11pt; background: transparent;"
        )
        root.addWidget(header)

        # ── Share Link section ────────────────────────────────────────────────
        root.addWidget(self._section_label(_t("export_share_link")))

        link_row = QHBoxLayout()
        link_row.setSpacing(8)

        self._link_edit = QLineEdit(link)
        self._link_edit.setReadOnly(True)
        self._link_edit.setStyleSheet(self._input_ss())
        link_row.addWidget(self._link_edit)

        self._copy_link_btn = QPushButton(_t("export_copy_link"))
        self._copy_link_btn.setFixedWidth(110)
        self._copy_link_btn.setStyleSheet(self._btn_ss(accent=True))
        self._copy_link_btn.clicked.connect(self._copy_link)
        link_row.addWidget(self._copy_link_btn)

        root.addLayout(link_row)

        # ── JSON section ──────────────────────────────────────────────────────
        root.addWidget(self._section_label(_t("export_json_file")))

        self._json_edit = QPlainTextEdit(self._json_text)
        self._json_edit.setReadOnly(True)
        self._json_edit.setStyleSheet(
            f"QPlainTextEdit {{"
            f"  background: {BG_BUTTON}; border: 1px solid {BORDER};"
            f"  border-radius: 6px; color: {TEXT_PRIMARY};"
            f"  font-family: monospace; font-size: 9pt;"
            f"  padding: 6px;"
            f"}}"
        )
        self._json_edit.setMinimumHeight(160)
        self._json_edit.setMaximumHeight(240)
        root.addWidget(self._json_edit)

        json_btns = QHBoxLayout()
        json_btns.setSpacing(8)
        json_btns.addStretch()

        self._copy_json_btn = QPushButton(_t("export_copy_json"))
        self._copy_json_btn.setStyleSheet(self._btn_ss())
        self._copy_json_btn.clicked.connect(self._copy_json)
        json_btns.addWidget(self._copy_json_btn)

        self._save_btn = QPushButton(_t("export_save_file"))
        self._save_btn.setStyleSheet(self._btn_ss(accent=True))
        self._save_btn.clicked.connect(self._save_file)
        json_btns.addWidget(self._save_btn)

        root.addLayout(json_btns)

        # ── Status label ──────────────────────────────────────────────────────
        self._status = QLabel("")
        self._status.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 9pt; background: transparent;"
        )
        root.addWidget(self._status)

        # ── Close button ──────────────────────────────────────────────────────
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _section_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 9pt; font-weight: bold;"
            f" text-transform: uppercase; background: transparent;"
        )
        return lbl

    def _input_ss(self) -> str:
        return (
            f"QLineEdit {{"
            f"  background: {BG_BUTTON}; border: 1px solid {BORDER};"
            f"  border-radius: 6px; color: {TEXT_PRIMARY}; padding: 6px 10px; font-size: 9pt;"
            f"}}"
        )

    def _btn_ss(self, *, accent: bool = False) -> str:
        bg = ACCENT if accent else BG_BUTTON
        fg = "#fff" if accent else TEXT_PRIMARY
        border = "none" if accent else f"1px solid {BORDER}"
        return (
            f"QPushButton {{"
            f"  background: {bg}; border: {border}; border-radius: 6px;"
            f"  color: {fg}; padding: 6px 14px; font-size: 10pt;"
            f"}}"
            f"QPushButton:hover {{ opacity: 0.85; }}"
        )

    def _flash_status(self, msg: str) -> None:
        self._status.setText(msg)
        QTimer.singleShot(2500, lambda: self._status.setText(""))

    # ── Actions ───────────────────────────────────────────────────────────────

    def _copy_link(self) -> None:
        QApplication.clipboard().setText(self._link)
        self._copy_link_btn.setText("✓ " + _t("export_copied_short"))
        QTimer.singleShot(2000, lambda: self._copy_link_btn.setText(_t("export_copy_link")))

    def _copy_json(self) -> None:
        QApplication.clipboard().setText(self._json_text)
        self._copy_json_btn.setText("✓ " + _t("export_copied_short"))
        QTimer.singleShot(2000, lambda: self._copy_json_btn.setText(_t("export_copy_json")))

    def _save_file(self) -> None:
        safe_name = self._preset_name.replace("/", "-").replace("\\", "-")
        default_path = str(Path.home() / f"{safe_name}.json")
        path, _ = QFileDialog.getSaveFileName(
            self,
            _t("export_save_file"),
            default_path,
            "JSON Files (*.json)",
        )
        if path:
            Path(path).write_text(self._json_text, encoding="utf-8")
            self._flash_status(f"✓ {_t('export_file_saved')}: {Path(path).name}")
