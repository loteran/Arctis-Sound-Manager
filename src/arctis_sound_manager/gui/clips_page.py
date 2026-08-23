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
import os
import re
import subprocess
import time
from pathlib import Path

from PySide6.QtCore import (QMimeData, QObject, QPointF, QRunnable, QSize, Qt,
                            QThreadPool, QTimer, QUrl, Signal)
from PySide6.QtGui import (QColor, QDesktopServices, QIcon, QKeySequence,
                           QPainter, QPixmap, QPolygonF, QShortcut)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

import arctis_sound_manager.gui.theme as _theme
from arctis_sound_manager.gui.qt_widgets.q_toggle import QToggle
from arctis_sound_manager.i18n import I18n

logger = logging.getLogger("ClipsPage")

# Resolved on each use rather than frozen at import: the folder is the
# localised video directory (issue #192), and a legacy ~/Videos/ASM Clips
# with recordings in it still wins — see clip_library.clip_dir().
from arctis_sound_manager.clip_library import clip_dir

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

# Mirrors clip_capture.FPS_CHOICES / DEFAULT_FPS, restated rather than imported
# so this page still builds its controls on a machine with no GStreamer — the
# rule the module docstring sets out. test_clip_rate keeps the two in step.
_FPS_CHOICES = (15, 30, 60)
_DEFAULT_FPS = 30


# How often the page asks whether a game is playing, and how long a game has to
# be gone before the capture follows it. The poll is cheap (one PulseAudio round
# trip) and the grace is what keeps a loading screen or a silent cutscene from
# tearing the buffer down mid-session — losing it would cost the user the very
# seconds they came back for.
_GAME_POLL_MS = 5_000
_GAME_GONE_GRACE_S = 45.0


def _autostart_enabled() -> bool:
    try:
        from arctis_sound_manager.settings import GeneralSettings
        return bool(GeneralSettings.read_from_file().clips_autostart)
    except Exception:  # noqa: BLE001 — a broken settings file is not worth the page
        logger.debug("could not read clips_autostart, assuming on", exc_info=True)
        return True


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


def clip_size(size_bytes: int) -> str:
    """A clip's size, in the unit that still says something about it.

    Whole megabytes alone turned every short or heavily-dropped clip into
    "0 MB", which reads as a broken file rather than a small one — a 515 KB clip
    that plays perfectly well looked like nothing had been recorded. Under a
    megabyte the answer is in kilobytes; over it, one decimal until the number
    is big enough not to need it.
    """
    mb = size_bytes / (1024 * 1024)
    if mb < 1:
        return f"{size_bytes / 1024:.0f} KB"
    if mb < 10:
        return f"{mb:.1f} MB"
    return f"{mb:.0f} MB"


