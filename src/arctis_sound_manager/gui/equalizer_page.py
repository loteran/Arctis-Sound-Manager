"""
Equalizer page — EQ mode toggle (Sonar / Custom) + 10-band EQ sliders + presets.
"""
import json
import subprocess
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from arctis_sound_manager.gui.components import AccentButton
from arctis_sound_manager.gui.sonar_page import SonarPage
from arctis_sound_manager.gui.dbus_wrapper import DbusWrapper
from arctis_sound_manager.sonar_to_pipewire import generate_virtual_sinks_conf, ensure_sonar_eq_configs
from arctis_sound_manager.gui.theme import (
    ACCENT,
    BG_CARD,
    BG_MAIN,
    BORDER,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)

STATE_FILE     = Path.home() / ".config" / "arctis_manager" / ".eq_mode"
_DEVICES_DIR   = Path.home() / ".config" / "arctis_manager" / "devices"
PRESETS_FILE   = Path.home() / ".config" / "arctis_manager" / "eq_presets.json"
_OVERRIDES_FILE = Path.home() / ".config" / "arctis_manager" / "routing_overrides.json"

# With the loopback-based routing, apps always target Arctis_Game/Chat.
# The virtual sinks conf routes to EQ or hardware based on mode.
# On toggle, fix any overrides that still point to effect_input sinks.
_SINK_REMAP = {
    "sonar": {
        "effect_input.sonar-game-eq": "Arctis_Game",
        "effect_input.sonar-chat-eq": "Arctis_Chat",
    },
    "custom": {
        "effect_input.sonar-game-eq": "Arctis_Game",
        "effect_input.sonar-chat-eq": "Arctis_Chat",
    },
}

EQ_BANDS = ["31", "62", "125", "250", "500", "1K", "2K", "4K", "8K", "16K"]

_SONAR_ON  = {
    "[0x06, 0x3b, 0x01]": "[0x06, 0x3b, 0x00]",
    "[0x06, 0x8d, 0x00]": "[0x06, 0x8d, 0x01]",
    "[0x06, 0x49, 0x00]": "[0x06, 0x49, 0x01]",
}
_SONAR_OFF = {v: k for k, v in _SONAR_ON.items()}

# Slider geometry constants (shared by _TickSlider and _ScaleWidget)
SLIDER_H   = 220
SLIDER_M   = 8    # top/bottom margin inside slider
FREQ_LBL_H = 24   # approx height of frequency label + spacing
VAL_LBL_H  = 26   # approx height of value lineedit + spacing


def _current_mode() -> str:
    return STATE_FILE.read_text().strip() if STATE_FILE.exists() else "custom"


def _apply_yaml(mode: str) -> bool:
    try:
        swaps = _SONAR_ON if mode == "sonar" else _SONAR_OFF
        for yaml_file in sorted(_DEVICES_DIR.glob("*.yaml")):
            content = yaml_file.read_text()
            new_content = content
            for old, new in swaps.items():
                new_content = new_content.replace(old, new)
            if new_content != content:
                yaml_file.write_text(new_content)
        return True
    except Exception:
        return False


def _load_presets() -> dict[str, list[int]]:
    if PRESETS_FILE.exists():
        try:
            return json.loads(PRESETS_FILE.read_text())
        except Exception:
            pass
    return {}


def _save_presets(presets: dict[str, list[int]]) -> None:
    PRESETS_FILE.write_text(json.dumps(presets, indent=2))


def _update_routing_overrides(new_mode: str) -> None:
    """Remap routing_overrides.json sink names when switching EQ mode."""
    remap = _SINK_REMAP.get(new_mode, {})
    if not remap or not _OVERRIDES_FILE.exists():
        return
    try:
        overrides = json.loads(_OVERRIDES_FILE.read_text())
        updated = {k: remap.get(v, v) for k, v in overrides.items()}
        if updated != overrides:
            _OVERRIDES_FILE.write_text(json.dumps(updated, indent=2))
    except Exception:
        pass


