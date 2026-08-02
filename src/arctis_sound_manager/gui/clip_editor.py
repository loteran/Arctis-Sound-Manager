# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Clip editor: preview, trim, per-track levels, and a share-sized export.

Opening a clip used to hand it to the system video player, which is the one
thing that cannot be done with it — the reason to open a clip is to cut it
down, quieten the microphone and send it somewhere. All of that lives here, and
the result is dragged straight out of the dialog into whatever it is going to.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

import arctis_sound_manager.gui.theme as _theme
from arctis_sound_manager.clip_export import (ExportPlan, TrackMix, duration_s,
                                             export, probe_tracks,
                                             video_bitrate_kbps)
from arctis_sound_manager.clip_library import (export_destination, read_trim,
                                               share_dir, write_trim)
from arctis_sound_manager.gui.trim_band import (DEFAULT_TAIL_S, LENGTH_PRESETS_S,
                                                TrimBand)
from arctis_sound_manager.i18n import I18n

logger = logging.getLogger("ClipEditor")

# Offered share sizes. "Original" keeps the recording untouched (a stream copy),
# which is both instant and lossless — the right default when the clip is going
# somewhere without a limit.
SIZE_CHOICES: list[tuple[str, float | None]] = [
    ("Original quality", None),
    ("10 MB", 10.0),
    ("25 MB", 25.0),
    ("50 MB", 50.0),
    ("100 MB", 100.0),
]


# Below this the rows stop fitting side by side: the trim band, its five preset
# buttons and the span read-out share one line, as do the size picker and the
# export buttons. Qt compresses past a layout's wishes but not past this.
MIN_SIZE = QSize(920, 660)

# What it opens at when the screen allows. The preview is the point of the
# dialog and it was arriving letterboxed into a third of the window.
PREFERRED_SIZE = QSize(1180, 860)


def _tr(key: str, fallback: str) -> str:
    try:
        value = I18n.translate("ui", key)
    except Exception:
        return fallback
    return fallback if not value or value == key else value


def _opening_size(available: QSize | None = None) -> QSize:
    """The preferred size, shrunk to fit the screen it will open on.

    A dialog larger than the display cannot be moved back into view on some
    compositors, so the preference is a ceiling rather than a demand.
    """
    if available is None:
        screen = QGuiApplication.primaryScreen()
        available = screen.availableSize() if screen else PREFERRED_SIZE
    return QSize(min(PREFERRED_SIZE.width(), max(MIN_SIZE.width(),
                                                 int(available.width() * 0.9))),
                 min(PREFERRED_SIZE.height(), max(MIN_SIZE.height(),
                                                  int(available.height() * 0.9))))


def _mmss(seconds: float) -> str:
    seconds = max(0.0, seconds)
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}"


class _ExportWorker(QThread):
    """Runs ffmpeg off the UI thread so the dialog stays responsive."""

    done = Signal(object, str)      # (path | None, error message)

    def __init__(self, plan: ExportPlan, parent=None):
        super().__init__(parent)
        self._plan = plan

    def run(self) -> None:
        try:
            result = export(self._plan)
        except ValueError as exc:       # target too small to be watchable
            self.done.emit(None, str(exc))
            return
        except Exception as exc:
            logger.exception("export failed")
            self.done.emit(None, str(exc))
            return
        self.done.emit(result, "" if result else _tr(
            "clip_export_failed", "Export failed — see the log for details."))


