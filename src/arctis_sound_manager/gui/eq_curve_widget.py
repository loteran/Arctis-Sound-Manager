"""
EqCurveWidget — Interactive parametric EQ curve widget (Sonar mode).

Interactions:
  - Click on dot          → select band
  - Click on empty canvas → create new band
  - Drag dot              → move freq (horizontal) / gain (vertical)
  - Double-click dot      → delete band
  - Scroll on selected    → adjust Q
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from arctis_sound_manager.gui.theme import ACCENT, BG_CARD, BORDER, TEXT_PRIMARY, TEXT_SECONDARY

# ── Data ──────────────────────────────────────────────────────────────────────

FILTER_TYPES = ["peakingEQ", "lowPass", "highPass", "lowShelving", "highShelving"]
FILTER_LABELS = {
    "peakingEQ":    "Peaking EQ",
    "lowPass":      "Low Pass",
    "highPass":     "High Pass",
    "lowShelving":  "Low Shelf",
    "highShelving": "High Shelf",
}
PW_LABEL = {
    "peakingEQ":    "bq_peaking",
    "lowPass":      "bq_lowpass",
    "highPass":     "bq_highpass",
    "lowShelving":  "bq_lowshelf",
    "highShelving": "bq_highshelf",
}

BAND_COLORS = [
    "#FF6B6B", "#FF9F43", "#FECA57", "#48DBFB", "#1DD1A1",
    "#C56BFF", "#FF78C4", "#54A0FF", "#FF6348", "#01CBC6",
]


@dataclass
class EqBand:
    freq: float = 1000.0
    gain: float = 0.0
    q: float = 0.707
    type: str = "peakingEQ"
    enabled: bool = True


# ── Biquad RBJ cookbook ───────────────────────────────────────────────────────

_SR = 48000.0
_FREQ_RANGE = (20.0, 20000.0)
_GAIN_RANGE = (-12.0, 12.0)
_N_POINTS = 512

_FREQ_POINTS: list[float] = [
    _FREQ_RANGE[0] * (_FREQ_RANGE[1] / _FREQ_RANGE[0]) ** (i / (_N_POINTS - 1))
    for i in range(_N_POINTS)
]


def _biquad_response(band: EqBand, freqs: list[float]) -> list[float]:
    """Amplitude response (dB) of a single biquad filter at each frequency."""
    f0, gain_db, Q = band.freq, band.gain, band.q
    A = 10 ** (gain_db / 40.0)
    w0 = 2 * math.pi * f0 / _SR
    cosw = math.cos(w0)
    sinw = math.sin(w0)
    alpha = sinw / (2 * Q)

    t = band.type
    if t == "peakingEQ":
        b0 = 1 + alpha * A;  b1 = -2 * cosw;  b2 = 1 - alpha * A
        a0 = 1 + alpha / A;  a1 = -2 * cosw;  a2 = 1 - alpha / A
    elif t == "lowPass":
        b0 = (1 - cosw) / 2; b1 = 1 - cosw;   b2 = (1 - cosw) / 2
        a0 = 1 + alpha;      a1 = -2 * cosw;  a2 = 1 - alpha
    elif t == "highPass":
        b0 = (1 + cosw) / 2; b1 = -(1 + cosw); b2 = (1 + cosw) / 2
        a0 = 1 + alpha;      a1 = -2 * cosw;   a2 = 1 - alpha
    elif t == "lowShelving":
        sA = math.sqrt(A)
        b0 =      A * ((A + 1) - (A - 1) * cosw + 2 * sA * alpha)
        b1 =  2 * A * ((A - 1) - (A + 1) * cosw)
        b2 =      A * ((A + 1) - (A - 1) * cosw - 2 * sA * alpha)
        a0 =           (A + 1) + (A - 1) * cosw + 2 * sA * alpha
        a1 = -2 *     ((A - 1) + (A + 1) * cosw)
        a2 =           (A + 1) + (A - 1) * cosw - 2 * sA * alpha
    elif t == "highShelving":
        sA = math.sqrt(A)
        b0 =      A * ((A + 1) + (A - 1) * cosw + 2 * sA * alpha)
        b1 = -2 * A * ((A - 1) + (A + 1) * cosw)
        b2 =      A * ((A + 1) + (A - 1) * cosw - 2 * sA * alpha)
        a0 =           (A + 1) - (A - 1) * cosw + 2 * sA * alpha
        a1 =  2 *     ((A - 1) - (A + 1) * cosw)
        a2 =           (A + 1) - (A - 1) * cosw - 2 * sA * alpha
    else:
        return [0.0] * len(freqs)

    # Normalise
    b0 /= a0; b1 /= a0; b2 /= a0
    a1 /= a0; a2 /= a0

    result = []
    for f in freqs:
        w = 2 * math.pi * f / _SR
        z = complex(math.cos(w), math.sin(w))
        zi = 1 / z
        H = (b0 + b1 * zi + b2 / z**2) / (1 + a1 * zi + a2 / z**2)
        result.append(20 * math.log10(max(abs(H), 1e-10)))
    return result


# ── Coordinate helpers ────────────────────────────────────────────────────────

class _Canvas:
    def __init__(self, rect: QRectF):
        self.rect = rect

    def freq_to_x(self, freq: float) -> float:
        t = math.log(max(freq, _FREQ_RANGE[0]) / _FREQ_RANGE[0]) / math.log(_FREQ_RANGE[1] / _FREQ_RANGE[0])
        return self.rect.left() + t * self.rect.width()

    def gain_to_y(self, gain: float) -> float:
        t = (_GAIN_RANGE[1] - gain) / (_GAIN_RANGE[1] - _GAIN_RANGE[0])
        return self.rect.top() + t * self.rect.height()

    def x_to_freq(self, x: float) -> float:
        t = max(0.0, min(1.0, (x - self.rect.left()) / self.rect.width()))
        return _FREQ_RANGE[0] * (_FREQ_RANGE[1] / _FREQ_RANGE[0]) ** t

    def y_to_gain(self, y: float) -> float:
        t = max(0.0, min(1.0, (y - self.rect.top()) / self.rect.height()))
        return _GAIN_RANGE[1] - t * (_GAIN_RANGE[1] - _GAIN_RANGE[0])


# ── Paint constants ───────────────────────────────────────────────────────────

_GRID_FREQS   = [20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000]
_FREQ_LABELS  = {
    20: "20", 50: "50", 100: "100", 200: "200", 500: "500",
    1000: "1k", 2000: "2k", 5000: "5k", 10000: "10k", 20000: "20k",
}
_ZONE_LABELS  = [
    (30, "SUB BASS"), (80, "BASS"), (250, "LOW MIDS"),
    (800, "MID RANGE"), (3500, "UPPER MIDS"), (12000, "HIGHS"),
]
_GAIN_LABELS  = [12, 6, 0, -6, -12]

_DOT_R     = 7    # dot radius (px)
_HIT_R     = 12   # click-detection radius (px)
_ML        = 44   # margin left  (gain labels)
_MR        = 10   # margin right
_MT        = 22   # margin top   (zone labels)
_MB        = 22   # margin bottom (freq labels)


# ── Main widget ───────────────────────────────────────────────────────────────

class EqCurveWidget(QWidget):
    """Interactive parametric EQ curve widget."""

    bands_changed = Signal(list)  # list[EqBand]

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._bands: list[EqBand] = []
        self._extra_bands: list[EqBand] = []
        self._selected: int | None = None
        self._drag_band: int | None = None
        self._drag_start_pos: tuple[float, float] | None = None

        self.setMinimumHeight(200)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)

        self._inspector = _BandInspector(self)
        self._inspector.hide()
        self._inspector.band_edited.connect(self._on_band_edited)

    # ── Public API ───────────────────────────────────────────────────────────

    def set_bands(self, bands: list[EqBand]) -> None:
        self._bands = [EqBand(b.freq, b.gain, b.q, b.type, b.enabled) for b in bands]
        self._selected = None
        self._inspector.hide()
        self.update()

    def set_extra_bands(self, bands: list[EqBand]) -> None:
        """Extra bands (macro sliders) included in curve but not interactive."""
        self._extra_bands = bands
        self.update()

    def get_bands(self) -> list[EqBand]:
        return [EqBand(b.freq, b.gain, b.q, b.type, b.enabled) for b in self._bands]

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _canvas_rect(self) -> QRectF:
        return QRectF(_ML, _MT, self.width() - _ML - _MR, self.height() - _MT - _MB)

    def _canvas(self) -> _Canvas:
        return _Canvas(self._canvas_rect())

    def _curve(self) -> list[float]:
        total = [0.0] * _N_POINTS
        for band in self._bands + self._extra_bands:
            if not band.enabled:
                continue
            for i, v in enumerate(_biquad_response(band, _FREQ_POINTS)):
                total[i] += v
        return total

    def _hit_band(self, x: float, y: float) -> int | None:
        c = self._canvas()
        best_idx, best_d = None, float(_HIT_R)
        for i, b in enumerate(self._bands):
            d = math.hypot(x - c.freq_to_x(b.freq), y - c.gain_to_y(b.gain))
            if d < best_d:
                best_d, best_idx = d, i
        return best_idx

    # ── Paint ────────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = self._canvas()
        cr = c.rect

        # Background
        p.fillRect(self.rect(), QColor(BG_CARD))

        small_font = QFont()
        small_font.setPixelSize(10)
        p.setFont(small_font)

        # Zone labels
        p.setPen(QColor("#2C3340"))
        for freq, label in _ZONE_LABELS:
            x = c.freq_to_x(freq)
            if cr.left() <= x <= cr.right():
                p.drawText(int(x) + 3, int(cr.top()) + 12, label)

        # Freq grid + labels
        for freq in _GRID_FREQS:
            x = c.freq_to_x(freq)
            p.setPen(QPen(QColor("#232A33"), 1))
            p.drawLine(QPointF(x, cr.top()), QPointF(x, cr.bottom()))
            lbl = _FREQ_LABELS[freq]
            fm = p.fontMetrics()
            p.setPen(QColor("#4A5568"))
            p.drawText(int(x - fm.horizontalAdvance(lbl) / 2), self.height() - 4, lbl)

        # Gain grid + labels
        fm = p.fontMetrics()
        for gain in _GAIN_LABELS:
            y = c.gain_to_y(gain)
            is_zero = gain == 0
            p.setPen(QPen(QColor("#3A4255") if is_zero else QColor("#232A33"),
                          1.5 if is_zero else 1))
            p.drawLine(QPointF(cr.left(), y), QPointF(cr.right(), y))
            lbl = f"+{gain}" if gain > 0 else str(gain)
            p.setPen(QColor("#4A5568"))
            p.drawText(int(_ML - fm.horizontalAdvance(lbl) - 4),
                       int(y + fm.height() / 3), lbl)

        # Curve
        gains = self._curve()
        path = QPainterPath()
        moved = False
        for freq, gain in zip(_FREQ_POINTS, gains):
            x = c.freq_to_x(freq)
            y = c.gain_to_y(max(_GAIN_RANGE[0], min(_GAIN_RANGE[1], gain)))
            if not moved:
                path.moveTo(x, y); moved = True
            else:
                path.lineTo(x, y)
        p.setPen(QPen(QColor("#FFFFFF"), 2))
        p.drawPath(path)

        # Dots
        dot_font = QFont()
        dot_font.setPixelSize(9)
        dot_font.setBold(True)
        for i, band in enumerate(self._bands):
            color = QColor(BAND_COLORS[i % len(BAND_COLORS)])
            bx = c.freq_to_x(max(_FREQ_RANGE[0], min(_FREQ_RANGE[1], band.freq)))
            by = c.gain_to_y(max(_GAIN_RANGE[0], min(_GAIN_RANGE[1], band.gain)))

            if not band.enabled:
                color.setAlphaF(0.35)

            if i == self._selected:
                p.setPen(QPen(QColor("#FFFFFF"), 2))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(QPointF(bx, by), _DOT_R + 4, _DOT_R + 4)

            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color)
            p.drawEllipse(QPointF(bx, by), _DOT_R, _DOT_R)

            # Index label inside dot
            p.setFont(dot_font)
            p.setPen(QColor("#000000" if color.lightness() > 128 else "#FFFFFF"))
            lbl = str(i + 1)
            fw = p.fontMetrics().horizontalAdvance(lbl)
            p.drawText(int(bx - fw / 2), int(by + p.fontMetrics().height() / 3 - 1), lbl)

        p.end()

    # ── Mouse ────────────────────────────────────────────────────────────────

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position()
        hit = self._hit_band(pos.x(), pos.y())
        if hit is not None:
            self._selected = hit
            self._drag_band = hit
            self._drag_start_pos = (pos.x(), pos.y())
        else:
            c = self._canvas()
            if c.rect.contains(pos):
                freq = round(c.x_to_freq(pos.x()), 1)
                gain = round(c.y_to_gain(pos.y()), 1)
                self._bands.append(EqBand(freq=freq, gain=gain))
                self._selected = len(self._bands) - 1
                self._drag_band = self._selected
                self._drag_start_pos = (pos.x(), pos.y())
                self.bands_changed.emit(self.get_bands())
            else:
                self._selected = None
                self._inspector.hide()
                self.update()
                return

        self._show_inspector(self._selected)
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._drag_band is None:
            return
        pos = event.position()
        c = self._canvas()
        b = self._bands[self._drag_band]
        b.freq = round(max(_FREQ_RANGE[0], min(_FREQ_RANGE[1], c.x_to_freq(pos.x()))), 1)
        b.gain = round(max(_GAIN_RANGE[0], min(_GAIN_RANGE[1], c.y_to_gain(pos.y()))), 2)
        self._update_inspector(self._drag_band)
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent):
        if self._drag_band is not None:
            self.bands_changed.emit(self.get_bands())
        self._drag_band = None
        self._drag_start_pos = None

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        pos = event.position()
        hit = self._hit_band(pos.x(), pos.y())
        if hit is not None:
            self._bands.pop(hit)
            self._selected = None
            self._inspector.hide()
            self.bands_changed.emit(self.get_bands())
            self.update()

    def wheelEvent(self, event: QWheelEvent):
        if self._selected is None or self._selected >= len(self._bands):
            event.ignore()
            return
        b = self._bands[self._selected]
        delta = event.angleDelta().y() / 120
        b.q = round(max(0.1, min(10.0, b.q + delta * 0.1)), 3)
        self._update_inspector(self._selected)
        self.bands_changed.emit(self.get_bands())
        self.update()
        event.accept()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._selected is not None and self._selected < len(self._bands):
            self._position_inspector(self._selected)

    # ── Inspector ────────────────────────────────────────────────────────────

    def _show_inspector(self, idx: int):
        self._inspector.set_band(self._bands[idx], idx)
        self._position_inspector(idx)
        self._inspector.show()
        self._inspector.raise_()

    def _update_inspector(self, idx: int):
        if self._inspector.isVisible():
            self._inspector.set_band(self._bands[idx], idx)
            self._position_inspector(idx)

    def _position_inspector(self, idx: int):
        c = self._canvas()
        b = self._bands[idx]
        bx = int(c.freq_to_x(b.freq))
        by = int(c.gain_to_y(b.gain))
        iw = self._inspector.sizeHint().width()
        ih = self._inspector.sizeHint().height()
        x = bx + _DOT_R + 10
        if x + iw > self.width() - 4:
            x = bx - _DOT_R - 10 - iw
        y = max(4, min(by - ih // 2, self.height() - ih - 4))
        self._inspector.move(x, y)

    def _on_band_edited(self, idx: int, band: EqBand):
        if 0 <= idx < len(self._bands):
            self._bands[idx] = band
            self.bands_changed.emit(self.get_bands())
            self.update()


# ── Band inspector overlay ────────────────────────────────────────────────────

class _BandInspector(QWidget):
    band_edited = Signal(int, EqBand)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._idx: int | None = None
        self._band: EqBand | None = None
        self._updating = False

        self.setStyleSheet(f"""
            _BandInspector, QWidget#inspector {{
                background-color: #1E242D;
                border: 1px solid {BORDER};
                border-radius: 8px;
            }}
            QLabel  {{ background: transparent; border: none; }}
            QLineEdit {{
                background: {BG_CARD};
                border: 1px solid {BORDER};
                border-radius: 4px;
                color: {TEXT_PRIMARY};
                padding: 2px 4px;
                font-size: 10pt;
            }}
            QLineEdit:focus {{ border-color: {ACCENT}; }}
            QComboBox {{
                background: {BG_CARD};
                border: 1px solid {BORDER};
                border-radius: 4px;
                color: {TEXT_PRIMARY};
                padding: 2px 6px;
                font-size: 10pt;
            }}
        """)
        self.setAutoFillBackground(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(6)

        # Header row: colored status + filter type combo
        header = QHBoxLayout()
        header.setSpacing(8)
        self._status = QLabel("● Enabled")
        self._status.setStyleSheet(f"font-size: 10pt; color: {TEXT_PRIMARY};")
        header.addWidget(self._status)

        self._type_combo = QComboBox()
        for t in FILTER_TYPES:
            self._type_combo.addItem(FILTER_LABELS[t], t)
        self._type_combo.setFixedWidth(112)
        self._type_combo.currentIndexChanged.connect(self._on_type_changed)
        header.addWidget(self._type_combo)
        root.addLayout(header)

        # Gain / Freq / Q fields
        fields = QHBoxLayout()
        fields.setSpacing(6)
        self._gain_edit, gw = self._make_field("Gain", "dB")
        self._freq_edit, fw = self._make_field("Freq", "Hz")
        self._q_edit,    qw = self._make_field("Q", "")
        fields.addWidget(gw); fields.addWidget(fw); fields.addWidget(qw)
        root.addLayout(fields)

        self._gain_edit.editingFinished.connect(self._on_gain_edited)
        self._freq_edit.editingFinished.connect(self._on_freq_edited)
        self._q_edit.editingFinished.connect(self._on_q_edited)

        self.adjustSize()

    def _make_field(self, label: str, unit: str) -> tuple[QLineEdit, QWidget]:
        w = QWidget()
        w.setStyleSheet("background: transparent; border: none;")
        vl = QVBoxLayout(w)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(2)
        edit = QLineEdit()
        edit.setFixedWidth(62)
        edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        caption = f"{label} ({unit})" if unit else label
        lbl = QLabel(caption)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 9pt;")
        vl.addWidget(edit)
        vl.addWidget(lbl)
        return edit, w

    def set_band(self, band: EqBand, idx: int):
        self._idx = idx
        self._band = EqBand(band.freq, band.gain, band.q, band.type, band.enabled)
        self._updating = True
        color = BAND_COLORS[idx % len(BAND_COLORS)]
        self._status.setText("● Enabled" if band.enabled else "○ Disabled")
        self._status.setStyleSheet(f"font-size: 10pt; color: {color}; background: transparent;")
        self._gain_edit.setText(f"{band.gain:.1f}")
        self._freq_edit.setText(f"{band.freq:.1f}")
        self._q_edit.setText(f"{band.q:.3f}")
        tidx = FILTER_TYPES.index(band.type) if band.type in FILTER_TYPES else 0
        self._type_combo.setCurrentIndex(tidx)
        self._updating = False
        self.adjustSize()

    def _emit(self):
        if not self._updating and self._idx is not None and self._band is not None:
            self.band_edited.emit(self._idx, EqBand(
                self._band.freq, self._band.gain, self._band.q,
                self._band.type, self._band.enabled,
            ))

    def _on_gain_edited(self):
        if self._updating or self._band is None:
            return
        try:
            v = max(_GAIN_RANGE[0], min(_GAIN_RANGE[1], float(self._gain_edit.text())))
            self._band.gain = v
            self._gain_edit.setText(f"{v:.1f}")
            self._emit()
        except ValueError:
            self._gain_edit.setText(f"{self._band.gain:.1f}")

    def _on_freq_edited(self):
        if self._updating or self._band is None:
            return
        try:
            v = max(_FREQ_RANGE[0], min(_FREQ_RANGE[1], float(self._freq_edit.text())))
            self._band.freq = v
            self._freq_edit.setText(f"{v:.1f}")
            self._emit()
        except ValueError:
            self._freq_edit.setText(f"{self._band.freq:.1f}")

    def _on_q_edited(self):
        if self._updating or self._band is None:
            return
        try:
            v = max(0.1, min(10.0, float(self._q_edit.text())))
            self._band.q = v
            self._q_edit.setText(f"{v:.3f}")
            self._emit()
        except ValueError:
            self._q_edit.setText(f"{self._band.q:.3f}")

    def _on_type_changed(self, idx: int):
        if self._updating or self._band is None:
            return
        self._band.type = FILTER_TYPES[idx]
        self._emit()
