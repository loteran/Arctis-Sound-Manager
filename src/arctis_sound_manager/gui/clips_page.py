# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Clips page — start the rolling capture, save the last N seconds, browse them.

The capture itself runs in :mod:`clip_capture`; this page owns only the UI and
the lifetime of the capture object. Clip capture needs PyGObject and GStreamer,
which are optional for ASM as a whole, so nothing here imports them at module
level: the page must still build (and explain itself) on a machine that has
neither, rather than taking the whole window down with an ImportError.
"""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

from PySide6.QtCore import (QMimeData, QObject, QPointF, QRunnable, QSize, Qt,
                            QThreadPool, QTimer, QUrl, Signal)
from PySide6.QtGui import (QColor, QDesktopServices, QIcon, QKeySequence,
                           QPainter, QPixmap, QPolygonF, QShortcut)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

import arctis_sound_manager.gui.theme as _theme
from arctis_sound_manager.i18n import I18n

logger = logging.getLogger("ClipsPage")

CLIP_DIR = Path.home() / "Videos" / "ASM Clips"

# Card geometry. The thumbnail is 16:9 because that is what a captured screen
# almost always is; a card wide enough to read the game name at a glance is the
# point of the grid, so these are deliberately larger than an icon view's
# defaults.
THUMB_SIZE = QSize(256, 144)
CARD_SIZE = QSize(THUMB_SIZE.width() + 24, THUMB_SIZE.height() + 62)

# Poster frames are extracted by ffmpeg, one process each. Two at a time keeps
# a freshly opened library responsive without handing the machine a job per
# clip while a game is running — which is exactly when this page gets used.
_THUMB_THREADS = 2


def _tr(key: str, fallback: str) -> str:
    """Translate *key*, falling back to English while the .ini catches up."""
    try:
        value = I18n.translate("ui", key)
    except Exception:
        return fallback
    return fallback if not value or value == key else value


# ── card text ─────────────────────────────────────────────────────────────────
#
# Clips are named clip_<Y-m-d>_<H-M-S>[_<game>].mkv by clip_capture. Both halves
# are worth showing and neither is worth showing raw.

_ASM_NAME = re.compile(
    r"^clip_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}(?:_(?P<game>.+))?$")


def clip_title(path) -> str:
    """What to write on a clip's card: the game it came from, or its own name.

    The window title is written into the filename with its spaces replaced, so
    for ASM's own names this is the reverse. A game whose real name contains an
    underscore comes back with a space instead — worth it for every other title
    reading properly.

    A name that is not ASM's is shown as it stands: the user renamed that file
    to something meaningful to them, and replacing it with a generic label would
    throw away the only description the clip has.
    """
    stem = Path(path).stem
    match = _ASM_NAME.match(stem)
    if match is None:
        return stem
    game = (match.group("game") or "").replace("_", " ").strip()
    return game or _tr("clips_untitled", "Clip")


def clip_caption(mtime: float, size_bytes: int) -> str:
    """The second line of a card: when it was taken and how big it is."""
    return f"{_format_time(mtime)}   ·   {size_bytes / (1024 * 1024):.0f} MB"


# ── poster frames ─────────────────────────────────────────────────────────────

_PLACEHOLDER: QIcon | None = None


def _placeholder_icon() -> QIcon:
    """The card shown before (or instead of) a real frame.

    Drawn rather than shipped as an asset so it follows the theme, and cached
    because the grid asks for it once per clip.
    """
    global _PLACEHOLDER
    if _PLACEHOLDER is not None:
        return _PLACEHOLDER

    pixmap = QPixmap(THUMB_SIZE)
    pixmap.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(_theme.c("SURFACE")))
    painter.drawRoundedRect(pixmap.rect(), 8, 8)

    side = THUMB_SIZE.height() * 0.32
    cx, cy = THUMB_SIZE.width() / 2, THUMB_SIZE.height() / 2
    painter.setBrush(QColor(_theme.c("TEXT_SECONDARY")))
    painter.drawPolygon(QPolygonF([
        QPointF(cx - side * 0.4, cy - side * 0.6),
        QPointF(cx - side * 0.4, cy + side * 0.6),
        QPointF(cx + side * 0.7, cy),
    ]))
    painter.end()

    _PLACEHOLDER = QIcon(pixmap)
    return _PLACEHOLDER


class _ThumbSignals(QObject):
    """Carries a finished frame back to the GUI thread."""

    ready = Signal(str, str)      # clip path, thumbnail path


class _ThumbJob(QRunnable):
    """Extract one poster frame off the GUI thread.

    ffmpeg takes a moment per clip, and doing this inline would freeze the page
    for as long as the library is deep — on the tab the user opens *because*
    something just happened in a game.
    """

    def __init__(self, clip: Path, signals: _ThumbSignals):
        super().__init__()
        self._clip = clip
        self._signals = signals

    def run(self) -> None:                          # pragma: no cover - thread
        try:
            from arctis_sound_manager.clip_thumbs import thumbnail
            path = thumbnail(self._clip)
        except Exception:
            logger.debug("thumbnail failed for %s", self._clip.name, exc_info=True)
            return
        if path is not None:
            self._signals.ready.emit(str(self._clip), str(path))


class ClipGrid(QListWidget):
    """Clip library as a grid of preview cards, draggable into other apps.

    Sharing a clip is the point of having one, and the fastest route is to drop
    the file onto Discord, a browser upload box or a file manager. Qt will only
    offer that if the drag carries ``text/uri-list``, so the payload is built
    here rather than relying on the default (which drags the row's text and
    lands as a meaningless string in the target).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(True)
        # Static movement and Adjust resize are what make this a grid that
        # reflows with the window instead of an icon view the user can shuffle
        # items around in by dragging — which the drag-to-share gesture would
        # otherwise turn into an accident.
        self.setMovement(QListView.Movement.Static)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setIconSize(THUMB_SIZE)
        self.setGridSize(CARD_SIZE)
        self.setSpacing(6)
        self.setUniformItemSizes(True)
        self.setWordWrap(True)
        self.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)

    def mimeData(self, items) -> QMimeData:  # type: ignore[override]
        data = QMimeData()
        urls = []
        for item in items:
            path = item.data(Qt.ItemDataRole.UserRole)
            if path and Path(path).exists():
                urls.append(QUrl.fromLocalFile(path))
        if urls:
            data.setUrls(urls)
            # Some targets read only the text flavour; give them the path so a
            # drop into a terminal or text field is still useful.
            data.setText("\n".join(u.toLocalFile() for u in urls))
        return data