def _round_to_half(db: float) -> float:
    """Round to nearest 0.5 dB and clamp to [-10, +10]."""
    db = max(-10.0, min(10.0, db))
    return round(db * 2) / 2


# ── Background worker ─────────────────────────────────────────────────────────

class _ToggleWorker(QThread):
    countdown_tick = Signal(int)
    done = Signal(bool, str)

    # Expected sink/source nodes per mode
    _MODE_NODES = {
        "sonar": {
            "sinks":   ["effect_input.sonar-game-eq", "effect_input.sonar-chat-eq"],
            "sources": ["effect_output.sonar-micro-eq"],
        },
        "custom": {
            "sinks":   ["Arctis_Game", "Arctis_Chat"],
            "sources": [],
        },
    }

    def __init__(self, new_mode: str, old_mode: str):
        super().__init__()
        self._new_mode = new_mode
        self._old_mode = old_mode

    # ── helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _snapshot_streams(log) -> tuple[list[str], list[str]]:
        """Return (sink_input_ids, source_output_ids) before restart."""
        si, so = [], []
        try:
            r = subprocess.run(["pactl", "list", "sink-inputs", "short"],
                               capture_output=True, text=True, timeout=3)
            si = [l.split()[0] for l in r.stdout.splitlines() if l.strip()]
        except Exception as e:
            log.warning("Could not snapshot sink-inputs: %s", e)
        try:
            r = subprocess.run(["pactl", "list", "source-outputs", "short"],
                               capture_output=True, text=True, timeout=3)
            so = [l.split()[0] for l in r.stdout.splitlines() if l.strip()]
        except Exception as e:
            log.warning("Could not snapshot source-outputs: %s", e)
        return si, so

    @staticmethod
    def _wait_for_node(node_name: str, timeout_ms: int = 8000) -> bool:
        """Poll pw-cli until *node_name* appears (or timeout)."""
        import time
        deadline = time.monotonic() + timeout_ms / 1000.0
        while time.monotonic() < deadline:
            try:
                r = subprocess.run(["pw-cli", "list-objects", "Node"],
                                   capture_output=True, text=True, timeout=3)
                if node_name in r.stdout:
                    return True
            except Exception:
                pass
            time.sleep(0.25)
        return False

    @staticmethod
    def _find_pactl_index(name: str, kind: str, log) -> str | None:
        """Find pactl numeric index of a sink or source by node name."""
        cmd = "sinks" if kind == "sink" else "sources"
        try:
            r = subprocess.run(["pactl", "list", cmd, "short"],
                               capture_output=True, text=True, timeout=3)
            for line in r.stdout.splitlines():
                if name in line:
                    return line.split()[0]
        except Exception as e:
            log.warning("Could not list %s: %s", cmd, e)
        return None

    @staticmethod
    def _move_streams(ids: list[str], target_idx: str, kind: str, log) -> None:
        """Move sink-inputs or source-outputs with retry."""
        import time
        cmd = "move-sink-input" if kind == "sink" else "move-source-output"
        for sid in ids:
            for attempt in range(3):
                r = subprocess.run(["pactl", cmd, sid, target_idx],
                                   capture_output=True, text=True, check=False, timeout=3)
                if r.returncode == 0:
                    break
                if attempt < 2:
                    time.sleep(0.3 * (attempt + 1))
                else:
                    log.warning("Failed to %s %s → %s: %s",
                                cmd, sid, target_idx, r.stderr.strip())

    def _restore_streams(self, saved_si: list[str], saved_so: list[str], log) -> None:
        """Wait for expected nodes, set defaults, and move saved streams."""
        import json as _json

        nodes = self._MODE_NODES.get(self._new_mode, {})
        expected_sinks = nodes.get("sinks", [])
        expected_sources = nodes.get("sources", [])

        # Wait for the primary sink to appear
        if expected_sinks:
            primary_sink = expected_sinks[0]
            if not self._wait_for_node(primary_sink):
                log.warning("Node %s did not appear after toggle", primary_sink)
                return

            # Set default sink
            sink_json = _json.dumps({"name": primary_sink})
            subprocess.run(["pw-metadata", "0", "default.configured.audio.sink", sink_json],
                           check=False, timeout=5)
            subprocess.run(["pw-metadata", "0", "default.audio.sink", sink_json],
                           check=False, timeout=5)

            # Move saved sink-inputs to primary sink
            if saved_si:
                idx = self._find_pactl_index(primary_sink, "sink", log)
                if idx:
                    self._move_streams(saved_si, idx, "sink", log)

        # Wait for the source to appear and restore mic streams
        if expected_sources:
            primary_source = expected_sources[0]
            if self._wait_for_node(primary_source):
                source_json = _json.dumps({"name": primary_source})
                subprocess.run(["pw-metadata", "0", "default.configured.audio.source",
                                source_json], check=False, timeout=5)
                subprocess.run(["pw-metadata", "0", "default.audio.source",
                                source_json], check=False, timeout=5)

                if saved_so:
                    idx = self._find_pactl_index(primary_source, "source", log)
                    if idx:
                        self._move_streams(saved_so, idx, "source", log)
            else:
                log.warning("Source node %s did not appear after toggle", primary_source)

    # ── main ───────────────────────────────────────────────────────────────

    def run(self):
        import logging
        log = logging.getLogger(__name__)

        if not _apply_yaml(self._new_mode):
            self.done.emit(False, self._old_mode)
            return

        # Ensure sonar EQ filter-chain configs exist before updating virtual sinks:
        # effect_input.sonar-game-eq and sonar-chat-eq must exist as PipeWire nodes
        # or Arctis_Game will connect to a non-existent target → silent game channel.
        if self._new_mode == 'sonar':
            ensure_sonar_eq_configs()

        # Update virtual sink targets before restarting PipeWire
        generate_virtual_sinks_conf(sonar=(self._new_mode == 'sonar'))

        # Snapshot active streams BEFORE restarting PipeWire
        saved_si, saved_so = self._snapshot_streams(log)

        # Phase 1: restart PipeWire stack and wait for ALSA sinks to be recreated
        result = subprocess.run(
            ["systemctl", "--user", "restart", "pipewire", "wireplumber", "pipewire-pulse"],
            check=False, timeout=20,
        )
        if result.returncode != 0:
            _apply_yaml(self._old_mode)
            self.done.emit(False, self._old_mode)
            return

        # Wait for WirePlumber to recreate ALSA sink nodes before starting filter-chain
        self.msleep(2000)

        # Phase 2: restart filter-chain and arctis-manager
        result = subprocess.run(
            ["systemctl", "--user", "restart", "filter-chain", "arctis-manager"],
            check=False, timeout=20,
        )
        if result.returncode != 0:
            _apply_yaml(self._old_mode)
            self.done.emit(False, self._old_mode)
            return

        for remaining in range(5, 0, -1):
            self.countdown_tick.emit(remaining)
            self.msleep(1000)

        # Restore streams to the correct sinks/sources for the new mode
        self._restore_streams(saved_si, saved_so, log)

        STATE_FILE.write_text(self._new_mode)
        _update_routing_overrides(self._new_mode)
        subprocess.run(
            ["notify-send", "-a", "Arctis EQ", "Arctis EQ",
             f'{"Sonar" if self._new_mode == "sonar" else "Custom EQ"} mode enabled'],
            check=False, timeout=5,
        )
        self.done.emit(True, self._new_mode)


