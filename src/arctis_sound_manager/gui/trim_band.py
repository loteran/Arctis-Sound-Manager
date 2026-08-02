# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""The clip editor's trim control: a timeline band with in/out markers.

Two horizontal sliders were the wrong shape for this job. Their handles carry
no relationship to each other — nothing on screen said which was the start and
which the end, the selected span was invisible, and the playhead lived on a
third slider somewhere else. Trimming is a spatial task, and every editor that
does it well draws it as one band: the clip runs left to right, the kept span
is lit, the discarded ends are dimmed, and the in and out points are vertical
lines you drag.

The geometry is kept in module functions rather than inside paintEvent so the
mapping between seconds and pixels can be tested without a screen.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QSizePolicy, QWidget

import arctis_sound_manager.gui.theme as _theme

# How much of the tail a clip opens with selected.
#
# The buffer ends at the moment the shortcut was pressed, so the thing worth
# sharing is at the end of it — and what gets shared is a few seconds, not the
# thirty that were saved. Opening on the whole clip means every single clip has
# to be trimmed by hand before it is worth sending; opening on the last ten
# seconds means the common case is already framed and Export can be pressed
# straight away. Nothing is discarded: the file still holds all of it, and the
# in-point drags back as far as the user wants.
DEFAULT_TAIL_S = 10.0

# Quick lengths offered next to the band, all measured back from the end.
LENGTH_PRESETS_S: tuple[float, ...] = (5.0, 10.0, 15.0, 30.0)

# Left/right room for the handles to sit in without being clipped by the widget
# edge, in pixels.
EDGE_PAD = 8

_HANDLE_GRAB_PX = 9        # how close a click counts as "on" a handle
_DRAG_SLOP_PX = 3          # movement below this is a click, not a drag


def default_range(duration_s: float, tail_s: float = DEFAULT_TAIL_S) -> tuple[float, float]:
    """The span a freshly opened clip starts with: its last *tail_s* seconds.

    A clip shorter than the tail is selected whole — there is nothing to trim
    back to, and an empty selection would disable Export on a perfectly good
    clip.
    """
    if duration_s <= 0:
        return (0.0, 0.0)
    if duration_s <= tail_s:
        return (0.0, duration_s)
    return (duration_s - tail_s, duration_s)


def tick_step_s(duration_s: float, width_px: int) -> float:
    """Seconds between ruler ticks, chosen so labels never collide.

    Picked from a 1-2-5 ladder against the available width: a 30 s clip in a
    narrow dialog gets 5 s marks, the same clip full-screen gets 1 s marks.
    """
    if duration_s <= 0 or width_px <= 0:
        return 1.0
    min_px = 58.0                       # room for "0:00" plus breathing space
    wanted = duration_s * min_px / width_px
    for step in (1, 2, 5, 10, 15, 30, 60, 120, 300, 600):
        if step >= wanted:
            return float(step)
    return 900.0


def s_to_x(seconds: float, duration_s: float, width_px: int) -> float:
    """Position of *seconds* along a band *width_px* wide."""
    track = max(1.0, width_px - 2 * EDGE_PAD)
    if duration_s <= 0:
        return float(EDGE_PAD)
    ratio = min(1.0, max(0.0, seconds / duration_s))
    return EDGE_PAD + ratio * track


def x_to_s(x: float, duration_s: float, width_px: int) -> float:
    """Inverse of :func:`s_to_x`, clamped to the clip."""
    track = max(1.0, width_px - 2 * EDGE_PAD)
    ratio = min(1.0, max(0.0, (x - EDGE_PAD) / track))
    return ratio * max(0.0, duration_s)


def mmss(seconds: float) -> str:
    seconds = max(0.0, seconds)
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}"


