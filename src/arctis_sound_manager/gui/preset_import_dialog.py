# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
preset_import_dialog.py — Dialog for importing EQ presets via deep link.

Accepts:
  - arctis-asm://import?data=<base64>   (ASM self-contained link)
  - https://www.steelseries.com/deeplink/gg/sonar/config/v1/import?url=<base64>
"""
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

from PySide6.QtCore import QThread, Signal, Slot
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

from arctis_sound_manager.gui.preset_share import (
    PresetImportError,
    decode_asm_link,
    decode_steelseries_link,
    is_asm_link,
    is_steelseries_link,
    parse_steelseries_cdn_payload,
    sanitize_filename,
    virtual_device_to_tag,
)
import arctis_sound_manager.gui.theme as _theme
from arctis_sound_manager.i18n import I18n

_PRESETS_DIR = Path.home() / ".config" / "arctis_manager" / "sonar_presets"


def _t(key: str) -> str:
    return I18n.translate("ui", key)


# ── Network worker ────────────────────────────────────────────────────────────

class _CdnFetchWorker(QThread):
    """Fetch a preset JSON from a SteelSeries CDN URL in a background thread."""

    done = Signal(bool, object)  # (success, dict|str)

    def __init__(self, cdn_url: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._url = cdn_url

    def run(self):
        try:
            req = urllib.request.Request(
                self._url,
                headers={"User-Agent": "Arctis-Sound-Manager/1.0"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = resp.read()
            payload = json.loads(raw)
            self.done.emit(True, payload)
        except urllib.error.HTTPError as e:
            self.done.emit(False, f"HTTP {e.code}: {e.reason}")
        except urllib.error.URLError as e:
            self.done.emit(False, str(e.reason))
        except socket.timeout:
            self.done.emit(False, _t("import_timeout"))
        except json.JSONDecodeError as e:
            self.done.emit(False, f"JSON error: {e}")
        except Exception as e:
            self.done.emit(False, str(e))


# ── Import dialog ─────────────────────────────────────────────────────────────

class PresetImportDialog(QDialog):
    """Dialog that accepts an ASM or SteelSeries deep link and saves the preset."""

    def __init__(self, channel: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._channel = channel
        self._worker: _CdnFetchWorker | None = None

        self.imported_name: str | None = None
        self.imported_tag:  str | None = None

        self.setWindowTitle(_t("import_preset"))
        self.setMinimumWidth(480)
        self.setStyleSheet(
            f"background-color: {_theme.c('BG_MAIN')}; color: {_theme.c('TEXT_PRIMARY')};"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        layout.addWidget(QLabel(
            _t("import_preset_label"),
            styleSheet=f"color: {_theme.c('TEXT_PRIMARY')}; font-size: 11pt; background: transparent;"
        ))

        self._url_edit = QLineEdit()
        self._url_edit.setPlaceholderText(_t("import_url_placeholder"))
        self._url_edit.setStyleSheet(
            f"QLineEdit {{ background: {_theme.c('BG_BUTTON')}; border: 1px solid {_theme.c('BORDER')}; "
            f"border-radius: 6px; color: {_theme.c('TEXT_PRIMARY')}; padding: 6px 10px; font-size: 10pt; }}"
            f"QLineEdit:focus {{ border-color: {_theme.c('ACCENT')}; }}"
        )
        layout.addWidget(self._url_edit)

        self._browse_file_btn = QPushButton(_t("import_from_file"))
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

        self._import_btn = QPushButton(_t("import_preset"))
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

        self._browse_community_btn = QPushButton(_t("browse_community"))
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
            self, _t("import_from_file"), str(Path.home()), "JSON Files (*.json)"
        )
        if path:
            try:
                raw = json.loads(Path(path).read_text(encoding="utf-8"))
                # Accept both wrapper format {"name":…,"data":{…}} and raw preset data
                if "data" in raw:
                    info = raw
                else:
                    info = {"name": Path(path).stem, "data": raw}
                if "name" not in info:
                    info["name"] = Path(path).stem
                self._finalize_import(info)
            except (json.JSONDecodeError, KeyError) as e:
                self._set_status(f"{_t('import_invalid_url')}: {e}", error=True)

    def _on_browse_community(self) -> None:
        webbrowser.open("https://loteran.github.io/asm-presets/")

    def _on_import(self) -> None:
        url = self._url_edit.text().strip()
        if not url:
            self._set_status(_t("import_invalid_url"), error=True)
            return

        if is_asm_link(url):
            try:
                result = decode_asm_link(url)
                self._finalize_import(result)
            except PresetImportError as e:
                self._set_status(f"{_t('import_invalid_url')}: {e}", error=True)

        elif is_steelseries_link(url):
            try:
                cdn_url = decode_steelseries_link(url)
            except PresetImportError as e:
                self._set_status(f"{_t('import_invalid_url')}: {e}", error=True)
                return
            self._set_status(_t("import_downloading"))
            self._import_btn.setEnabled(False)
            self._worker = _CdnFetchWorker(cdn_url, self)
            self._worker.done.connect(self._on_fetch_done)
            self._worker.start()

        else:
            self._set_status(_t("import_invalid_url"), error=True)

    @Slot(bool, object)
    def _on_fetch_done(self, ok: bool, result: object) -> None:
        self._import_btn.setEnabled(True)
        if not ok:
            self._set_status(f"{_t('import_download_failed')}: {result}", error=True)
            return
        try:
            normalized = parse_steelseries_cdn_payload(result)  # type: ignore[arg-type]
            self._finalize_import(normalized)
        except PresetImportError as e:
            self._set_status(f"{_t('import_invalid_url')}: {e}", error=True)

    def _finalize_import(self, info: dict) -> None:
        """Write the preset file and close the dialog."""
        name     = sanitize_filename(info["name"])
        device   = info.get("virtualAudioDevice", self._channel)
        tag      = virtual_device_to_tag(device)
        data     = info["data"]

        _PRESETS_DIR.mkdir(parents=True, exist_ok=True)
        filename = f"{name} {tag}.json"
        dest     = _PRESETS_DIR / filename

        # Avoid silent overwrites — append (2), (3) … if needed
        if dest.exists():
            counter = 2
            stem = name
            while dest.exists():
                name = f"{stem} ({counter})"
                dest = _PRESETS_DIR / f"{name} {tag}.json"
                counter += 1

        dest.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        self.imported_name = name
        self.imported_tag  = tag
        self._set_status(f"{_t('import_success')}: {name} {tag}")
        self.accept()