# ── Slider with painted tick marks and color bar ──────────────────────────────

class _TickSlider(QSlider):
    GROOVE_WIDTH = 6
    TICK_SHORT   = 5
    TICK_LONG    = 12
    TICK_COLOR   = QColor("#555555")
    TICK_ZERO    = QColor("#999999")
    COLOR_POS    = QColor("#FF6600")
    COLOR_NEG    = QColor("#00AAFF")

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        total   = self.maximum() - self.minimum()
        cx      = self.width() // 2
        track_h = self.height() - 2 * SLIDER_M

        def y_for(val_units: int) -> int:
            return SLIDER_M + int((total - (val_units - self.minimum())) / total * track_h)

        # Colored bar from 0 to current value
        v = self.value()
        if v != 0:
            y_zero   = y_for(0)
            y_handle = y_for(v)
            color    = self.COLOR_POS if v > 0 else self.COLOR_NEG
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            half_g = self.GROOVE_WIDTH // 2
            if v > 0:
                painter.drawRect(cx - half_g, y_handle, self.GROOVE_WIDTH, y_zero - y_handle)
            else:
                painter.drawRect(cx - half_g, y_zero, self.GROOVE_WIDTH, y_handle - y_zero)

        # Tick marks
        for i in range(total + 1):
            val = self.minimum() + i
            if val % 2 != 0:
                continue
            y      = y_for(val)
            is_key = (val % 10 == 0)
            length = self.TICK_LONG if is_key else self.TICK_SHORT
            color  = self.TICK_ZERO if val == 0 else self.TICK_COLOR
            painter.setPen(QPen(color, 1))
            half_g = self.GROOVE_WIDTH // 2 + 2
            painter.drawLine(cx - half_g - length, y, cx - half_g, y)
            painter.drawLine(cx + half_g,           y, cx + half_g + length, y)

        painter.end()