class TrimBand(QWidget):
    """A clip as one band: dimmed ends, a lit selection, draggable in/out lines.

    Emits :attr:`rangeChanged` while a marker moves and :attr:`scrubbed` when
    the user clicks somewhere to move the playhead. The playhead itself is
    driven from outside via :meth:`set_position` — the band does not own the
    player.
    """

    rangeChanged = Signal(float, float)     # (start_s, end_s)
    scrubbed = Signal(float)                # seconds

    MIN_SPAN_S = 0.2                        # shortest clip worth exporting
    HEIGHT = 82
    RULER_H = 16                            # bottom strip holding time labels

    def __init__(self, duration_s: float = 0.0, parent: QWidget | None = None):
        super().__init__(parent)
        self._duration = max(0.0, duration_s)
        self._start, self._end = default_range(self._duration)
        self._position = self._start
        self._grab: str | None = None       # "start" | "end" | "range" | None
        self._press_x = 0.0
        self._press_start = 0.0
        self._moved = False

        self.setMinimumHeight(self.HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    # ── state ─────────────────────────────────────────────────────────────────

    @property
    def duration_s(self) -> float:
        return self._duration

    @property
    def start_s(self) -> float:
        return self._start

    @property
    def end_s(self) -> float:
        return self._end

    @property
    def span_s(self) -> float:
        return max(0.0, self._end - self._start)

    def set_duration(self, duration_s: float, keep_range: bool = False) -> None:
        """Learn the real length, which often only arrives once the clip loads.

        The dialog is built before the media backend has opened the file, so
        the band is created with whatever ffprobe reported — or with zero when
        that failed. Re-selecting the default tail on the new length keeps the
        opening selection correct instead of leaving a range that was measured
        against a length that turned out to be wrong.
        """
        self._duration = max(0.0, duration_s)
        if keep_range:
            self._end = min(self._end, self._duration)
            self._start = min(self._start, max(0.0, self._end - self.MIN_SPAN_S))
        else:
            self._start, self._end = default_range(self._duration)
            self._position = self._start
        self.update()
        self.rangeChanged.emit(self._start, self._end)

    def set_range(self, start_s: float, end_s: float) -> None:
        start, end = self._clamped(start_s, end_s)
        if (start, end) == (self._start, self._end):
            return
        self._start, self._end = start, end
        self.update()
        self.rangeChanged.emit(self._start, self._end)

    def select_last(self, seconds: float) -> None:
        """Select the final *seconds* — what the quick-length buttons do."""
        self.set_range(max(0.0, self._duration - seconds), self._duration)

    def select_all(self) -> None:
        self.set_range(0.0, self._duration)

    def set_position(self, seconds: float) -> None:
        """Move the playhead (the player drives this)."""
        position = min(max(0.0, seconds), self._duration)
        if abs(position - self._position) < 0.01:
            return
        self._position = position
        self.update()

    def _clamped(self, start_s: float, end_s: float) -> tuple[float, float]:
        end = min(max(self.MIN_SPAN_S, end_s), self._duration or end_s)
        start = min(max(0.0, start_s), max(0.0, end - self.MIN_SPAN_S))
        return (start, end)

    # ── mouse ─────────────────────────────────────────────────────────────────

    def _x(self, seconds: float) -> float:
        return s_to_x(seconds, self._duration, self.width())

    def _s(self, x: float) -> float:
        return x_to_s(x, self._duration, self.width())

    def _hit(self, x: float) -> str:
        """What a press at *x* lands on.

        The markers are tested before the playhead, which matters because a
        freshly opened clip puts the playhead exactly on the in-point: with the
        playhead winning there, the in-marker would be the one thing on the band
        that could not be dragged. Scrubbing loses nothing by coming second —
        pressing anywhere outside the selection scrubs too, and once the
        playhead has moved off the marker it is grabbable on its own.
        """
        if abs(x - self._x(self._start)) <= _HANDLE_GRAB_PX:
            return "start"
        if abs(x - self._x(self._end)) <= _HANDLE_GRAB_PX:
            return "end"
        if abs(x - self._x(self._position)) <= _HANDLE_GRAB_PX:
            return "playhead"
        if self._x(self._start) < x < self._x(self._end):
            return "range"
        return "outside"

    def _scrub_to(self, x: float) -> None:
        self.scrubbed.emit(self._s(x))
        self.set_position(self._s(x))

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton or self._duration <= 0:
            return
        x = event.position().x()
        target = self._hit(x)
        self._press_x = x
        self._press_start = self._start
        self._moved = False
        if target == "outside":
            # Outside the selection there is nothing to move, so a press is a
            # seek — and holding it scrubs, which is how you find a moment.
            self._grab = "playhead"
            self._scrub_to(x)
            return
        self._grab = target
        if target == "playhead":
            self._scrub_to(x)

    def mouseMoveEvent(self, event) -> None:
        x = event.position().x()
        if self._grab is None:
            self.setCursor(
                Qt.CursorShape.SplitHCursor
                if self._hit(x) in ("start", "end", "playhead")
                else Qt.CursorShape.PointingHandCursor)
            return
        if not self._moved and abs(x - self._press_x) < _DRAG_SLOP_PX:
            return
        self._moved = True

        if self._grab == "playhead":
            self._scrub_to(x)
        elif self._grab == "start":
            self.set_range(self._s(x), self._end)
        elif self._grab == "end":
            self.set_range(self._start, self._s(x))
        else:
            # Slide the whole selection, keeping its length. Running into
            # either end stops it rather than squashing it — a window being
            # dragged should not silently change size.
            span = self.span_s
            start = self._press_start + (self._s(x) - self._s(self._press_x))
            start = min(max(0.0, start), max(0.0, self._duration - span))
            self.set_range(start, start + span)

    def mouseReleaseEvent(self, event) -> None:
        if self._grab == "range" and not self._moved:
            # A press inside the selection that never moved is a seek, not a
            # nudge: clicking the part you kept should play from there.
            self._scrub_to(event.position().x())
        self._grab = None
        self._moved = False

    def keyPressEvent(self, event) -> None:
        step = 1.0 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 0.1
        if event.key() == Qt.Key.Key_Left:
            self.set_range(self._start - step, self._end)
        elif event.key() == Qt.Key.Key_Right:
            self.set_range(self._start + step, self._end)
        elif event.key() == Qt.Key.Key_BracketLeft:
            self.set_range(self._start, self._end - step)
        elif event.key() == Qt.Key.Key_BracketRight:
            self.set_range(self._start, self._end + step)
        else:
            super().keyPressEvent(event)

    # ── paint ─────────────────────────────────────────────────────────────────

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        accent = QColor(_theme.c("ACCENT"))
        border = QColor(_theme.c("BORDER"))
        text_dim = QColor(_theme.c("TEXT_SECONDARY"))

        band = QRectF(0, 0, self.width(), self.height() - self.RULER_H)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(_theme.c("BG_BUTTON")))
        painter.drawRoundedRect(band, 6, 6)

        self._paint_ticks(painter, band, border, text_dim)

        if self._duration <= 0:
            painter.end()
            return

        x0, x1 = self._x(self._start), self._x(self._end)

        # Dim what the export will drop. Darkening the ends reads as "these
        # are gone" far faster than lightening the middle does.
        shade = QColor(0, 0, 0, 120)
        painter.setBrush(shade)
        painter.drawRect(QRectF(band.left(), band.top(), x0 - band.left(), band.height()))
        painter.drawRect(QRectF(x1, band.top(), band.right() - x1, band.height()))

        # The kept span: a faint accent wash with a solid bar top and bottom,
        # which is what makes it read as one block rather than two markers.
        keep = QRectF(x0, band.top(), max(1.0, x1 - x0), band.height())
        wash = QColor(accent)
        wash.setAlpha(38)
        painter.setBrush(wash)
        painter.drawRect(keep)
        painter.setBrush(accent)
        painter.drawRect(QRectF(keep.left(), keep.top(), keep.width(), 2.5))
        painter.drawRect(QRectF(keep.left(), keep.bottom() - 2.5, keep.width(), 2.5))

        self._paint_marker(painter, x0, band, accent, leading=True)
        self._paint_marker(painter, x1, band, accent, leading=False)
        self._paint_playhead(painter, band)
        self._paint_span_label(painter, keep)
        painter.end()

    def _paint_ticks(self, painter, band, border, text_dim) -> None:
        """Second lines down the band and a time label under each major one."""
        if self._duration <= 0:
            return
        step = tick_step_s(self._duration, self.width())
        minor = QColor(border)
        minor.setAlpha(150)

        font = QFont(self.font())
        font.setPointSizeF(7.5)
        painter.setFont(font)

        seconds = 0.0
        while seconds <= self._duration + 0.001:
            x = self._x(seconds)
            painter.setPen(QPen(minor, 1))
            painter.drawLine(QPointF(x, band.top() + 4), QPointF(x, band.bottom() - 4))
            painter.setPen(QPen(text_dim, 1))
            painter.drawText(QRectF(x - 24, band.bottom() + 1, 48, self.RULER_H),
                             Qt.AlignmentFlag.AlignCenter, mmss(seconds))
            seconds += step

    def _paint_marker(self, painter, x, band, accent, leading: bool) -> None:
        """One in/out point: a full-height line with a grip to grab it by."""
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(accent)
        painter.drawRect(QRectF(x - 1.5, band.top(), 3, band.height()))

        # The grip sits inside the selection so the two markers of a very short
        # selection cannot overlap into an unusable blob.
        grip_w, grip_h = 7.0, 22.0
        left = x if leading else x - grip_w
        painter.drawRoundedRect(
            QRectF(left, band.center().y() - grip_h / 2, grip_w, grip_h), 2.5, 2.5)

        # Two notches, the universal "this is a handle" mark.
        painter.setPen(QPen(QColor(_theme.c("BG_MAIN")), 1))
        for dy in (-4, 4):
            y = band.center().y() + dy
            painter.drawLine(QPointF(left + 2, y), QPointF(left + grip_w - 2, y))

    def _paint_playhead(self, painter, band) -> None:
        x = self._x(self._position)
        head = QColor(_theme.c("TEXT_PRIMARY"))
        painter.setPen(QPen(head, 1))
        painter.drawLine(QPointF(x, band.top()), QPointF(x, band.bottom()))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(head)
        painter.drawPolygon(QPolygonF([
            QPointF(x - 4, band.top()), QPointF(x + 4, band.top()),
            QPointF(x, band.top() + 6)]))

    def _paint_span_label(self, painter, keep) -> None:
        """The selection's length, written on it — but only when it fits."""
        text = f"{self.span_s:.1f}s"
        font = QFont(self.font())
        font.setPointSizeF(8.5)
        font.setBold(True)
        painter.setFont(font)
        if keep.width() < 52:
            return
        painter.setPen(QPen(QColor(_theme.c("TEXT_PRIMARY")), 1))
        painter.drawText(keep, Qt.AlignmentFlag.AlignCenter, text)
