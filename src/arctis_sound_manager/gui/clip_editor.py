# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Clip editor: preview, trim, per-channel levels, and a share-sized export.

Opening a clip used to hand it to the system video player, which is the one
thing that cannot be done with it — the reason to open a clip is to cut it
down, quieten the microphone and send it somewhere. All of that lives here, and
the result is dragged straight out of the dialog into whatever it is going to.

The preview is a mixer, not a player. A clip carries the game, the chat, the
media and the microphone as separate tracks, and a media player — Qt's included
— decodes exactly one of them and picks the first, so a clip whose game channel
was empty plays back in silence with three perfectly good channels sitting
beside it. Every channel is therefore given a player of its own and run in
step, which is what makes the level and mute controls mean something while you
are listening rather than only after an export.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication, QKeySequence, QShortcut
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
from arctis_sound_manager.clip_export import (FPS_CHOICES, SHARE_SUFFIX,
                                             ExportPlan, TrackMix, duration_s,
                                             export, probe_tracks,
                                             silent_tracks, split_tracks,
                                             video_bitrate_kbps)
from arctis_sound_manager.clip_library import (export_destination, read_mix,
                                               read_trim, share_dir, write_mix,
                                               write_trim)
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

# Frame rates offered at export. "As recorded" is the default because it is the
# only choice that costs nothing: the screencast is variable-rate, so picking a
# number here re-encodes the clip to hold that rate exactly. Worth it when a
# clip plays back unevenly somewhere it is being shared; not worth it by
# default.
FPS_LABELS: list[tuple[str, int | None]] = [
    ("As recorded", None), *((f"{n} fps", n) for n in FPS_CHOICES),
]

# How far a channel may drift from the video before it is pulled back into
# line. Each channel is its own decoder with its own clock, so they wander
# apart by a few milliseconds over a long clip. The threshold is deliberately
# well above audible lip-sync error: correcting a 20 ms drift costs an audible
# hitch every few seconds, which is far worse than the drift.
_SYNC_TOLERANCE_MS = 250

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


class _TrackPrepWorker(QThread):
    """Splits the channels out and measures which of them are empty.

    Both jobs walk the same tracks and both shell out to ffmpeg, so they share
    one thread and one pass. Neither is allowed to hold the dialog closed: the
    split is a demux and takes milliseconds, but the level scan decodes each
    channel, and the editor is perfectly usable while the answer is on its way.
    """

    done = Signal(list, list)       # ([per-channel file], [is_silent])

    def __init__(self, path: Path, count: int, workdir: Path, parent=None):
        super().__init__(parent)
        self._path = path
        self._count = count
        self._workdir = workdir

    def run(self) -> None:
        files: list[Path] = []
        flags: list[bool] = []
        try:
            files = split_tracks(self._path, self._count, self._workdir)
        except Exception:
            logger.debug("could not split the clip's channels", exc_info=True)
        try:
            flags = silent_tracks(self._path, self._count)
        except Exception:
            logger.debug("could not measure channel levels", exc_info=True)
        self.done.emit(files, flags)


class _ChannelMixer:
    """Every channel playing at once, one player each, driven together.

    Qt's player exposes an *active* audio track, singular — there is no API for
    hearing two at the same time, and no volume that applies to one of them.
    Handing each channel its own single-track file sidesteps that entirely: the
    players are ordinary, the volumes are ordinary, and the only new problem is
    keeping them in step, which the video's own position solves.

    The video player keeps its own audio silent throughout. It would otherwise
    contribute the first channel a second time, at full volume, immune to that
    channel's mute — audible as the one track the mute button did not work on.
    """

    def __init__(self, parent) -> None:
        self._parent = parent
        self._players: list = []
        self._outputs: list = []

    @property
    def ready(self) -> bool:
        return bool(self._players)

    def load(self, files: list[Path]) -> bool:
        """Build one player per channel file. False when Qt Multimedia is absent."""
        try:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
        except ImportError:                          # pragma: no cover - env dependent
            return False

        self.release()
        for path in files:
            player = QMediaPlayer(self._parent)
            output = QAudioOutput(self._parent)
            player.setAudioOutput(output)
            player.setSource(QUrl.fromLocalFile(str(path)))
            self._players.append(player)
            self._outputs.append(output)
        return bool(self._players)

    def set_level(self, index: int, volume: float, muted: bool) -> None:
        if 0 <= index < len(self._outputs):
            self._outputs[index].setVolume(0.0 if muted else max(0.0, volume))

    def seek(self, ms: int) -> None:
        for player in self._players:
            player.setPosition(ms)

    def play(self, ms: int) -> None:
        for player in self._players:
            player.setPosition(ms)
            player.play()

    def pause(self) -> None:
        for player in self._players:
            player.pause()

    def stop(self) -> None:
        for player in self._players:
            player.stop()

    def resync(self, ms: int) -> None:
        """Pull back any channel that has wandered away from the video."""
        for player in self._players:
            if abs(player.position() - ms) > _SYNC_TOLERANCE_MS:
                player.setPosition(ms)

    def release(self) -> None:
        for player in self._players:
            player.stop()
            player.setSource(QUrl())
        self._players.clear()
        self._outputs.clear()