# ── dB scale labels (left or right of sliders) ───────────────────────────────

class _ScaleWidget(QWidget):
    """Vertical dB scale: +10, +5, 0, -5, -10 aligned to slider track."""

    _LABELS = [(20, "+10"), (10, "+5"), (0, "0"), (-10, "-5"), (-20, "-10")]

    def __init__(self, align_right: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        self._align_right = align_right
        self.setFixedWidth(46)
        self.setFixedHeight(FREQ_LBL_H + SLIDER_H + VAL_LBL_H)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        font = QFont()
        font.setPixelSize(14)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QPen(QColor("#666666"), 1))

        total   = 40
        track_h = SLIDER_H - 2 * SLIDER_M
        w       = self.width()

        for val, text in self._LABELS:
            y_in_track = SLIDER_M + int((total - (val + 20)) / total * track_h)
            y = FREQ_LBL_H + y_in_track

            fm   = painter.fontMetrics()
            th   = fm.height()
            tw   = fm.horizontalAdvance(text)

            if self._align_right:
                x = w - tw - 2
            else:
                x = 2

            painter.drawText(x, y + th // 2 - 2, text)

        painter.end()


# ── Single band slider ────────────────────────────────────────────────────────

class _EqSlider(QWidget):
    value_changed = Signal(int, int)  # band_index, raw_value (0-40)

    def __init__(self, index: int, label: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._index = index

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(4)
        layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        freq = QLabel(label)
        freq.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        freq.setStyleSheet(f"font-size: 13px; font-weight: bold; color: {TEXT_SECONDARY}; background: transparent;")
        layout.addWidget(freq)

        self._slider = _TickSlider(Qt.Orientation.Vertical)
        self._slider.setMinimum(-20)
        self._slider.setMaximum(20)
        self._slider.setValue(0)
        self._slider.setFixedHeight(SLIDER_H)  # synced with SLIDER_H constant
        self._slider.setMinimumWidth(50)
        self._slider.setStyleSheet(f"""
            QSlider::groove:vertical {{
                background: #333;
                width: 6px;
                border-radius: 3px;
            }}
            QSlider::handle:vertical {{
                background: white;
                width: 16px;
                height: 16px;
                margin: 0 -5px;
                border-radius: 8px;
            }}
            QSlider::sub-page:vertical {{ background: transparent; }}
            QSlider::add-page:vertical  {{ background: transparent; }}
        """)
        self._slider.valueChanged.connect(self._on_value_changed)
        layout.addWidget(self._slider, alignment=Qt.AlignmentFlag.AlignHCenter)

        self._val_edit = QLineEdit("0")
        self._val_edit.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._val_edit.setFixedWidth(52)
        self._val_edit.setStyleSheet(f"""
            QLineEdit {{
                background: transparent;
                border: none;
                border-bottom: 1px solid transparent;
                color: {TEXT_PRIMARY};
                font-size: 13px;
                font-weight: bold;
                padding: 1px 0;
            }}
            QLineEdit:focus {{
                border-bottom: 1px solid {ACCENT};
            }}
        """)
        self._val_edit.editingFinished.connect(self._on_value_edited)
        layout.addWidget(self._val_edit, alignment=Qt.AlignmentFlag.AlignHCenter)

    def _format_db(self, slider_val: int) -> str:
        db = slider_val * 0.5
        if db == 0:
            return "0"
        return f"{db:+.1f}"

    def _on_value_changed(self, v: int):
        self._val_edit.setText(self._format_db(v))
        self.value_changed.emit(self._index, v + 20)

    def _on_value_edited(self):
        text = self._val_edit.text().replace(",", ".")
        try:
            db      = _round_to_half(float(text))
            new_val = int(db * 2)
            self._slider.setValue(new_val)
            # update display in case value didn't change (no signal)
            self._val_edit.setText(self._format_db(new_val))
        except ValueError:
            self._val_edit.setText(self._format_db(self._slider.value()))

    def set_raw_value(self, raw: int):
        self._slider.blockSignals(True)
        v = raw - 20
        self._slider.setValue(v)
        self._val_edit.setText(self._format_db(v))
        self._slider.blockSignals(False)


# ── Load preset dialog ────────────────────────────────────────────────────────

class _LoadPresetDialog(QDialog):
    def __init__(self, presets: dict[str, list[int]], parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Load preset")
        self.setMinimumWidth(300)
        self.selected_bands: list[int] | None = None

        layout = QVBoxLayout(self)

        self._list = QListWidget()
        self._list.setStyleSheet(f"""
            QListWidget {{
                background: {BG_CARD};
                color: {TEXT_PRIMARY};
                border: 1px solid {BORDER};
                border-radius: 6px;
            }}
            QListWidget::item:selected {{ background: {ACCENT}; color: #000; }}
        """)
        for name in presets:
            self._list.addItem(QListWidgetItem(name))
        self._list.itemDoubleClicked.connect(self.accept)
        layout.addWidget(self._list)

        del_btn = QPushButton("Delete")
        del_btn.setStyleSheet("color: #f44; background: transparent; border: 1px solid #f44; border-radius: 4px; padding: 4px 10px;")
        del_btn.clicked.connect(self._on_delete)
        layout.addWidget(del_btn)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._presets = presets

    def _on_delete(self):
        item = self._list.currentItem()
        if item:
            del self._presets[item.text()]
            _save_presets(self._presets)
            self._list.takeItem(self._list.row(item))

    def accept(self):
        item = self._list.currentItem()
        if item:
            self.selected_bands = self._presets[item.text()]
        super().accept()


# ── Main page ─────────────────────────────────────────────────────────────────

class EqualizerPage(QWidget):
    _sig_eq_bands = Signal(list)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setStyleSheet(f"background-color: {BG_MAIN};")
        self._band_values: list[int] = [20] * 10
        self._worker: _ToggleWorker | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(36, 28, 36, 28)
        root.setSpacing(0)

        app_title = QLabel("Arctis Sound Manager")
        app_title.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: 28pt; font-weight: bold; background: transparent;")
        root.addWidget(app_title)
        root.addSpacing(28)

        self._eq_title = QLabel("Equalizer")
        self._eq_title.setStyleSheet("color: #666666; font-size: 20pt; font-weight: bold; background: transparent;")
        root.addWidget(self._eq_title)
        root.addSpacing(20)

        # ── Mode card ────────────────────────────────────────────────────────
        self._card = QWidget()
        self._card.setObjectName("eqCard")
        self._card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._card.setStyleSheet(f"""
            QWidget#eqCard {{
                background-color: {BG_CARD};
                border: 1px solid {BORDER};
                border-radius: 12px;
            }}
        """)

        card_layout = QVBoxLayout(self._card)
        card_layout.setContentsMargins(28, 24, 28, 24)
        card_layout.setSpacing(16)

        mode_row = QWidget()
        mode_row.setStyleSheet("background: transparent;")
        mrl = QHBoxLayout(mode_row)
        mrl.setContentsMargins(0, 0, 0, 0)
        mrl.setSpacing(12)

        ms = QLabel("Current mode:")
        ms.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 11pt; background: transparent;")
        mrl.addWidget(ms)

        self._mode_label = QLabel()
        self._mode_label.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: 13pt; font-weight: bold; background: transparent;")
        mrl.addWidget(self._mode_label)
        mrl.addStretch(1)
        card_layout.addWidget(mode_row)

        self._desc_label = QLabel()
        self._desc_label.setWordWrap(True)
        self._desc_label.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 10pt; background: transparent;")
        card_layout.addWidget(self._desc_label)

        self._button = AccentButton("")
        self._button.clicked.connect(self._on_toggle)
        card_layout.addWidget(self._button)

        root.addWidget(self._card)
        root.addSpacing(24)

        # ── EQ sliders card ──────────────────────────────────────────────────
        self._eq_card = QWidget()
        self._eq_card.setObjectName("eqSlidersCard")
        self._eq_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self._eq_card.setStyleSheet(f"""
            QWidget#eqSlidersCard {{
                background-color: {BG_CARD};
                border: 1px solid {BORDER};
                border-radius: 12px;
            }}
        """)

        eq_card_layout = QVBoxLayout(self._eq_card)
        eq_card_layout.setContentsMargins(24, 20, 24, 20)
        eq_card_layout.setSpacing(12)

        eq_card_title = QLabel("Custom EQ")
        eq_card_title.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: 12pt; font-weight: bold; background: transparent;")
        eq_card_layout.addWidget(eq_card_title)

        # Sliders row with scale widgets on each side
        sliders_container = QWidget()
        sliders_container.setStyleSheet("background: transparent;")
        sc_layout = QHBoxLayout(sliders_container)
        sc_layout.setContentsMargins(0, 0, 0, 0)
        sc_layout.setSpacing(4)

        sc_layout.addWidget(_ScaleWidget(align_right=True))

        self._sliders: list[_EqSlider] = []
        for i, label in enumerate(EQ_BANDS):
            s = _EqSlider(i, label)
            s.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            s.value_changed.connect(self._on_slider_changed)
            sc_layout.addWidget(s)
            self._sliders.append(s)

        sc_layout.addWidget(_ScaleWidget(align_right=False))

        eq_card_layout.addWidget(sliders_container)

        # Preset buttons
        preset_row = QWidget()
        preset_row.setStyleSheet("background: transparent;")
        pl = QHBoxLayout(preset_row)
        pl.setContentsMargins(0, 4, 0, 0)
        pl.setSpacing(10)

        save_btn = QPushButton("Save preset")
        save_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {ACCENT};
                border: 1px solid {ACCENT}; border-radius: 6px;
                padding: 6px 14px; font-size: 10pt;
            }}
            QPushButton:hover {{ background: {ACCENT}22; }}
        """)
        save_btn.clicked.connect(self._on_save_preset)
        pl.addWidget(save_btn)

        load_btn = QPushButton("Load preset")
        load_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {TEXT_SECONDARY};
                border: 1px solid {BORDER}; border-radius: 6px;
                padding: 6px 14px; font-size: 10pt;
            }}
            QPushButton:hover {{ background: #ffffff11; }}
        """)
        load_btn.clicked.connect(self._on_load_preset)
        pl.addWidget(load_btn)

        pl.addStretch(1)

        self._preset_status = QLabel()
        self._preset_status.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 9pt; background: transparent;")
        pl.addWidget(self._preset_status)

        eq_card_layout.addWidget(preset_row)
        root.addWidget(self._eq_card)
        self._custom_stretch = root.count()   # index of stretch spacer (custom mode)
        root.addStretch(1)

        # ── Sonar page — wrapped in a single QScrollArea for the whole page ────
        self._sonar_page = SonarPage(embedded=True)
        self._sonar_scroll = QScrollArea()
        self._sonar_scroll.setWidget(self._sonar_page)
        self._sonar_scroll.setWidgetResizable(True)
        self._sonar_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._sonar_scroll.setStyleSheet("background: transparent;")
        self._sonar_scroll.setVisible(False)
        root.addWidget(self._sonar_scroll, 1)

        self._sig_eq_bands.connect(self._on_eq_bands_received)
        self._refresh()

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(500)
        self._poll_timer.timeout.connect(lambda: DbusWrapper.get_eq_bands(self._sig_eq_bands))

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def showEvent(self, event):
        super().showEvent(event)
        DbusWrapper.get_eq_bands(self._sig_eq_bands)
        self._poll_timer.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._poll_timer.stop()

    # ── EQ ───────────────────────────────────────────────────────────────────

    @Slot(list)
    def _on_eq_bands_received(self, bands: list):
        self._band_values = list(bands)
        for i, slider in enumerate(self._sliders):
            slider.set_raw_value(bands[i])

    def _on_slider_changed(self, index: int, raw: int):
        self._band_values[index] = raw
        self._poll_timer.stop()
        DbusWrapper.send_eq_command(list(self._band_values))
        self._preset_status.setText("")
        self._poll_timer.start()

    # ── Presets ──────────────────────────────────────────────────────────────

    def _on_save_preset(self):
        name, ok = QInputDialog.getText(self, "Save preset", "Preset name:")
        if not ok or not name.strip():
            return
        presets = _load_presets()
        presets[name.strip()] = list(self._band_values)
        _save_presets(presets)
        self._preset_status.setText(f"Saved: {name.strip()}")

    def _on_load_preset(self):
        presets = _load_presets()
        if not presets:
            self._preset_status.setText("No preset saved")
            return
        dialog = _LoadPresetDialog(presets, self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.selected_bands:
            self._on_eq_bands_received(dialog.selected_bands)
            DbusWrapper.send_eq_command(list(self._band_values))
            self._preset_status.setText("Preset loaded")

    # ── Toggle ───────────────────────────────────────────────────────────────

    def _refresh(self):
        mode = _current_mode()
        root = self.layout()
        stretch_item = root.itemAt(self._custom_stretch)
        if mode == "sonar":
            self._eq_title.setText("Sonar")
            self._mode_label.setText("Sonar")
            self._mode_label.setStyleSheet(f"color: {ACCENT}; font-size: 13pt; font-weight: bold; background: transparent;")
            self._desc_label.setText("SteelSeries Sonar audio processing is active. Click to switch back to your Custom EQ.")
            self._button.setText("Switch to Custom EQ")
            self._eq_card.setVisible(False)
            self._sonar_scroll.setVisible(True)
            if stretch_item:
                root.setStretch(self._custom_stretch, 0)
        else:
            self._eq_title.setText("Equalizer")
            self._mode_label.setText("Custom EQ")
            self._mode_label.setStyleSheet("color: #04C5A8; font-size: 13pt; font-weight: bold; background: transparent;")
            self._desc_label.setText("Your Custom EQ is active. Click to enable Sonar processing.")
            self._button.setText("Switch to Sonar")
            self._eq_card.setVisible(True)
            self._sonar_scroll.setVisible(False)
            if stretch_item:
                root.setStretch(self._custom_stretch, 1)

    @Slot()
    def _on_toggle(self):
        old_mode = _current_mode()
        new_mode = "sonar" if old_mode == "custom" else "custom"
        self._button.setEnabled(False)
        self._button.setText("Restarting audio...")
        self._worker = _ToggleWorker(new_mode, old_mode)
        self._worker.countdown_tick.connect(self._on_countdown)
        self._worker.done.connect(self._on_toggle_done)
        self._worker.start()

    @Slot(int)
    def _on_countdown(self, remaining: int):
        self._button.setText(f"Please wait... {remaining}s")

    @Slot(bool, str)
    def _on_toggle_done(self, success: bool, mode: str):
        if not success:
            self._desc_label.setText('<span style="color:red;">Failed to switch mode.</span>')
        self._refresh()
        self._button.setEnabled(True)
        self._worker = None
        if success and mode == "custom":
            DbusWrapper.get_eq_bands(self._sig_eq_bands)
