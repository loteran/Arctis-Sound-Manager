# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""The share step: what to do with a clip once it has been exported.

Sharing was a dashed strip along the bottom of the editor — present whether or
not anything had been exported, and easy to miss at the moment it finally
mattered. Exporting is the end of the job, so it now ends in something: a
window that shows the finished file, says how big it is, and offers the three
ways it actually leaves the machine — dragged into a chat, copied, or opened in
the folder.

The drag is the important one. Discord, a browser upload box and a file manager
all accept a ``text/uri-list`` drop, which is the only route that needs no
intermediate step at all.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QMimeData, QSize, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QDrag, QGuiApplication, QIcon, QPixmap
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QPushButton,
                               QVBoxLayout, QWidget)

import arctis_sound_manager.gui.theme as _theme
from arctis_sound_manager.i18n import I18n

logger = logging.getLogger("ClipShare")

PREVIEW_SIZE = QSize(320, 180)


def _tr(key: str, fallback: str) -> str:
    try:
        value = I18n.translate("ui", key)
    except Exception:
        return fallback
    return fallback if not value or value == key else value


class DragOutCard(QLabel):
    """The exported file as something to pick up and drop somewhere else.

    A poster frame rather than a filename: the drag has to look like the clip
    it carries, or the gesture reads as dragging a label around.
    """

    def __init__(self, path: Path, parent=None):
        super().__init__(parent)
        self._path = path
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(PREVIEW_SIZE)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip(_tr("clip_share_drag_hint",
                            "Drag this into Discord, a browser, anywhere"))
        self._apply_frame()

    def _apply_frame(self) -> None:
        pixmap = None
        try:
            from arctis_sound_manager.clip_thumbs import thumbnail
            frame = thumbnail(self._path, width=PREVIEW_SIZE.width())
            if frame is not None:
                pixmap = QPixmap(str(frame))
        except Exception:                            # pragma: no cover - cosmetic
            logger.debug("no poster frame for the share card", exc_info=True)

        if pixmap is not None and not pixmap.isNull():
            self.setPixmap(pixmap.scaled(
                PREVIEW_SIZE, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
        else:
            self.setText("📎")
        self.setStyleSheet(
            f"border: 2px dashed {_theme.c('ACCENT')}; border-radius: 8px; "
            f"padding: 6px; background: transparent; font-size: 28pt; "
            f"color: {_theme.c('TEXT_SECONDARY')};")

    def mousePressEvent(self, event) -> None:
        if not self._path.exists():
            return
        data = QMimeData()
        data.setUrls([QUrl.fromLocalFile(str(self._path))])
        data.setText(str(self._path))
        drag = QDrag(self)
        drag.setMimeData(data)
        pixmap = self.pixmap()
        if pixmap is not None and not pixmap.isNull():
            drag.setPixmap(pixmap.scaledToWidth(
                160, Qt.TransformationMode.SmoothTransformation))
        drag.exec(Qt.DropAction.CopyAction)


class ClipShareDialog(QDialog):
    """Shown when an export finishes: the file, and the ways out of here."""

    def __init__(self, path: Path, parent=None):
        super().__init__(parent)
        self._path = path

        self.setWindowTitle(_tr("clip_share_title", "Share clip"))
        self.setMinimumWidth(420)

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(14)

        headline = QLabel(_tr("clip_share_ready", "Your clip is ready to share."))
        headline.setStyleSheet(
            f"color: {_theme.c('TEXT_PRIMARY')}; font-size: 13pt; "
            f"font-weight: bold; background: transparent;")
        root.addWidget(headline)

        card_row = QHBoxLayout()
        card_row.addStretch(1)
        self._card = DragOutCard(path, self)
        card_row.addWidget(self._card)
        card_row.addStretch(1)
        root.addLayout(card_row)

        hint = QLabel(_tr("clip_share_drag_hint",
                          "Drag this into Discord, a browser, anywhere"))
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setWordWrap(True)
        hint.setStyleSheet(
            f"color: {_theme.c('TEXT_SECONDARY')}; font-size: 9pt; "
            f"background: transparent;")
        root.addWidget(hint)

        self._detail = QLabel(_describe(path))
        self._detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._detail.setWordWrap(True)
        self._detail.setStyleSheet(
            f"color: {_theme.c('TEXT_SECONDARY')}; font-size: 9pt; "
            f"background: transparent;")
        root.addWidget(self._detail)

        root.addWidget(self._build_actions())

    def _build_actions(self) -> QWidget:
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        copy_btn = QPushButton(_tr("clip_share_copy", "Copy file"))
        copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_btn.clicked.connect(self._on_copy)
        row.addWidget(copy_btn)

        folder_btn = QPushButton(_tr("clip_share_folder", "Open folder"))
        folder_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        folder_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(self._path.parent))))
        row.addWidget(folder_btn)

        row.addStretch(1)

        done_btn = QPushButton(_tr("clip_share_done", "Done"))
        done_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        done_btn.setDefault(True)
        done_btn.clicked.connect(self.accept)
        row.addWidget(done_btn)
        return box

    def _on_copy(self) -> None:
        """Put the file on the clipboard, not its path.

        Pasting into a chat has to arrive as the clip. The path is included as
        the text flavour for targets that take nothing else (a terminal, a
        filename field), which costs nothing and rescues those cases.
        """
        data = QMimeData()
        data.setUrls([QUrl.fromLocalFile(str(self._path))])
        data.setText(str(self._path))
        clipboard = QGuiApplication.clipboard()
        if clipboard is None:                        # pragma: no cover - headless
            return
        clipboard.setMimeData(data)
        self._detail.setText(_tr("clip_share_copied", "Copied — paste it anywhere."))


def _describe(path: Path) -> str:
    """Name and size, so there is no doubt which file this is."""
    try:
        size_mb = path.stat().st_size / (1024 * 1024)
    except OSError:
        return path.name
    return f"{path.name}   ·   {size_mb:.1f} MB"