class ClipEditor(QDialog):
    """Trim a clip, set its channel levels, and export it at a share size."""

    def __init__(self, path: Path, parent=None):
        super().__init__(parent)
        self._path = path
        self._duration = duration_s(path)
        self._exported: Path | None = None
        self._worker: _ExportWorker | None = None
        self._prep_worker: _TrackPrepWorker | None = None
        self._seeked_to_start = False
        self._mixer = _ChannelMixer(self)
        # Channel files live here for as long as the dialog does. A directory
        # rather than loose files so cleanup is one call and cannot leave a
        # stray track behind in /tmp for every clip ever opened.
        self._workdir = Path(tempfile.mkdtemp(prefix="asm-clip-"))

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

        # Space is what every player uses, and the video has to be clicked to
        # be focused otherwise — which is itself a play/pause here.
        play_pause = QShortcut(QKeySequence(Qt.Key.Key_Space), self)
        play_pause.activated.connect(self._toggle_play)

        self._update_estimate()

    # ── preview ───────────────────────────────────────────────────────────────

    def _build_preview(self) -> QWidget:
        """The picture, and nothing else.

        There is no transport bar. It carried a play button and a seek slider,
        and both were a second, worse copy of something already on screen: the
        trim band under the preview is a timeline with a playhead on it, drawn
        against the same clip, showing the span being exported as well as the
        position. Two scrubbers for one clip is one too many, and the one that
        knows about the trim is the one worth keeping. Clicking the picture
        starts and stops it, which is what clicking a video does everywhere.
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

        class _ClickableVideo(QVideoWidget):
            """A video surface that answers a click."""

            clicked = Signal()

            def mousePressEvent(self, event) -> None:
                if event.button() == Qt.MouseButton.LeftButton:
                    self.clicked.emit()
                super().mousePressEvent(event)

        video = _ClickableVideo()
        video.setMinimumHeight(260)
        video.setCursor(Qt.CursorShape.PointingHandCursor)
        video.setToolTip(_tr("clip_preview_hint",
                             "Click to play or pause. Drag the playhead on the "
                             "band below to scrub."))
        video.clicked.connect(self._toggle_play)

        self._player = QMediaPlayer(self)
        # The video player's own audio stays off for good: the channels are
        # played separately and mixing its copy back in would make the first
        # channel unmutable.
        self._video_audio = QAudioOutput(self)
        self._video_audio.setVolume(0.0)
        self._player.setAudioOutput(self._video_audio)
        self._player.setVideoOutput(video)
        self._player.setSource(QUrl.fromLocalFile(str(self._path)))

        # Setting a source only loads it — the widget stays black until frames
        # are decoded, so the editor opened onto an empty rectangle and the trim
        # markers had nothing to aim at. Playing and immediately pausing renders
        # the first frame without the clip starting up on its own.
        self._player.play()
        self._player.pause()
        self._player.positionChanged.connect(self._on_position)
        self._player.durationChanged.connect(self._on_media_duration)
        return video

    def _toggle_play(self) -> None:
        player = getattr(self, "_player", None)
        if player is None:
            return
        if self._is_playing():
            player.pause()
            self._mixer.pause()
            return
        # Play the selection. Starting from wherever the playhead was left —
        # usually outside the trim — plays the part being thrown away.
        band = getattr(self, "_band", None)
        position = player.position()
        if band is not None and not (band.start_s <= position / 1000.0 < band.end_s):
            position = int(band.start_s * 1000)
            player.setPosition(position)
        player.play()
        self._mixer.play(position)

    def _seek(self, seconds: float) -> None:
        """Move everything — the picture and every channel — to *seconds*."""
        player = getattr(self, "_player", None)
        if player is None:
            return
        ms = int(max(0.0, seconds) * 1000)
        player.setPosition(ms)
        self._mixer.seek(ms)

    def _on_position(self, ms: int) -> None:
        band = getattr(self, "_band", None)
        if band is not None:
            band.set_position(ms / 1000.0)
        if self._is_playing():
            self._mixer.resync(ms)
        # Preview what will be exported, not what is on disk: playback stops at
        # the out point and returns to the in point. Running on past the trim
        # means the preview never shows the clip being made.
        if band is not None and self._is_playing() and ms >= band.end_s * 1000:
            self._player.pause()
            self._mixer.pause()
            self._seek(band.start_s)

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
                "Opens on the last {n} seconds — drag the markers to change it, "
                "or drag the playhead to scrub.")
            .replace("{n}", f"{DEFAULT_TAIL_S:.0f}"))
        self._band.rangeChanged.connect(self._on_trim_changed)
        self._band.scrubbed.connect(self._seek)
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

    def _on_media_duration(self, ms: int) -> None:
        """Take the player's length over ffprobe's when they disagree.

        ffprobe reads the container header; a clip written straight out of the
        rolling buffer can carry a duration that is short of what actually
        decodes. Whichever number is used has to be the one the band is drawn
        against, or the end marker sits somewhere other than the end.
        """
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
            self._seek(self._band.start_s)

    @property
    def _start_s(self) -> float:
        return self._band.start_s

    @property
    def _end_s(self) -> float:
        return self._band.end_s

    # ── channels ──────────────────────────────────────────────────────────────

    def _build_tracks(self) -> QWidget:
        """One row per channel: its name, its level, and whether to keep it.

        Every channel plays, always. There is no "listen to this one" — that
        was a workaround for the player's one-track-at-a-time limit dressed up
        as a feature, and it put the word *Listen* in front of four channel
        names that were the only thing worth reading. Mute is the control that
        decides what you hear, and it is the same control that decides what
        gets exported, so the preview is the export.

        Levels are restored from the sidecar, so a clip reopened to adjust an
        export does not start again with the microphone back at full.
        """
        box = QWidget()
        col = QVBoxLayout(box)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(4)

        names = probe_tracks(self._path)
        self._track_rows: list[tuple[str, QSlider, QCheckBox]] = []
        self._silent_labels: list[QLabel] = []
        if not names:
            col.addWidget(QLabel(_tr("clip_no_tracks", "No audio tracks found.")))
            return box

        header = QHBoxLayout()
        header.addWidget(QLabel(_tr("clip_tracks", "Audio channels:")))
        header.addStretch(1)
        hint = QLabel(_tr("clip_channels_hint",
                          "All channels play together — mute the ones you do "
                          "not want. This is exactly what gets exported."))
        hint.setStyleSheet(f"color: {_theme.c('TEXT_SECONDARY')}; font-size: 8pt;")
        header.addWidget(hint)
        col.addLayout(header)

        remembered = read_mix(self._path)
        for index, name in enumerate(names):
            row = QHBoxLayout()
            label = QLabel(name)
            label.setMinimumWidth(110)

            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(0, 150)
            mute = QCheckBox(_tr("clip_mute", "Mute"))
            volume, muted = remembered.get(name, (1.0, False))
            slider.setValue(int(round(volume * 100)))
            mute.setChecked(muted)
            slider.valueChanged.connect(self._on_levels_changed)
            mute.toggled.connect(self._on_levels_changed)

            silent = QLabel("")
            silent.setStyleSheet(f"color: {_theme.c('TEXT_SECONDARY')}; font-size: 8pt;")
            self._silent_labels.append(silent)

            row.addWidget(label)
            row.addWidget(slider, stretch=1)
            row.addWidget(silent)
            row.addWidget(mute)
            col.addLayout(row)
            self._track_rows.append((name, slider, mute))

        self._start_track_prep(len(names))
        return box

    def _tracks(self) -> list[TrackMix]:
        return [TrackMix(name=n, volume=s.value() / 100.0, muted=m.isChecked())
                for n, s, m in getattr(self, "_track_rows", [])]

    def _on_levels_changed(self) -> None:
        """A slider or a mute moved: hear it now, and remember it."""
        for index, track in enumerate(self._tracks()):
            self._mixer.set_level(index, track.volume, track.muted)
        self._update_estimate()

    def _start_track_prep(self, count: int) -> None:
        """Split the channels out and scan their levels, off the UI thread."""
        self._prep_worker = _TrackPrepWorker(self._path, count, self._workdir, self)
        self._prep_worker.done.connect(self._on_tracks_prepared)
        self._prep_worker.start()

    def _on_tracks_prepared(self, files: list, flags: list) -> None:
        """Bring the channels online and say which of them are empty."""
        if files and self._mixer.load([Path(f) for f in files]):
            self._on_levels_changed()
            if (band := getattr(self, "_band", None)) is not None:
                self._mixer.seek(int(band.start_s * 1000))
        else:
            # The channels could not be split, so there is no mixer — and the
            # video player's audio is off precisely because there normally is
            # one. Left there, the preview would be completely silent, which is
            # worse than the single-track playback this replaced. Give the
            # video its own audio back as the degraded mode, and say why the
            # per-channel controls are not doing anything.
            if (audio := getattr(self, "_video_audio", None)) is not None:
                audio.setVolume(1.0)
            if shutil.which("ffmpeg") is None:
                message = _tr("clip_no_ffmpeg",
                              "ffmpeg is not installed, so clips cannot be "
                              "exported and the preview plays one track only.")
            else:
                message = _tr("clip_no_channel_preview",
                              "Could not separate this clip's channels, so the "
                              "preview plays one track and the level sliders "
                              "only affect the export.")
            self._status.setText("⚠ " + message)

        audible = False
        for index, is_silent in enumerate(flags):
            if index < len(self._silent_labels):
                self._silent_labels[index].setText(
                    _tr("clip_track_silent", "silent") if is_silent else "")
            audible = audible or not is_silent

        if flags and not audible:
            self._status.setText("⚠ " + _tr(
                "clip_all_silent",
                "Every audio channel in this clip is empty. Check that the game "
                "and chat are routed through the Sonar channels on the Home page."))

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

        row.addWidget(QLabel(_tr("clip_fps", "Frame rate:")))
        self._fps = QComboBox()
        for label, value in FPS_LABELS:
            self._fps.addItem(_tr(f"clip_fps_{value or 'source'}", label), value)
        self._fps.setToolTip(_tr(
            "clip_fps_hint",
            "The screen is captured whenever it changes, so a recording holds "
            "whatever rate it managed. Choosing one here re-encodes the clip to "
            "hold it exactly — use it if a clip plays back unevenly."))
        self._fps.currentIndexChanged.connect(self._update_estimate)
        row.addWidget(self._fps)

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
            # Without a size target the export is normally a stream copy, which
            # is instant. Fixing the rate is not, and an export that suddenly
            # takes a minute with no warning reads as a hang.
            self._status.setText("" if not self._fps.currentData() else _tr(
                "clip_fps_reencode",
                "Fixing the frame rate re-encodes the clip — this takes longer "
                "than a plain export."))
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
        # Always MP4, whatever the settings — see clip_export.SHARE_SUFFIX for
        # why the recording's own container is not an option here.
        suffix = SHARE_SUFFIX
        # Exports go under the library, not beside the recordings: written next
        # to them they came back as extra cards in the grid, and opening one of
        # those to export again produced "_share_share".
        try:
            share_dir().mkdir(parents=True, exist_ok=True)
            destination = export_destination(self._path, suffix)
        except OSError as exc:
            logger.warning("cannot prepare the share folder: %s", exc)
            destination = self._path.with_name(f"{self._path.stem}_share{suffix}")

        self._remember()

        plan = ExportPlan(
            source=self._path, destination=destination,
            start_s=self._start_s, end_s=self._end_s,
            target_mb=target, tracks=self._tracks(),
            fps=self._fps.currentData(),
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

    def _remember(self) -> None:
        """Write the trim and the channel levels beside the clip.

        Both are work, and both used to be discarded by closing the dialog. The
        trim is where the clip starts and ends; the mix is the decision that the
        microphone was too loud and the chat channel should be off, which is no
        less of a judgement and no less annoying to make twice.
        """
        band = getattr(self, "_band", None)
        if band is not None:
            write_trim(self._path, band.start_s, band.end_s)
        rows = getattr(self, "_track_rows", [])
        if rows:
            write_mix(self._path, {
                name: (slider.value() / 100.0, mute.isChecked())
                for name, slider, mute in rows})

    def closeEvent(self, event) -> None:
        self._remember()
        player = getattr(self, "_player", None)
        if player is not None:
            player.stop()
        self._mixer.release()
        for worker in (self._worker, self._prep_worker):
            if worker is not None and worker.isRunning():
                worker.wait(2000)
        # The split channels are only good for this dialog's lifetime.
        shutil.rmtree(self._workdir, ignore_errors=True)
        super().closeEvent(event)