class ClipsPage(QWidget):
    """Rolling capture control plus the clip library."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._capture = None
        self._error: str | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        title = QLabel(_tr("clips", "Clips"))
        title.setStyleSheet(
            f"color: {_theme.c('TEXT_PRIMARY')}; font-size: 16pt; "
            f"font-weight: bold; background: transparent;")
        root.addWidget(title)

        self._hint = QLabel(_tr(
            "clips_hint",
            "Keeps the last seconds of your screen buffered in memory. "
            "Press Save to write what already happened — no need to start "
            "recording first."))
        self._hint.setWordWrap(True)
        self._hint.setStyleSheet(
            f"color: {_theme.c('TEXT_SECONDARY')}; font-size: 9pt; "
            f"background: transparent;")
        root.addWidget(self._hint)

        # ── controls ──────────────────────────────────────────────────────────
        controls = QHBoxLayout()
        controls.setSpacing(10)

        self._toggle_btn = QPushButton(_tr("clips_start", "Start capture"))
        self._toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle_btn.clicked.connect(self._on_toggle)
        controls.addWidget(self._toggle_btn)

        controls.addWidget(QLabel(_tr("clips_length", "Length:")))
        self._seconds = QSpinBox()
        self._seconds.setRange(5, 300)
        self._seconds.setValue(30)
        self._seconds.setSuffix(" s")
        controls.addWidget(self._seconds)

        self._save_btn = QPushButton(_tr("clips_save", "Save last seconds"))
        self._save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._save_btn.setEnabled(False)
        self._save_btn.clicked.connect(self._on_save)
        controls.addWidget(self._save_btn)

        controls.addStretch(1)

        self._folder_btn = QPushButton(_tr("clips_open_folder", "Open folder"))
        self._folder_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._folder_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(CLIP_DIR))))
        controls.addWidget(self._folder_btn)

        root.addLayout(controls)

        self._status = QLabel("")
        self._status.setStyleSheet(
            f"color: {_theme.c('TEXT_SECONDARY')}; font-size: 9pt; "
            f"background: transparent;")
        root.addWidget(self._status)

        # ── shortcut row ──────────────────────────────────────────────────────
        shortcut_row = QHBoxLayout()
        shortcut_row.setSpacing(8)
        self._shortcut_lbl = QLabel("")
        self._shortcut_lbl.setStyleSheet(
            f"color: {_theme.c('TEXT_SECONDARY')}; font-size: 9pt; "
            f"background: transparent;")
        shortcut_row.addWidget(self._shortcut_lbl)

        self._shortcut_btn = QPushButton(_tr("clips_change_shortcut", "Change…"))
        self._shortcut_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._shortcut_btn.setEnabled(False)
        self._shortcut_btn.clicked.connect(self._on_configure_shortcut)
        shortcut_row.addWidget(self._shortcut_btn)
        shortcut_row.addStretch(1)
        root.addLayout(shortcut_row)

        # ── library ───────────────────────────────────────────────────────────
        library_bar = QHBoxLayout()
        library_bar.setSpacing(8)

        self._select_all_btn = QPushButton(_tr("clips_select_all", "Select all"))
        self._select_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._select_all_btn.clicked.connect(self._on_select_all)
        library_bar.addWidget(self._select_all_btn)

        self._rename_btn = QPushButton(_tr("clips_rename", "Rename"))
        self._rename_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._rename_btn.clicked.connect(self._on_rename)
        library_bar.addWidget(self._rename_btn)

        self._delete_btn = QPushButton(_tr("clips_delete", "Delete"))
        self._delete_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._delete_btn.clicked.connect(self._on_delete)
        library_bar.addWidget(self._delete_btn)

        library_bar.addStretch(1)

        self._selection_lbl = QLabel("")
        self._selection_lbl.setStyleSheet(
            f"color: {_theme.c('TEXT_SECONDARY')}; font-size: 9pt; "
            f"background: transparent;")
        library_bar.addWidget(self._selection_lbl)
        root.addLayout(library_bar)

        self._list = ClipGrid()
        self._list.itemDoubleClicked.connect(self._on_open_clip)
        self._list.itemSelectionChanged.connect(self._update_selection_actions)
        # Delete is the gesture people already know from every file manager;
        # a grid where it does nothing reads as broken.
        delete_shortcut = QShortcut(QKeySequence.StandardKey.Delete, self._list)
        delete_shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        delete_shortcut.activated.connect(self._on_delete)
        root.addWidget(self._list, stretch=1)

        self._empty = QLabel(_tr(
            "clips_empty",
            "No clips yet. Start the capture, then press Save (or the shortcut) "
            "after something worth keeping happens."))
        self._empty.setWordWrap(True)
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setStyleSheet(
            f"color: {_theme.c('TEXT_SECONDARY')}; font-size: 9pt; "
            f"background: transparent;")
        self._empty.hide()
        root.addWidget(self._empty)

        self._open_hint = QLabel(_tr(
            "clips_open_hint",
            "Double-click a clip to open it — the last seconds are already "
            "selected, ready to export. Drag one into Discord or a browser to "
            "share the whole thing."))
        self._open_hint.setWordWrap(True)
        self._open_hint.setStyleSheet(
            f"color: {_theme.c('TEXT_SECONDARY')}; font-size: 8pt; "
            f"background: transparent;")
        root.addWidget(self._open_hint)

        # Poster frames are fetched off-thread; the pool is owned here so it can
        # be drained on shutdown rather than outliving the page.
        self._thumb_pool = QThreadPool(self)
        self._thumb_pool.setMaxThreadCount(_THUMB_THREADS)
        self._thumb_signals = _ThumbSignals()
        self._thumb_signals.ready.connect(self._on_thumb_ready)
        self._thumb_queued: set[str] = set()
        self._closing = False

        # The buffered seconds only mean anything while capturing, but the
        # library is refreshed regardless so clips written by asm-clipd (or a
        # keyboard shortcut) show up without reopening the page.
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

        self._shortcut = None
        self._bind_shortcut()
        self.refresh_clips()
        self._update_status()

    # ── global shortcut ───────────────────────────────────────────────────────

    def _bind_shortcut(self) -> None:
        """Ask the compositor for the save-clip shortcut, and show what it gave."""
        try:
            from arctis_sound_manager.clip_shortcut import ClipShortcut
        except Exception as exc:                     # pragma: no cover - env dependent
            logger.debug("global shortcut unavailable: %s", exc)
            self._update_shortcut_label()
            return

        self._shortcut = ClipShortcut(on_activate=self._on_shortcut_fired)
        if self._shortcut.bind():
            self._shortcut_btn.setEnabled(True)
        self._update_shortcut_label()

    def _on_shortcut_fired(self) -> None:
        """The shortcut fired — save if capturing, and say so if not.

        Firing while the capture is off is the likeliest way for this to seem
        broken, so it reports that state rather than doing nothing silently.
        """
        if self._capture is None:
            self._status.setText(_tr(
                "clips_shortcut_idle",
                "Shortcut pressed, but capture is off — start it first."))
            return
        self._on_save()

    def _on_configure_shortcut(self) -> None:
        if self._shortcut is not None and not self._shortcut.configure():
            self._status.setText(_tr(
                "clips_shortcut_no_editor",
                "Your desktop did not offer a shortcut editor."))

    def _update_shortcut_label(self) -> None:
        trigger = self._shortcut.current_trigger() if self._shortcut else None
        if trigger:
            self._shortcut_lbl.setText(
                _tr("clips_shortcut", "Shortcut:") + f"  {trigger}")
        elif self._shortcut is not None and self._shortcut.available:
            # Bound, but the compositor has not reported the combination yet.
            self._shortcut_lbl.setText(_tr(
                "clips_shortcut_pending",
                "Shortcut registered — see your system shortcut settings."))
        else:
            self._shortcut_lbl.setText(_tr(
                "clips_shortcut_none",
                "No global shortcut — this desktop does not offer the "
                "GlobalShortcuts portal."))

    # ── capture control ───────────────────────────────────────────────────────

    def _on_toggle(self) -> None:
        if self._capture is None:
            self._start_capture()
        else:
            self._stop_capture()

    def _start_capture(self) -> None:
        try:
            from arctis_sound_manager.clip_capture import (ClipCapture,
                                                           ClipCaptureUnavailable)
        except Exception as exc:                     # pragma: no cover - env dependent
            self._error = str(exc)
            self._update_status()
            return

        self._status.setText(_tr("clips_starting", "Starting capture…"))
        try:
            capture = ClipCapture(history_s=max(90.0, self._seconds.value() * 2.0))
            capture.start()
        except ClipCaptureUnavailable as exc:
            self._error = str(exc)
            logger.warning("clip capture unavailable: %s", exc)
            self._update_status()
            return
        except Exception as exc:                     # pragma: no cover - env dependent
            self._error = str(exc)
            logger.exception("could not start clip capture")
            self._update_status()
            return

        self._error = None
        self._capture = capture
        self._toggle_btn.setText(_tr("clips_stop", "Stop capture"))
        self._save_btn.setEnabled(True)
        self._update_status()

    def _stop_capture(self) -> None:
        if self._capture is not None:
            try:
                self._capture.stop()
            except Exception:
                logger.exception("error while stopping clip capture")
            self._capture = None
        self._toggle_btn.setText(_tr("clips_start", "Start capture"))
        self._save_btn.setEnabled(False)
        self._update_status()

    def _on_save(self) -> None:
        if self._capture is None:
            return
        path = self._capture.save_clip(float(self._seconds.value()))
        if path is None:
            self._status.setText(_tr(
                "clips_not_ready",
                "Nothing buffered yet — give the capture a few seconds."))
            return
        self.refresh_clips()
        self._status.setText(_tr("clips_saved", "Saved:") + f" {path.name}")
        # The shortcut is pressed while something else owns the screen, so a
        # status line nobody is looking at is not feedback. Sound first, for
        # the same reason every camera makes one.
        try:
            from arctis_sound_manager.clip_feedback import clip_saved
            clip_saved(path)
        except Exception:                            # pragma: no cover - cosmetic
            logger.debug("could not announce the clip", exc_info=True)

    # ── library ───────────────────────────────────────────────────────────────

    def refresh_clips(self) -> None:
        """Rebuild the grid from disk, newest first."""
        selected = self._list.currentItem()
        keep = selected.data(Qt.ItemDataRole.UserRole) if selected else None

        from arctis_sound_manager.clip_library import list_clips

        self._list.clear()
        clips = list_clips(CLIP_DIR)

        self._empty.setVisible(not clips)
        self._open_hint.setVisible(bool(clips))
        if not clips:
            self._update_selection_actions()
            return

        from arctis_sound_manager.clip_thumbs import cache_path

        for clip in clips:
            stat = clip.stat()
            item = QListWidgetItem(
                f"{clip_title(clip)}\n{clip_caption(stat.st_mtime, stat.st_size)}")
            item.setData(Qt.ItemDataRole.UserRole, str(clip))
            item.setToolTip(clip.name)
            item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter |
                                  Qt.AlignmentFlag.AlignTop)
            item.setSizeHint(CARD_SIZE)

            # A cached frame is a file read, so it goes straight in; only a
            # miss costs an ffmpeg run, and that happens off-thread.
            cached = cache_path(clip)
            if cached.exists():
                item.setIcon(QIcon(str(cached)))
            else:
                item.setIcon(_placeholder_icon())
                self._queue_thumbnail(clip)

            self._list.addItem(item)
            if str(clip) == keep:
                self._list.setCurrentItem(item)

        self._update_selection_actions()

        # Frames for clips that are gone (or were re-encoded) are dead weight in
        # the cache; the moment the library is known is the moment to say so.
        try:
            from arctis_sound_manager.clip_thumbs import prune
            prune(clips)
        except Exception:                            # pragma: no cover - cosmetic
            logger.debug("could not prune the thumbnail cache", exc_info=True)

    def _queue_thumbnail(self, clip: Path) -> None:
        """Ask for *clip*'s poster frame, at most once per page lifetime."""
        key = str(clip)
        if key in self._thumb_queued or self._closing:
            return
        self._thumb_queued.add(key)
        self._thumb_pool.start(_ThumbJob(clip, self._thumb_signals))

    def _on_thumb_ready(self, clip_path: str, thumb_path: str) -> None:
        """A frame arrived — put it on its card if that card is still there.

        The grid is rebuilt on every save and every editor close, so by the time
        a frame lands its item may be a different object or gone entirely. The
        clip path is matched rather than an index for that reason.
        """
        if self._closing:
            return
        icon = QIcon(thumb_path)
        if icon.isNull():
            return
        for row in range(self._list.count()):
            item = self._list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == clip_path:
                item.setIcon(icon)
                return

    # ── bulk actions ──────────────────────────────────────────────────────────

    def _selected_clips(self) -> list[Path]:
        return [Path(p) for p in
                (item.data(Qt.ItemDataRole.UserRole)
                 for item in self._list.selectedItems()) if p]

    def _update_selection_actions(self) -> None:
        """Enable only what the current selection can actually do."""
        count = len(self._list.selectedItems())
        self._delete_btn.setEnabled(count > 0)
        # Renaming several files to one name is not a thing.
        self._rename_btn.setEnabled(count == 1)
        self._select_all_btn.setEnabled(self._list.count() > 0)
        self._selection_lbl.setText(
            "" if count == 0 else
            _tr("clips_selected", "{n} selected").replace("{n}", str(count)))

    def _on_select_all(self) -> None:
        self._list.selectAll()
        self._list.setFocus()

    def _on_delete(self) -> None:
        """Delete the selection, after saying exactly what will go.

        The confirmation names the count rather than asking a generic "are you
        sure": on a grid of near-identical cards, how many are selected is the
        thing the user cannot see at a glance.
        """
        clips = self._selected_clips()
        if not clips:
            return

        question = (_tr("clips_delete_one", "Delete “{name}”?")
                    .replace("{name}", clips[0].name) if len(clips) == 1 else
                    _tr("clips_delete_many", "Delete {n} clips?")
                    .replace("{n}", str(len(clips))))
        answer = QMessageBox.question(
            self, _tr("clips_delete", "Delete"),
            question + "\n\n" + _tr(
                "clips_delete_trash",
                "They go to your system trash, so this can be undone there."),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return

        from arctis_sound_manager.clip_library import delete_clips

        gone, failed = delete_clips(clips)
        self.refresh_clips()
        if failed:
            self._status.setText("⚠ " + _tr(
                "clips_delete_failed", "{n} could not be deleted — see the log.")
                .replace("{n}", str(len(failed))))
            logger.warning("could not delete: %s", [p.name for p in failed])
        else:
            self._status.setText(
                _tr("clips_deleted", "Deleted {n}.").replace("{n}", str(gone)))

    def _on_rename(self) -> None:
        """Give a clip a name that means something.

        Renaming moves the sidecars too — the track names and the remembered
        trim are keyed to the filename, and leaving them behind would silently
        cost the clip its per-track labels in the editor.
        """
        clips = self._selected_clips()
        if len(clips) != 1:
            return
        clip = clips[0]

        new_stem, ok = QInputDialog.getText(
            self, _tr("clips_rename", "Rename"),
            _tr("clips_rename_prompt", "New name:"), text=clip.stem)
        new_stem = (new_stem or "").strip()
        if not ok or not new_stem or new_stem == clip.stem:
            return
        if "/" in new_stem or new_stem in (".", ".."):
            self._status.setText("⚠ " + _tr(
                "clips_rename_invalid", "That name cannot be used."))
            return

        target = clip.with_name(new_stem + clip.suffix)
        if target.exists():
            self._status.setText("⚠ " + _tr(
                "clips_rename_taken", "A clip with that name already exists."))
            return

        from arctis_sound_manager.clip_library import sidecars

        try:
            for sidecar, renamed in zip(sidecars(clip), sidecars(target)):
                if sidecar.exists():
                    sidecar.rename(renamed)
            clip.rename(target)
        except OSError as exc:
            logger.warning("could not rename %s: %s", clip.name, exc)
            self._status.setText("⚠ " + _tr(
                "clips_rename_failed", "Could not rename that clip."))
            return
        self.refresh_clips()

    def _on_open_clip(self, item: QListWidgetItem) -> None:
        """Open the editor, not the system player.

        Handing the file to a player is the one thing a clip does not need: the
        reason to open it is to cut it down, quieten a track and send it
        somewhere. The player is still one button away inside the editor.
        """
        path = item.data(Qt.ItemDataRole.UserRole)
        if not path:
            return
        try:
            from arctis_sound_manager.gui.clip_editor import ClipEditor
            editor = ClipEditor(Path(path), self)
            editor.exec()
            self.refresh_clips()     # an export lands next to the original
        except Exception:
            logger.exception("could not open the clip editor")
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    # ── status ────────────────────────────────────────────────────────────────

    def _tick(self) -> None:
        if self._capture is not None:
            self._update_status()

    def _update_status(self) -> None:
        if self._error:
            self._status.setText("⚠ " + self._error)
            return
        if self._capture is None:
            self._status.setText(_tr("clips_idle", "Capture is off."))
            return

        ready = self._capture.ready_s
        game = getattr(self._capture, "_game_label", None)
        if game is None:
            try:
                from arctis_sound_manager.clip_capture import detect_game
                game = detect_game()
            except Exception:
                game = None

        parts = [_tr("clips_buffered", "Buffered:") + f" {ready:.0f}s"]

        # The capture rate is otherwise invisible until a clip is played back,
        # and a pipeline quietly managing a third of the display rate looks like
        # a broken recording rather than a slow encode. Showing it live — with
        # whether the frame is staying on the GPU — makes that diagnosable while
        # it is happening instead of afterwards.
        fps = getattr(self._capture, "fps", 0.0)
        if fps:
            where = getattr(self._capture, "video_path_label", "")
            source = getattr(self._capture, "source_fps", 0.0)
            text = f"{fps:.0f} fps" + (f" ({where})" if where else "")
            # Both numbers, because a recorded 15 fps means opposite things
            # depending on whether the compositor sent 15 or 60: the first is
            # the screencast being slow, the second is this pipeline dropping.
            if source and abs(source - fps) > max(2.0, fps * 0.15):
                text += "  " + _tr("clips_source_fps", "screen: {n} fps").replace(
                    "{n}", f"{source:.0f}")
            parts.append(text)

        if game:
            parts.append(_tr("clips_recording", "Recording:") + f" {game}")
        tracks = [name for name, _ in getattr(self._capture, "audio_tracks", [])]
        if tracks:
            parts.append(_tr("clips_tracks", "Tracks:") + " " + ", ".join(tracks))
        self._status.setText("    ".join(parts))

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def apply_theme(self, t=None) -> None:
        global _PLACEHOLDER
        for widget, key, size in (
            (self._hint, "TEXT_SECONDARY", "9pt"),
            (self._status, "TEXT_SECONDARY", "9pt"),
            (self._empty, "TEXT_SECONDARY", "9pt"),
            (self._open_hint, "TEXT_SECONDARY", "8pt"),
        ):
            widget.setStyleSheet(
                f"color: {_theme.c(key)}; font-size: {size}; background: transparent;")
        # The placeholder card is painted in theme colours, so it is stale now.
        # Cards already showing a real frame are unaffected.
        _PLACEHOLDER = None
        self.refresh_clips()

    def shutdown(self) -> None:
        """Release the capture — the portal session and encoder outlive the
        window otherwise, and a second run then contends for the same node."""
        self._closing = True
        self._timer.stop()
        # Drop what has not started and give a running ffmpeg a moment to end.
        # Without this a thread can come back to a deleted widget as the window
        # closes, which is a crash on the way out rather than a slow exit.
        self._thumb_pool.clear()
        self._thumb_pool.waitForDone(3000)
        self._stop_capture()
        if self._shortcut is not None:
            self._shortcut.close()
            self._shortcut = None


def _format_time(mtime: float) -> str:
    import time
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime))


def open_clip_externally(path: str) -> None:
    """Play *path* in the user's default player, falling back to xdg-open."""
    if not QDesktopServices.openUrl(QUrl.fromLocalFile(path)):
        try:
            subprocess.Popen(["xdg-open", path])
        except OSError:
            logger.warning("no handler could open %s", path)