def clip_caption(mtime: float, size_bytes: int) -> str:
    """The second line of a card: when it was taken and how big it is."""
    return f"{_format_time(mtime)}   ·   {clip_size(size_bytes)}"


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

    Dragging a card carries the recording itself — a Matroska file, because that
    is what holds one audio track per channel. That is the right payload for a
    file manager or an editor, and the wrong one for Discord, which uploads a
    .mkv and then cannot play it. The editor's export is the route there: it
    writes MP4. The hint under the grid says so rather than leaving it to be
    discovered by posting a clip nobody can watch.

    Qt only offers a drag at all if it carries ``text/uri-list``, so the payload
    is built here rather than relying on the default (which drags the row's text
    and lands as a meaningless string in the target).
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

    # Raised after Clips has been switched off from this page, so the window can
    # put the install screen back without a restart.
    clips_disabled = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._capture = None
        self._error: str | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        # ── title row ─────────────────────────────────────────────────────────
        # The switch belongs with the title, not in the button row underneath:
        # it turns the whole feature off, and sitting next to Start and Save it
        # would read as a third capture control. Until it existed the only way
        # off this page was "Uninstall", which opens with a question about
        # removing ffmpeg — so someone who just wanted the recorder to stop had
        # to walk through a package conversation to get there, and most people
        # reasonably read that as "there is no way to turn this off".
        header = QHBoxLayout()
        header.setSpacing(10)

        title = QLabel(_tr("clips", "Clips"))
        title.setStyleSheet(
            f"color: {_theme.c('TEXT_PRIMARY')}; font-size: 16pt; "
            f"font-weight: bold; background: transparent;")
        header.addWidget(title)
        header.addStretch(1)

        self._power_lbl = QLabel(_tr("clips_power_on", "On"))
        self._power_lbl.setStyleSheet(
            f"color: {_theme.c('TEXT_SECONDARY')}; font-size: 10pt; "
            f"background: transparent;")
        header.addWidget(self._power_lbl)

        # QToggle, the switch the Sonar page uses everywhere, rather than a
        # styled checkbox: this is the same kind of control and it should not
        # be the one place in ASM where an on/off switch looks like something
        # else.
        self._power_switch = QToggle(is_checkbox=True)
        self._power_switch.setChecked(True)
        self._power_switch.setToolTip(_tr(
            "clips_power_tooltip",
            "Turn Clips off. The recording stops and the tab goes back to the "
            "start screen — your saved clips and the packages it uses are left "
            "alone."))
        # toggled(bool) rather than stateChanged(int): stateChanged hands over a
        # Qt.CheckState, and bool(Qt.CheckState.Unchecked) is True — a switch
        # wired to it reads every "off" as an "on".
        self._power_switch.toggled.connect(self._on_power_toggled)
        header.addWidget(self._power_switch)

        root.addLayout(header)

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
        # Two rows on purpose. The old single row ran the two things you press
        # (Start, Save) in among the four you set once and leave alone, so the
        # buttons that matter had no more weight than a spin box. Actions on
        # top, settings underneath, each setting a label and its control in the
        # same shape — which is also what the shortcut row is now, instead of a
        # bare sentence with a button after it.
        actions = QHBoxLayout()
        actions.setSpacing(10)

        self._toggle_btn = QPushButton(_tr("clips_start", "Start capture"))
        self._toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle_btn.clicked.connect(self._on_toggle_clicked)
        actions.addWidget(self._toggle_btn)

        self._save_btn = QPushButton(_tr("clips_save", "Save last seconds"))
        self._save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._save_btn.setEnabled(False)
        self._save_btn.clicked.connect(self._on_save)
        actions.addWidget(self._save_btn)

        actions.addStretch(1)

        # Everything you set once, in one place, out of the way of the two
        # buttons above. A tool button rather than a plain one so the menu opens
        # on a click rather than needing the arrow.
        self._settings_btn = QToolButton()
        self._settings_btn.setText("⚙")
        self._settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._settings_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._settings_btn.setToolTip(_tr("clips_settings", "Capture settings"))
        self._settings_btn.setStyleSheet("QToolButton::menu-indicator { image: none; }")
        actions.addWidget(self._settings_btn)

        self._folder_btn = QPushButton(_tr("clips_open_folder", "Open folder"))
        self._folder_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._folder_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(clip_dir()))))
        actions.addWidget(self._folder_btn)

        # Deliberately not beside Start and Save: it is the one control here
        # that uninstalls software, and it sits at the far end of the row that
        # holds the things you press once, not among the ones you press while
        # playing. It never touches the clips already on disk.
        self._uninstall_btn = QPushButton(_tr("clips_uninstall", "Uninstall"))
        self._uninstall_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._uninstall_btn.setToolTip(_tr(
            "clips_uninstall_tooltip",
            "Switch Clips off and optionally remove the packages it "
            "installed. Your saved clips are left alone."))
        self._uninstall_btn.clicked.connect(self._on_uninstall)
        actions.addWidget(self._uninstall_btn)

        root.addLayout(actions)

        # ── settings, behind a gear ───────────────────────────────────────────
        # These used to be a second row across the page: five labels and five
        # controls that are set once and then never looked at again, carrying
        # the same weight as Start and Save. Behind a gear they stop competing
        # with the two buttons that are actually pressed while playing, and the
        # page gets the room back for the library, which is what people open it
        # for. One row per setting rather than a line of them, because a popup
        # is read down, not across.
        settings_box = QWidget()
        settings = QVBoxLayout(settings_box)
        settings.setContentsMargins(14, 12, 14, 12)
        settings.setSpacing(10)

        def _row(label_key: str, fallback: str, *widgets) -> None:
            row = QHBoxLayout()
            row.setSpacing(8)
            label = QLabel(_tr(label_key, fallback))
            label.setMinimumWidth(96)
            row.addWidget(label)
            for widget in widgets:
                row.addWidget(widget)
            row.addStretch(1)
            settings.addLayout(row)

        self._seconds = QSpinBox()
        self._seconds.setRange(5, 300)
        self._seconds.setValue(30)
        self._seconds.setSuffix(" s")
        _row("clips_length", "Length:", self._seconds)

        # A ceiling, not a target — see clip_capture.FPS_CHOICES. It is offered
        # because it decides the keyframe interval and the encoder's budget, and
        # locked while capturing because both are fixed when the pipeline is
        # built: changing it live would mean tearing the capture down, and the
        # buffer with it.
        self._fps = QComboBox()
        for value in _FPS_CHOICES:
            self._fps.addItem(f"{value} fps", value)
        self._fps.setCurrentIndex(max(0, _FPS_CHOICES.index(_DEFAULT_FPS)))
        self._fps.setToolTip(_tr(
            "clips_fps_hint",
            "The most this will record. The screen is only captured when it "
            "changes, so the real rate is usually lower — a clip can be set to "
            "an exact rate when you export it."))
        _row("clips_fps", "Frame rate:", self._fps)

        # What is being captured cannot be *shown* — the choice lives in the
        # portal and Wayland never tells the app what was picked — so this
        # offers the only honest thing: the way back to the picker.
        self._source_btn = QPushButton(_tr("clips_change_source", "Change…"))
        self._source_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._source_btn.setToolTip(_tr(
            "clips_source_hint",
            "Ask again which screen or window to record. The picker is shown "
            "once and the answer is remembered, so this is the way to change "
            "it."))
        self._source_btn.clicked.connect(self._on_change_source)
        _row("clips_source", "Capture:", self._source_btn)

        # Where clips land. Shown rather than assumed: the default follows the
        # desktop's own video folder, whatever it is called in the user's
        # language, so "the usual place" is not a thing anyone can point at
        # from memory (#192).
        self._folder_lbl = QLabel("")
        self._folder_lbl.setStyleSheet(
            f"color: {_theme.c('TEXT_SECONDARY')}; font-size: 9pt; "
            f"background: transparent;")
        self._folder_btn_change = QPushButton(_tr("clips_change_folder", "Change…"))
        self._folder_btn_change.setCursor(Qt.CursorShape.PointingHandCursor)
        self._folder_btn_change.setToolTip(_tr(
            "clips_folder_hint",
            "Choose where clips are saved. Clips already recorded stay where "
            "they are — only new ones go to the new folder."))
        self._folder_btn_change.clicked.connect(self._on_change_folder)
        self._folder_reset_btn = QPushButton(_tr("clips_reset_folder", "Default"))
        self._folder_reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._folder_reset_btn.setToolTip(_tr(
            "clips_reset_folder_hint",
            "Go back to your desktop's video folder."))
        self._folder_reset_btn.clicked.connect(self._on_reset_folder)
        _row("clips_folder", "Folder:", self._folder_lbl,
             self._folder_btn_change, self._folder_reset_btn)

        self._shortcut_lbl = QLabel("")
        self._shortcut_lbl.setStyleSheet(
            f"color: {_theme.c('TEXT_SECONDARY')}; font-size: 9pt; "
            f"background: transparent;")
        self._shortcut_btn = QPushButton(_tr("clips_change_shortcut", "Change…"))
        self._shortcut_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._shortcut_btn.setEnabled(False)
        self._shortcut_btn.clicked.connect(self._on_configure_shortcut)
        _row("clips_shortcut", "Shortcut:", self._shortcut_lbl, self._shortcut_btn)

        # Left on, because someone who installed a clip recorder wants the last
        # thirty seconds of the game they just started, and a buffer that has to
        # be armed by hand is armed after the moment worth keeping. It is a
        # switch rather than a rule for the people who would rather decide
        # themselves when their screen is being read.
        self._autostart = QCheckBox(_tr(
            "clips_autostart", "Capture automatically while a game is running"))
        self._autostart.setToolTip(_tr(
            "clips_autostart_hint",
            "Starts the buffer when a game starts playing audio and stops it "
            "when the game is gone — a capture nothing is using costs CPU and "
            "battery for a recording no one will ask for."))
        self._autostart.setChecked(_autostart_enabled())
        self._autostart.toggled.connect(self._on_autostart_toggled)
        settings.addWidget(self._autostart)

        settings.addStretch(1)

        self._settings_menu = QMenu(self)
        holder = QWidgetAction(self._settings_menu)
        holder.setDefaultWidget(settings_box)
        self._settings_menu.addAction(holder)

        self._settings_btn.setMenu(self._settings_menu)

        self._status = QLabel("")
        self._status.setStyleSheet(
            f"color: {_theme.c('TEXT_SECONDARY')}; font-size: 9pt; "
            f"background: transparent;")
        root.addWidget(self._status)

        # ── library ───────────────────────────────────────────────────────────
        library_bar = QHBoxLayout()
        library_bar.setSpacing(8)

        self._select_all_btn = QPushButton(_tr("clips_select_all", "Select all"))
        self._select_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._select_all_btn.clicked.connect(self._on_select_all)
        library_bar.addWidget(self._select_all_btn)

        # Select all had no counterpart. Clicking empty space clears a
        # selection in a file manager, but this grid fills its width with
        # cards and often leaves no empty space to click — so the way out of a
        # selection was to ctrl-click every card back off.
        self._select_none_btn = QPushButton(_tr("clips_select_none", "Deselect all"))
        self._select_none_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._select_none_btn.clicked.connect(self._on_select_none)
        library_bar.addWidget(self._select_none_btn)

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
        # Right-click, because that is where everyone looks first for what to do
        # with a file. The buttons above stay: they are how the actions are
        # discovered at all, and they show at a glance what a selection can do.
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._on_context_menu)
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
            "selected, and Export writes an MP4 you can post anywhere. Dragging "
            "a card straight out gives the original recording, which Discord "
            "uploads but cannot play."))
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

        # Following the game runs on its own, slower timer: detect_game() is a
        # PulseAudio round trip and the second-by-second one above is for the
        # buffer read-out.
        self._auto_started = False
        self._game_gone_since: float | None = None
        self._last_detected_game: str | None = None
        # Which game an automatic start already failed for, so the 5 s poll
        # does not relaunch it forever (#204).
        self._autostart_failed_for: str | None = None
        self._game_timer = QTimer(self)
        self._game_timer.setInterval(_GAME_POLL_MS)
        self._game_timer.timeout.connect(self._poll_game)
        self._game_timer.start()

        self._shortcut = None
        self._bind_shortcut()
        self._update_folder_label()
        self.refresh_clips()
        self._update_status()
        # Asked once at startup as well as on the timer: a page opened while a
        # game is already running should arm the buffer now, not in five
        # seconds.
        QTimer.singleShot(0, self._poll_game)

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
            # Just the combination: the row it sits in is already labelled
            # "Shortcut:", and repeating it read as "Shortcut: Shortcut: Alt+F".
            self._shortcut_lbl.setText(str(trigger))
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

    def _on_toggle_clicked(self) -> None:
        """The button. Clears the autostart backoff: asking again by hand is
        exactly the signal that a retry is wanted."""
        self._autostart_failed_for = None
        self._on_toggle()

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
            capture = ClipCapture(history_s=max(90.0, self._seconds.value() * 2.0),
                                  fps=int(self._fps.currentData() or _DEFAULT_FPS))
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
        # The rate is baked into the pipeline (keyframe interval, encoder
        # budget), so it can only change between captures.
        self._fps.setEnabled(False)
        self._update_status()

    # ── where clips are saved ─────────────────────────────────────────────────

    def _update_folder_label(self) -> None:
        """Show the folder in use, and offer "Default" only when it can do
        something — a button that resets what is already the default is noise."""
        from arctis_sound_manager.clip_library import clip_dir as _dir
        from arctis_sound_manager.clip_library import configured_clip_dir

        current = str(_dir())
        # Elided from the left: the tail (…/Vidéos/ASM Clips) is what tells the
        # folders apart, while the leading /home/<user> is the same every time.
        metrics = self._folder_lbl.fontMetrics()
        self._folder_lbl.setText(
            metrics.elidedText(current, Qt.TextElideMode.ElideLeft, 260))
        self._folder_lbl.setToolTip(current)
        self._folder_reset_btn.setVisible(configured_clip_dir() is not None)

    def _on_change_folder(self) -> None:
        """Pick a new folder for clips to be saved into.

        Existing clips are deliberately left where they are. Moving them would
        mean moving their sidecars too (trim, mix, tracks), across filesystems,
        while a capture may be writing — and a user who wants their library
        moved can move it, whereas one who does not cannot undo it.
        """
        from arctis_sound_manager.clip_library import clip_dir as _dir

        chosen = QFileDialog.getExistingDirectory(
            self, _tr("clips_pick_folder", "Where should clips be saved?"),
            str(_dir()))
        if not chosen:
            return

        target = Path(chosen)
        if not os.access(target, os.W_OK):
            # Caught here rather than at the moment a clip is saved: that
            # moment is the one where the recording is lost.
            QMessageBox.warning(
                self, _tr("clips_folder_unwritable_title", "Cannot write there"),
                _tr("clips_folder_unwritable",
                    "ASM cannot write to that folder, so clips would fail to "
                    "save. Pick another one."))
            return

        self._set_clips_directory(str(target))

    def _on_reset_folder(self) -> None:
        self._set_clips_directory(None)

    def _set_clips_directory(self, value: str | None) -> None:
        try:
            from arctis_sound_manager.settings import GeneralSettings
            settings = GeneralSettings.read_from_file()
            settings.clips_directory = value
            settings.write_to_file()
        except Exception:  # noqa: BLE001
            logger.warning("could not persist clips_directory", exc_info=True)
            QMessageBox.warning(
                self, _tr("clips_folder_save_failed_title", "Not saved"),
                _tr("clips_folder_save_failed",
                    "The new folder could not be saved to your settings."))
            return
        self._update_folder_label()
        # The library on screen is the old folder's — reload so the page shows
        # what the new one holds rather than clips it will no longer write to.
        self.refresh_clips()

    def _on_change_source(self) -> None:
        """Bring the portal picker back, so what is captured can be re-chosen.

        The picker appears once and never again: the portal is asked to persist
        the choice and the saved token is replayed on every later start, which
        is what stops a rolling capture from prompting each time it rebuilds
        its pipeline. The cost is that the first answer was permanent — a user
        who picked the wrong monitor, or picked a window and later wanted the
        whole screen, had no way back short of `asm-clipd --forget` in a
        terminal.

        Dropping the token is all it takes; the next open() asks again. If a
        capture is running it is restarted here rather than at some later
        moment the user is not watching for, because a picker that appears
        unprompted twenty minutes on is worse than one that appears now.
        """
        from arctis_sound_manager.clip_capture import ScreenCastPortal

        ScreenCastPortal.forget()

        if self._capture is None:
            self._status.setText(_tr(
                "clips_source_forgotten",
                "You will be asked what to capture when you start again."))
            return

        try:
            self._capture.restart()
        except Exception as exc:
            # Most likely the picker was cancelled. The old session is already
            # closed by then, so there is no capture left to go back to — say
            # so plainly rather than leaving a Stop button over a dead pipeline.
            logger.warning("could not re-open the capture source: %s", exc)
            self._error = str(exc)
            self._stop_capture()
            return

        self._error = None
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
        self._fps.setEnabled(True)
        self._update_status()

    # ── following the game ────────────────────────────────────────────────────

    def _on_autostart_toggled(self, on: bool) -> None:
        try:
            from arctis_sound_manager.settings import GeneralSettings
            settings = GeneralSettings.read_from_file()
            settings.clips_autostart = bool(on)
            settings.write_to_file()
        except Exception:  # noqa: BLE001
            logger.warning("could not persist clips_autostart", exc_info=True)
        self._game_gone_since = None
        if on:
            self._poll_game()

    def _poll_game(self) -> None:
        """Start the capture when a game shows up, drop it when the game goes.

        A rolling buffer is only useful if it is already running when something
        worth keeping happens, and the thing people forget is arming it. The
        game is found the same way a clip is labelled — `detect_game()`, which
        asks what the user routed to the Game channel before it guesses — so
        this needs no list of titles to maintain.

        Stopping matters as much as starting: a capture with no game behind it
        holds a screen's worth of frames in memory and keeps an encoder busy for
        a recording nobody is going to ask for. It is not immediate, though. A
        game goes quiet for a loading screen or a cutscene, and tearing the
        pipeline down there would throw away the buffer and take a portal
        prompt to rebuild — so silence has to last `_GAME_GONE_GRACE_S` first.

        Only the capture this started is stopped. Someone who pressed Start
        themselves gets to decide when it ends.
        """
        if self._closing:
            return

        try:
            from arctis_sound_manager.clip_capture import detect_game
            game = detect_game()
        except Exception:  # noqa: BLE001 — a probe failure is not worth the page
            logger.debug("could not look for a game", exc_info=True)
            return

        # Remembered for _update_status(), which used to call detect_game()
        # itself on the one-second timer — a PulseAudio round trip per second,
        # for a label that changes when a game starts (GUI-1). The detection
        # runs here, on the slow timer that exists for exactly this, whether or
        # not autostart is on: the label is shown either way.
        self._last_detected_game = game

        if not self._autostart.isChecked():
            return

        if game:
            self._game_gone_since = None
            if self._capture is None:
                # One failed autostart is a reason to stop, not to try again in
                # five seconds. Each attempt asks the portal for a screencast
                # and spawns an encoder, so a cause that is not going away —
                # a clips folder on a disconnected drive (issue #204), a
                # missing dependency, a refused portal — turned this timer into
                # a machine for launching capture processes for as long as the
                # game ran. Retry when something has actually changed: another
                # game, or the user pressing the button.
                if self._autostart_failed_for == game:
                    return
                logger.info("clips: '%s' is playing — starting the capture", game)
                self._on_toggle()
                if self._capture is not None:
                    self._auto_started = True
                    self._autostart_failed_for = None
                else:
                    self._autostart_failed_for = game
                    logger.warning(
                        "clips: could not autostart the capture for '%s' (%s) — "
                        "not retrying until the game changes or you start it "
                        "yourself", game, self._error or "no reason given",
                    )
            return

        if self._capture is None or not self._auto_started:
            return

        now = time.monotonic()
        if self._game_gone_since is None:
            self._game_gone_since = now
            return
        if now - self._game_gone_since < _GAME_GONE_GRACE_S:
            return

        logger.info("clips: no game for %.0fs — stopping the capture",
                    _GAME_GONE_GRACE_S)
        self._game_gone_since = None
        self._auto_started = False
        self._stop_capture()

    def _on_power_toggled(self, on: bool) -> None:
        """The switch in the title row: off stops recording and puts the tab
        back to the start screen, and does nothing else.

        Deliberately not the Uninstall path. Turning a recorder off is an
        everyday thing; being asked whether ffmpeg should leave the machine is
        not, and while the two shared a button there was no way to do the first
        without answering for the second. The packages stay, the saved clips
        stay, and the start screen the tab falls back to offers Enable — so
        this is one switch with two faces, not a one-way door.

        Only the off direction is acted on: the switch is created checked on a
        page that only exists while Clips is on, so an "on" here is the initial
        state being set, not a user asking for anything.
        """
        if on:
            return

        from arctis_sound_manager.gui import clips_setup

        self._power_lbl.setText(_tr("clips_power_off", "Off"))
        self._stop_capture()
        clips_setup.set_enabled(False)
        # The window swaps this page out for the start screen, which is also
        # what releases the portal session and the global shortcut.
        self.clips_disabled.emit()

    def _on_uninstall(self) -> None:
        """Switch Clips off from the tab, and offer to remove its packages.

        The recording is stopped first: leaving a capture running for a feature
        the user has just switched off would keep the screen-share indicator up
        with nothing on screen to explain it. The conversation itself lives in
        `clips_setup.confirm_and_remove`, which the install screen asks too.
        """
        from arctis_sound_manager.gui import clips_setup

        # Stopped after the confirmation, not before it: calling the uninstall
        # off should leave the recording exactly as it was found. A pipeline
        # already running keeps the libraries it loaded, so a package removal
        # in between costs nothing.
        if clips_setup.confirm_and_remove(self):
            self._stop_capture()
            self.clips_disabled.emit()

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
        # The rate the file ended up with, not the live one — it is the number
        # that will be argued about after watching the clip back, and saying it
        # here is cheaper than having to open the file to find out.
        written = getattr(self._capture, "last_clip_fps", 0.0)
        rate = f"  ({written:.0f} fps)" if written else ""
        self._status.setText(_tr("clips_saved", "Saved:") + f" {path.name}{rate}")
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
        """Rebuild the grid from disk, newest first.

        Three things are carried across the rebuild, because the grid is thrown
        away and built again on every save, delete and editor close, and losing
        any of them is felt immediately:

        * **Where the list was scrolled to.** Deleting the fourth clip from the
          bottom of a long library used to jump back to the top, so deleting
          several in a row meant scrolling back down after each one.
        * **Which clip was selected**, when it is still there.
        * **The place in the list**, when it is not — the clip that took the
          deleted one's position is selected instead, so the next Delete is
          already aimed. Only when something was selected to begin with:
          refreshing after a save must not select anything on its own.
        """
        selected = self._list.currentItem()
        keep = selected.data(Qt.ItemDataRole.UserRole) if selected else None
        keep_row = self._list.row(selected) if selected else -1
        scroll = self._list.verticalScrollBar().value()

        from arctis_sound_manager.clip_library import list_clips

        self._list.clear()
        clips = list_clips(clip_dir())

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

        if keep is not None and self._list.currentItem() is None and self._list.count():
            # The selected clip is gone — deleted, renamed, or moved away.
            # Whatever now sits in its place is what the user is looking at.
            self._list.setCurrentRow(min(keep_row, self._list.count() - 1))

        # Restored after any selection change, because selecting an item scrolls
        # to it and would otherwise undo this — and then again once the event
        # loop has run, because an icon view lays its cards out lazily: the
        # scrollbar's range is still the old one at this point, so a position
        # near the end gets clamped to a maximum that is about to grow.
        def _restore_scroll(value: int = scroll) -> None:
            bar = self._list.verticalScrollBar()
            bar.setValue(min(value, bar.maximum()))

        _restore_scroll()
        QTimer.singleShot(0, _restore_scroll)

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
        # Nothing selected is already the state this would produce.
        self._select_none_btn.setEnabled(count > 0)
        self._selection_lbl.setText(
            "" if count == 0 else
            _tr("clips_selected", "{n} selected").replace("{n}", str(count)))

    @staticmethod
    def context_actions(selected: int) -> list[tuple[str, str, bool]]:
        """What the right-click menu offers for *selected* clips.

        ``(handler name, label, enabled)`` per entry, with ``""`` for a
        separator. Kept apart from the menu it fills so it can be tested without
        building the page — constructing ClipsPage registers a global shortcut
        through the desktop portal, which is a real request to the compositor of
        whoever runs the suite.
        """
        if selected == 0:
            # Nothing under the cursor: the only thing left to offer is the
            # folder itself.
            return [("folder", _tr("clips_open_folder", "Open folder"), True)]

        one = selected == 1
        return [
            # Opening several clips at once would be several editors, and
            # renaming several to one name is not a thing — so both are shown
            # and disabled rather than hidden, which would make the menu change
            # shape depending on how much is selected.
            ("open", _tr("clips_open", "Open"), one),
            ("rename", _tr("clips_rename_menu", "Rename…"), one),
            ("", "", True),
            ("delete",
             _tr("clips_delete_one_menu", "Delete") if one else
             _tr("clips_delete_many_menu", "Delete {n} clips")
             .replace("{n}", str(selected)), True),
            ("", "", True),
            ("folder", _tr("clips_open_folder", "Open folder"), True),
        ]

    def _on_context_menu(self, point) -> None:
        """The right-click menu for a clip, or for the empty space around them.

        Right-clicking a card that is not in the current selection selects it
        first, and does not clear a selection the click landed inside. That is
        how every file manager behaves, and getting it wrong is how a menu
        deletes the wrong five files: without it, right-clicking one card while
        five are selected would act on the five.

        Rename asks for exactly one clip, so it is offered only when one is
        selected rather than silently renaming the first of many.
        """
        item = self._list.itemAt(point)
        if item is not None and not item.isSelected():
            self._list.setCurrentItem(item)

        handlers = {
            "open": lambda: self._on_open_clip(self._list.selectedItems()[0]),
            "rename": self._on_rename,
            "delete": self._on_delete,
            "folder": lambda: QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(clip_dir()))),
        }

        menu = QMenu(self._list)
        for name, label, enabled in self.context_actions(len(self._selected_clips())):
            if not name:
                menu.addSeparator()
                continue
            action = menu.addAction(label)
            action.setEnabled(enabled)
            action.triggered.connect(handlers[name])

        menu.exec(self._list.viewport().mapToGlobal(point))

    def _on_select_all(self) -> None:
        self._list.selectAll()
        self._list.setFocus()

    def _on_select_none(self) -> None:
        self._list.clearSelection()
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
        # Never probe from here: this runs once a second (GUI-1). The label is
        # the capture's own when it has one, and otherwise whatever the slow
        # game poll last saw.
        game = getattr(self._capture, "_game_label", None)
        if game is None:
            game = self._last_detected_game

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
            # The live rate is a few seconds wide; a clip averages over its whole
            # length, idle stretches included. Reported alone, the live number
            # promises a smoothness the saved file does not have — which is
            # exactly how a status bar reading 20 fps produced a 12 fps clip with
            # nothing on screen to explain it.
            buffered = getattr(self._capture, "buffered_fps", 0.0)
            if buffered and abs(buffered - fps) > max(2.0, fps * 0.15):
                text += "  " + _tr("clips_clip_fps", "clip: {n} fps").replace(
                    "{n}", f"{buffered:.0f}")
            parts.append(text)

        # "Game", never "Recording": this name comes from the audio graph, not
        # from the screen. What is being captured was chosen in the portal picker
        # and Wayland never says what it was, so calling it "Recording:" told
        # anyone who picked a window that the capture was pointed elsewhere.
        if game:
            parts.append(_tr("clips_game", "Game:") + f" {game}")
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
        self._power_lbl.setStyleSheet(
            f"color: {_theme.c('TEXT_SECONDARY')}; font-size: 10pt; "
            f"background: transparent;")
        # The placeholder card is painted in theme colours, so it is stale now.
        # Cards already showing a real frame are unaffected.
        _PLACEHOLDER = None
        self.refresh_clips()

    def shutdown(self) -> None:
        """Release the capture — the portal session and encoder outlive the
        window otherwise, and a second run then contends for the same node."""
        self._closing = True
        self._timer.stop()
        # The game poll outlived the page: it is a PulseAudio round trip every
        # five seconds, for a widget nobody is looking at any more (GUI-1).
        self._game_timer.stop()
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