class ClipEditor(QDialog):
    """Trim a clip, set its track levels, and export it at a share size."""

    def __init__(self, path: Path, parent=None):
        super().__init__(parent)
        self._path = path
        self._duration = duration_s(path)
        self._exported: Path | None = None
        self._worker: _ExportWorker | None = None
        self._seeked_to_start = False

        self.setWindowTitle(f"{_tr('clips_edit', 'Edit clip')} — {path.name}")
        # The minimum is what every row needs side by side. Below it the trim
        # band was squeezed under its own preset buttons and the read-out ran
        # into them — a dialog that can be resized into an unreadable state is
        # a dialog that will be.
        self.setMinimumSize(MIN_SIZE)
        self.resize(_opening_size())

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        root.addWidget(self._build_preview(), stretch=1)
        root.addWidget(self._build_trim())
        root.addWidget(self._build_tracks())
        root.addLayout(self._build_export_row())

        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setStyleSheet(
            f"color: {_theme.c('TEXT_SECONDARY')}; font-size: 9pt; "
            f"background: transparent;")
        root.addWidget(self._status)

        self._update_estimate()

    # ── preview ───────────────────────────────────────────────────────────────

    def _build_preview(self) -> QWidget:
        """A player when Qt Multimedia is present, a hint when it is not.

        The preview is the least essential part of the dialog: trimming and
        levels are still usable without it, so a missing multimedia backend
        must not stop the editor from opening.
        """
        try:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
            from PySide6.QtMultimediaWidgets import QVideoWidget
        except ImportError:
            hint = QLabel(_tr("clip_no_preview",
                              "Preview unavailable (Qt Multimedia not installed) — "
                              "trimming and levels still work."))
            hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
            hint.setWordWrap(True)
            hint.setStyleSheet(f"color: {_theme.c('TEXT_SECONDARY')};")
            return hint

        video = QVideoWidget()
        video.setMinimumHeight(260)
        self._player = QMediaPlayer(self)
        self._audio_out = QAudioOutput(self)
        self._player.setAudioOutput(self._audio_out)
        self._player.setVideoOutput(video)
        self._player.setSource(QUrl.fromLocalFile(str(self._path)))

        # Setting a source only loads it — the widget stays black until frames
        # are decoded, so the editor opened onto an empty rectangle and the trim
        # sliders had nothing to aim at. Playing and immediately pausing renders
        # the first frame without the clip starting up on its own.
        self._player.play()
        self._player.pause()

        controls = QWidget()
        row = QHBoxLayout(controls)
        row.setContentsMargins(0, 0, 0, 0)
        self._play_btn = QPushButton("▶")
        self._play_btn.setFixedWidth(44)
        self._play_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._play_btn.clicked.connect(self._toggle_play)
        self._seek = QSlider(Qt.Orientation.Horizontal)
        self._seek.sliderMoved.connect(self._player.setPosition)
        self._player.positionChanged.connect(self._on_position)
        self._player.durationChanged.connect(self._on_media_duration)
        row.addWidget(self._play_btn)
        row.addWidget(self._seek, stretch=1)

        wrapper = QWidget()
        col = QVBoxLayout(wrapper)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(6)
        col.addWidget(video, stretch=1)
        col.addWidget(controls)
        return wrapper

    def _toggle_play(self) -> None:
        if self._is_playing():
            self._player.pause()
            self._play_btn.setText("▶")
            return
        # Play the selection. Starting from wherever the playhead was left —
        # usually outside the trim — plays the part being thrown away.
        band = getattr(self, "_band", None)
        if band is not None and not (band.start_s <= self._player.position() / 1000.0
                                     < band.end_s):
            self._player.setPosition(int(band.start_s * 1000))
        self._player.play()
        self._play_btn.setText("⏸")

    def _on_position(self, ms: int) -> None:
        if not self._seek.isSliderDown():
            self._seek.setValue(ms)
        band = getattr(self, "_band", None)
        if band is not None:
            band.set_position(ms / 1000.0)
        # Preview what will be exported, not what is on disk: playback stops at
        # the out point and returns to the in point. Running on past the trim
        # means the preview never shows the clip being made.
        if band is not None and self._is_playing() and ms >= band.end_s * 1000:
            self._player.pause()
            self._play_btn.setText("▶")
            self._player.setPosition(int(band.start_s * 1000))

    def _is_playing(self) -> bool:
        from PySide6.QtMultimedia import QMediaPlayer as _QMP
        player = getattr(self, "_player", None)
        return (player is not None
                and player.playbackState() == _QMP.PlaybackState.PlayingState)

    # ── trim ──────────────────────────────────────────────────────────────────

    def _build_trim(self) -> QWidget:
        """The band, its quick lengths, and the span read-out.

        The band opens on the clip's last seconds (see ``trim_band``), so the
        editor is already framed on the part worth sharing and Export can be
        pressed without touching anything.
        """
        box = QWidget()
        col = QVBoxLayout(box)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(6)

        self._band = TrimBand(self._duration)
        # Below this the band paints its handles and end times into each other.
        self._band.setMinimumWidth(360)
        # A span chosen last time beats the default tail: reopening a clip to
        # adjust an export used to discard the trim that was just made.
        remembered = read_trim(self._path)
        if remembered is not None and remembered[1] <= self._duration + 0.5:
            self._band.set_range(*remembered)
        self._band.setToolTip(
            _tr("clip_trim_hint",
                "Opens on the last {n} seconds — drag the markers to change it.")
            .replace("{n}", f"{DEFAULT_TAIL_S:.0f}"))
        self._band.rangeChanged.connect(self._on_trim_changed)
        self._band.scrubbed.connect(self._on_scrub)
        col.addWidget(self._band)

        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(QLabel(_tr("clip_trim", "Trim:")))
        for seconds in LENGTH_PRESETS_S:
            row.addWidget(self._preset_button(f"{seconds:.0f}s",
                                              lambda _=False, s=seconds:
                                              self._band.select_last(s)))
        row.addWidget(self._preset_button(_tr("clip_trim_all", "All"),
                                          lambda _=False: self._band.select_all()))
        row.addStretch(1)

        self._trim_label = QLabel("")
        self._trim_label.setMinimumWidth(140)
        self._trim_label.setAlignment(Qt.AlignmentFlag.AlignRight
                                      | Qt.AlignmentFlag.AlignVCenter)
        self._trim_label.setStyleSheet(f"color: {_theme.c('TEXT_SECONDARY')};")
        row.addWidget(self._trim_label)

        col.addLayout(row)
        return box

    def _preset_button(self, text: str, on_click) -> QPushButton:
        button = QPushButton(text)
        button.setFixedHeight(24)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setStyleSheet(
            f"QPushButton {{ background: {_theme.c('BG_BUTTON')}; border: none; "
            f"border-radius: 4px; padding: 2px 10px; font-size: 9pt; "
            f"color: {_theme.c('TEXT_SECONDARY')}; }}"
            f"QPushButton:hover {{ background: {_theme.c('BG_BUTTON_HOVER')}; "
            f"color: {_theme.c('TEXT_PRIMARY')}; }}")
        button.clicked.connect(on_click)
        return button

    def _on_trim_changed(self, *_args) -> None:
        self._update_estimate()

    def _on_scrub(self, seconds: float) -> None:
        player = getattr(self, "_player", None)
        if player is not None:
            player.setPosition(int(seconds * 1000))

    def _on_media_duration(self, ms: int) -> None:
        """Take the player's length over ffprobe's when they disagree.

        ffprobe reads the container header; a clip written straight out of the
        rolling buffer can carry a duration that is short of what actually
        decodes. Whichever number is used has to be the one the band is drawn
        against, or the end marker sits somewhere other than the end.
        """
        self._seek.setRange(0, max(1, ms))
        if not hasattr(self, "_band"):      # duration can land mid-construction
            return
        length = ms / 1000.0
        if length > 0 and abs(length - self._duration) > 0.25:
            self._duration = length
            self._band.set_duration(length)
            # set_duration re-selects the default tail against the corrected
            # length, which would throw away a span restored from disk moments
            # earlier — put it back once the real length is known.
            remembered = read_trim(self._path)
            if remembered is not None and remembered[1] <= length + 0.5:
                self._band.set_range(*remembered)
        # Show the first frame of the selection rather than the first frame of
        # the recording: the preview should open on what is about to be shared.
        if not self._seeked_to_start and self._band.start_s > 0:
            self._seeked_to_start = True
            self._player.setPosition(int(self._band.start_s * 1000))

    @property
    def _start_s(self) -> float:
        return self._band.start_s

    @property
    def _end_s(self) -> float:
        return self._band.end_s

    # ── tracks ────────────────────────────────────────────────────────────────

    def _build_tracks(self) -> QWidget:
        box = QWidget()
        col = QVBoxLayout(box)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(4)

        names = probe_tracks(self._path)
        self._track_rows: list[tuple[str, QSlider, QCheckBox]] = []
        if not names:
            col.addWidget(QLabel(_tr("clip_no_tracks", "No audio tracks found.")))
            return box

        col.addWidget(QLabel(_tr("clip_tracks", "Audio tracks:")))
        for name in names:
            row = QHBoxLayout()
            label = QLabel(name)
            label.setMinimumWidth(110)
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(0, 150)
            slider.setValue(100)
            slider.valueChanged.connect(self._update_estimate)
            mute = QCheckBox(_tr("clip_mute", "Mute"))
            mute.toggled.connect(self._update_estimate)

            row.addWidget(label)
            row.addWidget(slider, stretch=1)
            row.addWidget(mute)
            col.addLayout(row)
            self._track_rows.append((name, slider, mute))
        return box

    def _tracks(self) -> list[TrackMix]:
        return [TrackMix(name=n, volume=s.value() / 100.0, muted=m.isChecked())
                for n, s, m in getattr(self, "_track_rows", [])]

    # ── export ────────────────────────────────────────────────────────────────

    def _build_export_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)

        row.addWidget(QLabel(_tr("clip_size", "Share size:")))
        self._size = QComboBox()
        for label, mb in SIZE_CHOICES:
            self._size.addItem(label, mb)
        self._size.currentIndexChanged.connect(self._update_estimate)
        row.addWidget(self._size)

        row.addStretch(1)

        self._open_btn = QPushButton(_tr("clip_open_player", "Open in player"))
        self._open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._open_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._path))))
        row.addWidget(self._open_btn)

        self._export_btn = QPushButton(_tr("clip_export", "Export"))
        self._export_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._export_btn.setDefault(True)
        self._export_btn.clicked.connect(self._on_export)
        row.addWidget(self._export_btn)
        return row

    def _update_estimate(self) -> None:
        """Say up front whether the chosen size can hold the chosen trim.

        Finding out only when the export fails means re-doing the trim after a
        wait, so the refusal is surfaced while there is still something to
        adjust.
        """
        target = self._size.currentData()
        length = max(0.0, self._end_s - self._start_s)
        self._trim_label.setText(f"{_mmss(self._start_s)} – {_mmss(self._end_s)}"
                                 f"  ({length:.1f}s)")
        if not target:
            self._status.setText("")
            self._export_btn.setEnabled(True)
            return
        try:
            kbps = video_bitrate_kbps(target, length, max(1, len(self._tracks())))
        except ValueError as exc:
            self._status.setText("⚠ " + str(exc))
            self._export_btn.setEnabled(False)
            return
        self._status.setText(
            _tr("clip_estimate", "About {kbps} kbit/s video at this length.")
            .replace("{kbps}", str(kbps)))
        self._export_btn.setEnabled(True)

    def _on_export(self) -> None:
        target = self._size.currentData()
        suffix = ".mp4" if target else self._path.suffix
        # Exports go under the library, not beside the recordings: written next
        # to them they came back as extra cards in the grid, and opening one of
        # those to export again produced "_share_share".
        try:
            share_dir().mkdir(parents=True, exist_ok=True)
            destination = export_destination(self._path, suffix)
        except OSError as exc:
            logger.warning("cannot prepare the share folder: %s", exc)
            destination = self._path.with_name(f"{self._path.stem}_share{suffix}")

        # The span being exported is the one worth reopening on.
        write_trim(self._path, self._start_s, self._end_s)

        plan = ExportPlan(
            source=self._path, destination=destination,
            start_s=self._start_s, end_s=self._end_s,
            target_mb=target, tracks=self._tracks(),
        )

        self._export_btn.setEnabled(False)
        self._status.setText(_tr("clip_exporting", "Exporting…"))
        self._worker = _ExportWorker(plan, self)
        self._worker.done.connect(self._on_export_done)
        self._worker.start()

    def _on_export_done(self, path, error: str) -> None:
        self._export_btn.setEnabled(True)
        if path is None:
            self._status.setText("⚠ " + (error or _tr("clip_export_failed",
                                                      "Export failed.")))
            return
        self._exported = Path(path)
        size_mb = self._exported.stat().st_size / (1024 * 1024)
        self._status.setText(
            _tr("clip_exported", "Exported:") + f" {self._exported.name} "
            f"({size_mb:.1f} MB)")
        # Exporting is the end of the job, so it ends in the share step rather
        # than in a line of status text the user has to notice.
        try:
            from arctis_sound_manager.gui.clip_share_dialog import ClipShareDialog
            ClipShareDialog(self._exported, self).exec()
        except Exception:                            # pragma: no cover - env dependent
            logger.exception("could not open the share dialog")

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:
        # Keep the span even when the editor is closed without exporting: the
        # trim is the work, and having to redo it is what makes a second visit
        # feel like starting over.
        band = getattr(self, "_band", None)
        if band is not None:
            write_trim(self._path, band.start_s, band.end_s)
        player = getattr(self, "_player", None)
        if player is not None:
            player.stop()
        worker = self._worker
        if worker is not None and worker.isRunning():
            worker.wait(2000)
        super().closeEvent(event)
