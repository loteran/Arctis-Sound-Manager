"""
sonar_page.py — Full Sonar mode UI: Game / Chat / Micro tabs.

Each tab contains:
  - PresetBar    (active preset + search + 9 favorite slots)
  - EqCurveWidget (interactive parametric EQ)
  - MacroSliders  (Basses / Voix / Aigus)

Changes are applied to PipeWire filter-chain via sonar_to_pipewire.py.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from PySide6.QtCore import QThread, Qt, QTimer, Signal, Slot
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from linux_arctis_manager.gui.eq_curve_widget import EqBand, EqCurveWidget
from linux_arctis_manager.gui.qt_widgets.q_toggle import QToggle
from linux_arctis_manager.gui.theme import (
    ACCENT,
    BG_BUTTON,
    BG_BUTTON_HOVER,
    BG_CARD,
    BG_MAIN,
    BORDER,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)
from linux_arctis_manager.sonar_to_pipewire import (
    _MACRO_PARAMS as MACRO_PARAMS,
    generate_sonar_eq_conf,
    generate_sonar_micro_conf,
)

# ── Paths ─────────────────────────────────────────────────────────────────────

_CFG          = Path.home() / ".config" / "arctis_manager"
_PRESETS_DIR  = _CFG / "sonar_presets"
_RAW_DIR      = Path("/mnt/NVMe/sonar_easyeffects_presets/sonar_raw_presets")

_CHANNEL_TAG  = {"game": "[Game]", "chat": "[Chat]", "micro": "[Mic]"}
_MAX_FAV      = 9
_APPLY_DELAY  = 600   # ms debounce before restarting filter-chain

# ── Preset I/O ────────────────────────────────────────────────────────────────

def _parse_preset(path: Path) -> list[EqBand]:
    data = json.loads(path.read_text())
    eq = data.get("parametricEQ", {})
    bands: list[EqBand] = []
    for i in range(1, 11):
        f = eq.get(f"filter{i}")
        if f:
            bands.append(EqBand(
                freq=float(f.get("frequency", 1000)),
                gain=float(f.get("gain", 0)),
                q=float(f.get("qFactor", 0.707)),
                type=f.get("type", "peakingEQ"),
                enabled=bool(f.get("enabled", True)),
            ))
    return bands


def _list_presets(channel: str) -> dict[str, Path]:
    """Return {preset_name: path} for all presets matching the channel tag."""
    tag = _CHANNEL_TAG.get(channel, "[Game]")
    result: dict[str, Path] = {}

    suffix = f" {tag}.json"

    # Raw presets from NVMe
    if _RAW_DIR.exists():
        for p in sorted(_RAW_DIR.glob("*.json")):
            if p.name.endswith(suffix):
                name = p.stem[: -len(tag) - 1].strip()
                result[name] = p

    # Copied presets in config dir
    if _PRESETS_DIR.exists():
        for p in sorted(_PRESETS_DIR.glob("*.json")):
            if p.name.endswith(suffix):
                name = p.stem[: -len(tag) - 1].strip()
                if name not in result:
                    result[name] = p

    return result


def _load_favorites(channel: str) -> list[str]:
    f = _CFG / f".sonar_favorites_{channel}.json"
    if f.exists():
        try:
            return json.loads(f.read_text())
        except Exception:
            pass
    return []


def _save_favorites(channel: str, names: list[str]) -> None:
    _CFG.mkdir(parents=True, exist_ok=True)
    (_CFG / f".sonar_favorites_{channel}.json").write_text(json.dumps(names))


def _active_preset_name(channel: str) -> str:
    f = _CFG / f".sonar_preset_{channel}"
    return f.read_text().strip() if f.exists() else "Flat"


def _set_active_preset(channel: str, name: str) -> None:
    _CFG.mkdir(parents=True, exist_ok=True)
    (_CFG / f".sonar_preset_{channel}").write_text(name)


_SPATIAL_FILE = _CFG / "sonar_spatial_audio.json"
_SPATIAL_DEFAULTS: dict = {
    "enabled": True,
    "mode": "headphones",    # "headphones" | "speakers"
    "immersion": 50,         # 0–100, pending USB
    "distance": 50,          # 0–100, pending USB
}


def _load_spatial_audio() -> dict:
    if _SPATIAL_FILE.exists():
        try:
            d = json.loads(_SPATIAL_FILE.read_text())
            return {**_SPATIAL_DEFAULTS, **d}
        except Exception:
            pass
    return dict(_SPATIAL_DEFAULTS)


def _save_spatial_audio(state: dict) -> None:
    _CFG.mkdir(parents=True, exist_ok=True)
    _SPATIAL_FILE.write_text(json.dumps(state, indent=2))


def _load_macro(channel: str) -> dict[str, float]:
    f = _CFG / f"sonar_macro_{channel}.json"
    if f.exists():
        try:
            return json.loads(f.read_text())
        except Exception:
            pass
    return {"basses": 0.0, "voix": 0.0, "aigus": 0.0}


def _save_macro(channel: str, values: dict[str, float]) -> None:
    _CFG.mkdir(parents=True, exist_ok=True)
    (_CFG / f"sonar_macro_{channel}.json").write_text(json.dumps(values))


# ── Apply worker ──────────────────────────────────────────────────────────────

class _ApplyWorker(QThread):
    done = Signal(bool)

    def __init__(self, channel: str, bands: list[EqBand],
                 basses: float, voix: float, aigus: float):
        super().__init__()
        self._channel = channel
        self._bands   = bands
        self._basses  = basses
        self._voix    = voix
        self._aigus   = aigus

    def run(self):
        import logging
        log = logging.getLogger(__name__)
        try:
            boost_state = _load_boost()
            boost_db = boost_state["db"] if boost_state["enabled"] else 0.0
            if self._channel == "micro":
                generate_sonar_micro_conf(self._bands, self._basses, self._voix, self._aigus,
                                          boost_db=boost_db)
            else:
                spatial = _load_spatial_audio()["enabled"] if self._channel == "game" else True
                generate_sonar_eq_conf(self._channel, self._bands,
                                       self._basses, self._voix, self._aigus,
                                       spatial_audio=spatial, boost_db=boost_db)
            subprocess.run(["systemctl", "--user", "restart", "filter-chain"],
                           check=False, timeout=15)
            self.msleep(900)
            # Re-set default sink so WirePlumber routes correctly
            if self._channel != "micro":
                sink = f"effect_input.sonar-{self._channel}-eq"
                subprocess.run(
                    ["pw-metadata", "0", "default.configured.audio.sink",
                     f'{{"name":"{sink}"}}'],
                    check=False, timeout=5,
                )
            self.done.emit(True)
        except Exception as e:
            log.error("_ApplyWorker error (channel=%s): %s", self._channel, e)
            self.done.emit(False)


# ── Preset search dialog ──────────────────────────────────────────────────────

class _PresetSearchDialog(QDialog):
    def __init__(self, presets: dict[str, Path], parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Search preset")
        self.setMinimumSize(340, 480)
        self.selected_name: str | None = None
        self._all = list(presets.keys())

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search…")
        self._search.setStyleSheet(f"""
            QLineEdit {{
                background: {BG_BUTTON};
                border: 1px solid {BORDER};
                border-radius: 6px;
                color: {TEXT_PRIMARY};
                padding: 6px 10px;
                font-size: 11pt;
            }}
            QLineEdit:focus {{ border-color: {ACCENT}; }}
        """)
        self._search.textChanged.connect(self._filter)
        layout.addWidget(self._search)

        self._list = QListWidget()
        self._list.setStyleSheet(f"""
            QListWidget {{
                background: {BG_CARD};
                border: 1px solid {BORDER};
                border-radius: 6px;
                color: {TEXT_PRIMARY};
            }}
            QListWidget::item:selected {{ background: {ACCENT}; color: #fff; }}
            QListWidget::item:hover    {{ background: {BG_BUTTON_HOVER}; }}
        """)
        self._list.itemDoubleClicked.connect(self.accept)
        layout.addWidget(self._list)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._filter("")

    def _filter(self, text: str):
        self._list.clear()
        q = text.lower()
        for name in self._all:
            if q in name.lower():
                self._list.addItem(QListWidgetItem(name))

    def accept(self):
        item = self._list.currentItem()
        if item:
            self.selected_name = item.text()
        super().accept()


# ── Favorite slot button ──────────────────────────────────────────────────────

class _FavoriteSlot(QPushButton):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedSize(52, 42)
        self._name: str | None = None
        self._refresh()

    def set_preset(self, name: str | None):
        self._name = name
        self._refresh()

    def get_preset(self) -> str | None:
        return self._name

    def _refresh(self):
        if self._name:
            label = self._name[:6] + "…" if len(self._name) > 7 else self._name
            self.setText(label)
            self.setToolTip(self._name)
            self.setStyleSheet(f"""
                QPushButton {{
                    background: {BG_BUTTON};
                    border: 1px solid {ACCENT}44;
                    border-radius: 6px;
                    color: {TEXT_PRIMARY};
                    font-size: 8pt;
                    padding: 2px;
                }}
                QPushButton:hover {{ border-color: {ACCENT}; background: {BG_BUTTON_HOVER}; }}
            """)
        else:
            self.setText("")
            self.setToolTip("Empty slot")
            self.setStyleSheet(f"""
                QPushButton {{
                    background: {BG_CARD};
                    border: 1px dashed {BORDER};
                    border-radius: 6px;
                    color: {TEXT_SECONDARY};
                }}
                QPushButton:hover {{ border-color: {ACCENT}55; }}
            """)


# ── Preset bar ────────────────────────────────────────────────────────────────

class _PresetBar(QWidget):
    preset_selected = Signal(str, list)   # name, list[EqBand]

    def __init__(self, channel: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._channel  = channel
        self._presets  = _list_presets(channel)
        self._favs     = _load_favorites(channel)
        self._active   = _active_preset_name(channel)
        self._cur_bands: list[EqBand] = []

        self.setStyleSheet("background: transparent;")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        # ── Row 1: active preset name + actions ──────────────────────────────
        row1 = QHBoxLayout()
        row1.setSpacing(8)

        self._name_label = QLabel()
        self._name_label.setStyleSheet(
            f"font-size: 12pt; font-weight: bold; color: {TEXT_PRIMARY}; background: transparent;"
        )
        row1.addWidget(self._name_label)
        row1.addStretch(1)

        star_btn = QPushButton("★")
        star_btn.setFixedSize(32, 32)
        star_btn.setToolTip("Add to favorites")
        star_btn.setStyleSheet(self._icon_btn_ss())
        star_btn.clicked.connect(self._on_star)
        row1.addWidget(star_btn)

        more_btn = QPushButton("…")
        more_btn.setFixedSize(32, 32)
        more_btn.setToolTip("More options")
        more_btn.setStyleSheet(self._icon_btn_ss())
        more_btn.clicked.connect(self._on_more)
        row1.addWidget(more_btn)

        root.addLayout(row1)

        # ── Row 2: search + favorites label ─────────────────────────────────
        row2 = QHBoxLayout()
        row2.setSpacing(8)

        search_btn = QPushButton("🔍  Search preset")
        search_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                border: 1px solid {BORDER};
                border-radius: 6px;
                color: {TEXT_SECONDARY};
                padding: 4px 12px;
                font-size: 10pt;
            }}
            QPushButton:hover {{ border-color: {ACCENT}; color: {TEXT_PRIMARY}; }}
        """)
        search_btn.clicked.connect(self._on_search)
        row2.addWidget(search_btn)
        row2.addStretch(1)

        self._fav_count = QLabel()
        self._fav_count.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 10pt; background: transparent;")
        row2.addWidget(self._fav_count)

        root.addLayout(row2)

        # ── Row 3: 9 favorite slots ──────────────────────────────────────────
        row3 = QHBoxLayout()
        row3.setSpacing(4)
        self._slots: list[_FavoriteSlot] = []
        for i in range(_MAX_FAV):
            slot = _FavoriteSlot()
            slot.clicked.connect(lambda checked, idx=i: self._on_fav_slot(idx))
            row3.addWidget(slot)
            self._slots.append(slot)
        row3.addStretch(1)
        root.addLayout(row3)

        self._refresh_display()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _icon_btn_ss(self) -> str:
        return f"""
            QPushButton {{
                background: {BG_BUTTON};
                border: 1px solid {BORDER};
                border-radius: 6px;
                color: {TEXT_PRIMARY};
                font-size: 12pt;
            }}
            QPushButton:hover {{ background: {BG_BUTTON_HOVER}; border-color: {ACCENT}; }}
        """

    def _refresh_display(self):
        self._name_label.setText(self._active)
        self._fav_count.setText(f"Favorites ({len(self._favs)}/{_MAX_FAV})")
        for i, slot in enumerate(self._slots):
            slot.set_preset(self._favs[i] if i < len(self._favs) else None)

    def _load_and_emit(self, name: str):
        presets = _list_presets(self._channel)
        if name not in presets:
            return
        bands = _parse_preset(presets[name])
        self._cur_bands = bands
        self._active = name
        _set_active_preset(self._channel, name)
        self._refresh_display()
        self.preset_selected.emit(name, bands)

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_search(self):
        presets = _list_presets(self._channel)
        dlg = _PresetSearchDialog(presets, self)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.selected_name:
            self._load_and_emit(dlg.selected_name)

    def _on_star(self):
        if self._active and self._active not in self._favs and len(self._favs) < _MAX_FAV:
            self._favs.append(self._active)
            _save_favorites(self._channel, self._favs)
            self._refresh_display()

    def _on_more(self):
        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background: {BG_CARD};
                border: 1px solid {BORDER};
                color: {TEXT_PRIMARY};
            }}
            QMenu::item:selected {{ background: {ACCENT}; }}
        """)
        act_reset = menu.addAction("Reset to Flat")
        act_clear = menu.addAction("Clear favorites")
        action = menu.exec(self.mapToGlobal(self.sender().pos()))
        if action == act_reset:
            self._load_and_emit("Flat")
        elif action == act_clear:
            self._favs = []
            _save_favorites(self._channel, self._favs)
            self._refresh_display()

    def _on_fav_slot(self, idx: int):
        name = self._favs[idx] if idx < len(self._favs) else None
        if name:
            self._load_and_emit(name)


# ── Macro sliders ─────────────────────────────────────────────────────────────

class _MacroSliders(QWidget):
    macros_changed = Signal(float, float, float)   # basses, voix, aigus

    _LABELS = [("Basses", "basses"), ("Voix", "voix"), ("Aigus", "aigus")]

    def __init__(self, channel: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._channel = channel
        values = _load_macro(channel)

        self.setStyleSheet("background: transparent;")
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(24)

        self._sliders: dict[str, QSlider] = {}
        self._labels:  dict[str, QLabel]  = {}

        for label_text, key in self._LABELS:
            col = QVBoxLayout()
            col.setSpacing(4)

            title = QLabel(label_text)
            title.setAlignment(Qt.AlignmentFlag.AlignCenter)
            title.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 10pt;")
            col.addWidget(title)

            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setMinimum(-120)
            slider.setMaximum(120)
            slider.setValue(int(values.get(key, 0.0) * 10))
            slider.setFixedWidth(140)
            slider.setStyleSheet(f"""
                QSlider::groove:horizontal {{
                    height: 4px;
                    background: {BG_BUTTON};
                    border-radius: 2px;
                }}
                QSlider::handle:horizontal {{
                    background: {ACCENT};
                    width: 14px;
                    height: 14px;
                    margin: -5px 0;
                    border-radius: 7px;
                }}
                QSlider::sub-page:horizontal {{
                    background: {ACCENT};
                    border-radius: 2px;
                }}
            """)
            slider.valueChanged.connect(lambda v, k=key: self._on_change(k, v))
            col.addWidget(slider, alignment=Qt.AlignmentFlag.AlignCenter)

            val_label = QLabel(self._fmt(values.get(key, 0.0)))
            val_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            val_label.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: 10pt; font-weight: bold;")
            col.addWidget(val_label)

            self._sliders[key] = slider
            self._labels[key]  = val_label
            row.addLayout(col)

        row.addStretch(1)

    def _fmt(self, db: float) -> str:
        return f"{db:+.1f} dB" if db != 0 else "0 dB"

    def _on_change(self, key: str, raw: int):
        db = raw / 10.0
        self._labels[key].setText(self._fmt(db))
        values = {k: self._sliders[k].value() / 10.0 for k in ("basses", "voix", "aigus")}
        _save_macro(self._channel, values)
        self.macros_changed.emit(values["basses"], values["voix"], values["aigus"])

    def get_values(self) -> tuple[float, float, float]:
        return (
            self._sliders["basses"].value() / 10.0,
            self._sliders["voix"].value() / 10.0,
            self._sliders["aigus"].value() / 10.0,
        )


# ── Channel widget ────────────────────────────────────────────────────────────

class SonarChannelWidget(QWidget):
    def __init__(self, channel: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._channel = channel
        self._worker: _ApplyWorker | None = None
        self._pending_apply = False
        self._apply_timer = QTimer(self)
        self._apply_timer.setSingleShot(True)
        self._apply_timer.setInterval(_APPLY_DELAY)
        self._apply_timer.timeout.connect(self._do_apply)

        self.setStyleSheet(f"background-color: {BG_MAIN};")

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(scroll.Shape.NoFrame)
        scroll.setStyleSheet("background: transparent;")

        content = QWidget()
        content.setStyleSheet(f"background-color: {BG_MAIN};")
        root = QVBoxLayout(content)
        root.setContentsMargins(0, 0, 16, 0)
        root.setSpacing(16)

        # ── Preset bar card ───────────────────────────────────────────────────
        preset_card = self._card()
        pcl = QVBoxLayout(preset_card)
        pcl.setContentsMargins(20, 16, 20, 16)
        self._preset_bar = _PresetBar(channel, preset_card)
        self._preset_bar.preset_selected.connect(self._on_preset_selected)
        pcl.addWidget(self._preset_bar)
        root.addWidget(preset_card)

        # ── EQ curve card ─────────────────────────────────────────────────────
        eq_card = self._card()
        ecl = QVBoxLayout(eq_card)
        ecl.setContentsMargins(16, 14, 16, 14)
        ecl.setSpacing(12)

        eq_header = QHBoxLayout()
        eq_header.addWidget(QLabel("Equalizer"))
        eq_header.addStretch(1)
        self._status_lbl = QLabel()
        self._status_lbl.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 9pt;")
        eq_header.addWidget(self._status_lbl)
        ecl.addLayout(eq_header)

        self._eq_widget = EqCurveWidget(eq_card)
        self._eq_widget.setMinimumHeight(220)
        self._eq_widget.bands_changed.connect(self._on_bands_changed)
        ecl.addWidget(self._eq_widget)

        # Macro sliders
        macro_sep = QLabel("Macro")
        macro_sep.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 10pt; background: transparent;")
        ecl.addWidget(macro_sep)

        self._macros = _MacroSliders(channel, eq_card)
        self._macros.macros_changed.connect(self._on_macros_changed)
        ecl.addWidget(self._macros)

        root.addWidget(eq_card)
        root.addStretch(1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll.setWidget(content)
        outer.addWidget(scroll)

        # Load initial preset
        self._load_initial()

    def _card(self) -> QWidget:
        w = QWidget()
        w.setObjectName("sonarCard")
        w.setStyleSheet(f"""
            QWidget#sonarCard {{
                background-color: {BG_CARD};
                border: 1px solid {BORDER};
                border-radius: 12px;
            }}
        """)
        return w

    def _load_initial(self):
        name = _active_preset_name(self._channel)
        presets = _list_presets(self._channel)
        if name in presets:
            bands = _parse_preset(presets[name])
        else:
            bands = []
        self._cur_bands = bands
        self._eq_widget.set_bands(bands)
        self._update_macro_curve()

    # ── Signals ───────────────────────────────────────────────────────────────

    @Slot(str, list)
    def _on_preset_selected(self, name: str, bands: list):
        self._cur_bands = bands
        self._eq_widget.set_bands(bands)
        self._update_macro_curve()
        self._schedule_apply()

    def _on_bands_changed(self, bands: list):
        self._cur_bands = bands
        self._schedule_apply()

    def _on_macros_changed(self, basses: float, voix: float, aigus: float):
        self._update_macro_curve()
        self._schedule_apply()

    # ── Curve update ──────────────────────────────────────────────────────────

    def _update_macro_curve(self):
        b, v, a = self._macros.get_values()
        extra: list[EqBand] = []
        for key, db in (("basses", b), ("voix", v), ("aigus", a)):
            if abs(db) >= 0.01:
                p = MACRO_PARAMS[key]
                extra.append(EqBand(freq=p["freq"], gain=db, q=p["q"],
                                    type="peakingEQ", enabled=True))
        self._eq_widget.set_extra_bands(extra)

    # ── Apply ─────────────────────────────────────────────────────────────────

    def _schedule_apply(self):
        self._status_lbl.setText("pending…")
        self._apply_timer.start()

    def _do_apply(self):
        if self._worker and self._worker.isRunning():
            # Worker still running — remember to apply once it finishes
            self._pending_apply = True
            return
        self._pending_apply = False
        basses, voix, aigus = self._macros.get_values()
        self._worker = _ApplyWorker(
            self._channel, list(self._cur_bands), basses, voix, aigus
        )
        self._worker.done.connect(self._on_apply_done)
        self._worker.start()
        self._status_lbl.setText("applying…")

    @Slot(bool)
    def _on_apply_done(self, ok: bool):
        self._status_lbl.setText("✓ applied" if ok else "⚠ error")
        QTimer.singleShot(2000, lambda: self._status_lbl.setText(""))
        self._worker = None
        if self._pending_apply:
            self._pending_apply = False
            self._do_apply()


# ── Boost de Volume / Smart Volume — persistence ─────────────────────────────

_BOOST_FILE  = _CFG / "sonar_boost.json"
_SMART_FILE  = _CFG / "sonar_smart_volume.json"

_BOOST_DEFAULTS: dict  = {"enabled": False, "db": 0.0}
_SMART_DEFAULTS: dict  = {"enabled": False, "level": 0.0, "loudness": "balanced"}


def _load_boost() -> dict:
    if _BOOST_FILE.exists():
        try:
            return {**_BOOST_DEFAULTS, **json.loads(_BOOST_FILE.read_text())}
        except Exception:
            pass
    return dict(_BOOST_DEFAULTS)


def _save_boost(state: dict) -> None:
    _CFG.mkdir(parents=True, exist_ok=True)
    _BOOST_FILE.write_text(json.dumps(state, indent=2))


def _load_smart_volume() -> dict:
    if _SMART_FILE.exists():
        try:
            return {**_SMART_DEFAULTS, **json.loads(_SMART_FILE.read_text())}
        except Exception:
            pass
    return dict(_SMART_DEFAULTS)


def _save_smart_volume(state: dict) -> None:
    _CFG.mkdir(parents=True, exist_ok=True)
    _SMART_FILE.write_text(json.dumps(state, indent=2))


# ── Spatial Audio widget ─────────────────────────────────────────────────────

class SpatialAudioWidget(QWidget):
    """
    Global spatial audio controls (affects Game channel routing only).

    Toggle ON  → sonar-game-eq targets effect_input.virtual-surround-7.1-hesuvi
    Toggle OFF → sonar-game-eq targets physical output directly

    Mode / Immersion / Distance: saved state, pending USB captures for full impl.
    """
    state_changed = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._state = _load_spatial_audio()
        self._updating = False

        self.setObjectName("spatialCard")
        self.setStyleSheet(f"""
            QWidget#spatialCard {{
                background-color: {BG_CARD};
                border: 1px solid {BORDER};
                border-radius: 12px;
            }}
            QLabel {{ background: transparent; border: none; }}
            QSlider::groove:horizontal {{
                height: 4px; background: {BG_BUTTON}; border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                background: {ACCENT}; width: 14px; height: 14px;
                margin: -5px 0; border-radius: 7px;
            }}
            QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(14)

        # ── Row 1: toggle + title ─────────────────────────────────────────────
        row1 = QHBoxLayout()
        row1.setSpacing(12)

        self._toggle = QToggle(is_checkbox=True)
        self._toggle.setChecked(self._state["enabled"])
        self._toggle.checkStateChanged.connect(self._on_toggle)
        row1.addWidget(self._toggle)

        title = QLabel("Spatial Audio")
        title.setStyleSheet(f"font-size: 12pt; font-weight: bold; color: {TEXT_PRIMARY};")
        row1.addWidget(title)
        row1.addStretch(1)

        note = QLabel("Game channel")
        note.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 9pt;")
        row1.addWidget(note)
        root.addLayout(row1)

        # ── Collapsible section ───────────────────────────────────────────────
        self._detail = QWidget()
        self._detail.setStyleSheet("background: transparent;")
        detail_layout = QVBoxLayout(self._detail)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.setSpacing(12)

        # Mode selector: Casque / Haut-parleur
        mode_row = QHBoxLayout()
        mode_row.setSpacing(8)
        mode_lbl = QLabel("Mode")
        mode_lbl.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 10pt; min-width: 80px;")
        mode_row.addWidget(mode_lbl)

        self._mode_casque = self._pill_btn("Casque", "headphones")
        self._mode_speakers = self._pill_btn("Haut-parleur", "speakers")
        mode_row.addWidget(self._mode_casque)
        mode_row.addWidget(self._mode_speakers)
        mode_row.addStretch(1)
        detail_layout.addLayout(mode_row)

        self._refresh_mode_buttons()

        # Immersion slider
        detail_layout.addWidget(self._slider_row(
            "Performance / Immersion", "immersion",
            "(Linux : en attente de captures USB)"
        ))

        # Distance slider
        detail_layout.addWidget(self._slider_row(
            "Distance", "distance",
            "(Linux : en attente de captures USB)"
        ))

        root.addWidget(self._detail)
        self._detail.setVisible(self._state["enabled"])

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _pill_btn(self, label: str, value: str) -> QPushButton:
        btn = QPushButton(label)
        btn.setFixedHeight(28)
        btn.setProperty("mode_value", value)
        btn.clicked.connect(lambda: self._on_mode(value))
        return btn

    def _refresh_mode_buttons(self):
        active = self._state["mode"]
        for btn in (self._mode_casque, self._mode_speakers):
            selected = btn.property("mode_value") == active
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: {"" + ACCENT if selected else BG_BUTTON};
                    color: {"#fff" if selected else TEXT_SECONDARY};
                    border: 1px solid {ACCENT if selected else BORDER};
                    border-radius: 6px;
                    padding: 0 14px;
                    font-size: 10pt;
                }}
                QPushButton:hover {{ border-color: {ACCENT}; color: {TEXT_PRIMARY}; }}
            """)

    def _slider_row(self, label: str, key: str, pending_note: str) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        row = QHBoxLayout(w)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)

        lbl = QLabel(label)
        lbl.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 10pt; min-width: 180px;")
        row.addWidget(lbl)

        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setMinimum(0)
        slider.setMaximum(100)
        slider.setValue(self._state.get(key, 50))
        slider.setFixedWidth(160)
        slider.setToolTip(pending_note)
        slider.setEnabled(False)   # disabled until USB impl
        slider.setStyleSheet(f"""
            QSlider::groove:horizontal {{
                height: 4px; background: {BG_BUTTON}; border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                background: {TEXT_SECONDARY}; width: 14px; height: 14px;
                margin: -5px 0; border-radius: 7px;
            }}
        """)
        slider.valueChanged.connect(lambda v, k=key: self._on_slider(k, v))
        row.addWidget(slider)

        val_lbl = QLabel(str(self._state.get(key, 50)))
        val_lbl.setFixedWidth(28)
        val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        val_lbl.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 10pt;")
        row.addWidget(val_lbl)

        note = QLabel(pending_note)
        note.setStyleSheet(f"color: {BORDER}; font-size: 8pt;")
        row.addWidget(note)
        row.addStretch(1)

        # Store val_lbl ref on slider for update
        slider.setProperty("val_lbl_ptr", id(val_lbl))
        self.__dict__[f"_val_lbl_{key}"] = val_lbl

        return w

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_toggle(self, checked):
        enabled = bool(checked)
        self._state["enabled"] = enabled
        _save_spatial_audio(self._state)
        self._detail.setVisible(enabled)
        self.state_changed.emit()

    def _on_mode(self, value: str):
        self._state["mode"] = value
        _save_spatial_audio(self._state)
        self._refresh_mode_buttons()
        # Note: mode change doesn't affect routing yet (pending USB)

    def _on_slider(self, key: str, value: int):
        self._state[key] = value
        _save_spatial_audio(self._state)
        lbl = self.__dict__.get(f"_val_lbl_{key}")
        if lbl:
            lbl.setText(str(value))


# ── Boost de Volume widget ────────────────────────────────────────────────────

class BoostVolumeWidget(QWidget):
    """
    Adds a linear gain node at the end of every channel's filter chain.
    Fully functional via PipeWire builtin gain node.
    """
    state_changed = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._state = _load_boost()

        self.setObjectName("boostCard")
        self.setStyleSheet(f"""
            QWidget#boostCard {{
                background-color: {BG_CARD};
                border: 1px solid {BORDER};
                border-radius: 12px;
            }}
            QLabel {{ background: transparent; border: none; }}
            QSlider::groove:horizontal {{
                height: 4px; background: {BG_BUTTON}; border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                background: {ACCENT}; width: 14px; height: 14px;
                margin: -5px 0; border-radius: 7px;
            }}
            QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)

        # Toggle + title
        row1 = QHBoxLayout()
        row1.setSpacing(12)
        self._toggle = QToggle(is_checkbox=True)
        self._toggle.setChecked(self._state["enabled"])
        self._toggle.checkStateChanged.connect(self._on_toggle)
        row1.addWidget(self._toggle)
        title = QLabel("Boost de Volume")
        title.setStyleSheet(f"font-size: 12pt; font-weight: bold; color: {TEXT_PRIMARY};")
        row1.addWidget(title)
        row1.addStretch(1)
        self._db_label = QLabel(self._fmt(self._state["db"]))
        self._db_label.setStyleSheet(
            f"font-size: 11pt; font-weight: bold; color: {ACCENT}; min-width: 60px;"
        )
        self._db_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row1.addWidget(self._db_label)
        root.addLayout(row1)

        # Slider
        self._detail = QWidget()
        self._detail.setStyleSheet("background: transparent;")
        dl = QHBoxLayout(self._detail)
        dl.setContentsMargins(0, 0, 0, 0)
        dl.setSpacing(10)
        lbl_min = QLabel("0 dB")
        lbl_min.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 9pt;")
        dl.addWidget(lbl_min)
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setMinimum(0)
        self._slider.setMaximum(120)   # 0 → +12 dB  (steps of 0.1 dB)
        self._slider.setValue(int(self._state["db"] * 10))
        self._slider.valueChanged.connect(self._on_slider)
        dl.addWidget(self._slider, 1)
        lbl_max = QLabel("+12 dB")
        lbl_max.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 9pt;")
        dl.addWidget(lbl_max)
        root.addWidget(self._detail)

        self._detail.setVisible(self._state["enabled"])

    def _fmt(self, db: float) -> str:
        return f"+{db:.1f} dB" if db > 0 else "0 dB"

    def get_boost_db(self) -> float:
        return self._state["db"] if self._state["enabled"] else 0.0

    def _on_toggle(self, checked):
        self._state["enabled"] = bool(checked)
        _save_boost(self._state)
        self._detail.setVisible(self._state["enabled"])
        self.state_changed.emit()

    def _on_slider(self, value: int):
        db = value / 10.0
        self._state["db"] = db
        _save_boost(self._state)
        self._db_label.setText(self._fmt(db))
        self.state_changed.emit()


# ── Smart Volume widget ────────────────────────────────────────────────────────

class SmartVolumeWidget(QWidget):
    """
    Smart Volume — UI + saved state.
    Actual DSP implementation pending USB captures to confirm
    whether it's software-side (compressor) or a USB command to the DAC.
    """
    state_changed = Signal()

    _LOUDNESS_OPTIONS = [("Balanced", "balanced"), ("Cinema", "cinema"), ("Speech", "speech")]

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._state = _load_smart_volume()

        self.setObjectName("smartCard")
        self.setStyleSheet(f"""
            QWidget#smartCard {{
                background-color: {BG_CARD};
                border: 1px solid {BORDER};
                border-radius: 12px;
            }}
            QLabel {{ background: transparent; border: none; }}
            QSlider::groove:horizontal {{
                height: 4px; background: {BG_BUTTON}; border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                background: {TEXT_SECONDARY}; width: 14px; height: 14px;
                margin: -5px 0; border-radius: 7px;
            }}
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)

        # Toggle + title + pending note
        row1 = QHBoxLayout()
        row1.setSpacing(12)
        self._toggle = QToggle(is_checkbox=True)
        self._toggle.setChecked(self._state["enabled"])
        self._toggle.checkStateChanged.connect(self._on_toggle)
        row1.addWidget(self._toggle)
        title = QLabel("Smart Volume")
        title.setStyleSheet(f"font-size: 12pt; font-weight: bold; color: {TEXT_PRIMARY};")
        row1.addWidget(title)
        row1.addStretch(1)
        note = QLabel("en attente de captures USB")
        note.setStyleSheet(f"color: {BORDER}; font-size: 8pt;")
        row1.addWidget(note)
        root.addLayout(row1)

        # Detail section
        self._detail = QWidget()
        self._detail.setStyleSheet("background: transparent;")
        dl = QVBoxLayout(self._detail)
        dl.setContentsMargins(0, 0, 0, 0)
        dl.setSpacing(10)

        # Loudness mode
        loudness_row = QHBoxLayout()
        loudness_row.setSpacing(8)
        loudness_lbl = QLabel("Mode")
        loudness_lbl.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 10pt; min-width: 60px;")
        loudness_row.addWidget(loudness_lbl)
        self._loudness_btns: dict[str, QPushButton] = {}
        for label, value in self._LOUDNESS_OPTIONS:
            btn = QPushButton(label)
            btn.setFixedHeight(28)
            btn.setProperty("mode_value", value)
            btn.clicked.connect(lambda _, v=value: self._on_loudness(v))
            btn.setEnabled(False)
            self._loudness_btns[value] = btn
            loudness_row.addWidget(btn)
        loudness_row.addStretch(1)
        dl.addLayout(loudness_row)
        self._refresh_loudness()

        # Level slider (disabled, pending USB)
        level_row = QHBoxLayout()
        level_row.setSpacing(10)
        level_lbl = QLabel("Niveau")
        level_lbl.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 10pt; min-width: 60px;")
        level_row.addWidget(level_lbl)
        self._level_slider = QSlider(Qt.Orientation.Horizontal)
        self._level_slider.setMinimum(0)
        self._level_slider.setMaximum(100)
        self._level_slider.setValue(int(self._state.get("level", 0.0)))
        self._level_slider.setEnabled(False)
        self._level_slider.setToolTip("Pending USB captures")
        self._level_slider.valueChanged.connect(self._on_level)
        level_row.addWidget(self._level_slider, 1)
        self._level_val = QLabel(str(int(self._state.get("level", 0))))
        self._level_val.setFixedWidth(28)
        self._level_val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._level_val.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 10pt;")
        level_row.addWidget(self._level_val)
        dl.addLayout(level_row)

        root.addWidget(self._detail)
        self._detail.setVisible(self._state["enabled"])

    def _refresh_loudness(self):
        active = self._state.get("loudness", "balanced")
        for value, btn in self._loudness_btns.items():
            selected = value == active
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: {ACCENT if selected else BG_BUTTON};
                    color: {"#fff" if selected else TEXT_SECONDARY};
                    border: 1px solid {ACCENT if selected else BORDER};
                    border-radius: 6px; padding: 0 12px; font-size: 10pt;
                }}
            """)

    def _on_toggle(self, checked):
        self._state["enabled"] = bool(checked)
        _save_smart_volume(self._state)
        self._detail.setVisible(self._state["enabled"])
        self.state_changed.emit()

    def _on_loudness(self, value: str):
        self._state["loudness"] = value
        _save_smart_volume(self._state)
        self._refresh_loudness()

    def _on_level(self, value: int):
        self._state["level"] = float(value)
        _save_smart_volume(self._state)
        self._level_val.setText(str(value))


# ── Sonar page (top-level) ────────────────────────────────────────────────────

class SonarPage(QWidget):
    def __init__(self, embedded: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        self.setStyleSheet(f"background-color: {BG_MAIN};")

        root = QVBoxLayout(self)
        root.setContentsMargins(0 if embedded else 36, 0 if embedded else 28, 0 if embedded else 36, 0 if embedded else 28)
        root.setSpacing(0)

        if not embedded:
            title = QLabel("Arctis Sound Manager")
            title.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: 28pt; font-weight: bold; background: transparent;")
            root.addWidget(title)
            root.addSpacing(4)

            subtitle = QLabel("Sonar")
            subtitle.setStyleSheet("color: #666666; font-size: 20pt; font-weight: bold; background: transparent;")
            root.addWidget(subtitle)
            root.addSpacing(20)

        # ── Channel tabs ──────────────────────────────────────────────────────
        from PySide6.QtWidgets import QTabWidget
        self._tabs = QTabWidget()
        self._tabs.setStyleSheet(f"""
            QTabWidget::pane {{
                border: none;
                background: transparent;
            }}
            QTabBar::tab {{
                background: {BG_BUTTON};
                color: {TEXT_SECONDARY};
                border: 1px solid {BORDER};
                border-bottom: none;
                border-radius: 6px 6px 0 0;
                padding: 7px 22px;
                margin-right: 3px;
                font-size: 11pt;
            }}
            QTabBar::tab:selected {{
                background: {BG_CARD};
                color: {TEXT_PRIMARY};
                border-color: {BORDER};
            }}
            QTabBar::tab:hover {{
                background: {BG_BUTTON_HOVER};
                color: {TEXT_PRIMARY};
            }}
        """)

        self._game_widget  = SonarChannelWidget("game")
        self._chat_widget  = SonarChannelWidget("chat")
        self._micro_widget = SonarChannelWidget("micro")

        self._tabs.addTab(self._game_widget,  "Game")
        self._tabs.addTab(self._chat_widget,  "Chat")
        self._tabs.addTab(self._micro_widget, "Micro")

        root.addWidget(self._tabs, 1)

        # ── Global section — Spatial Audio ────────────────────────────────────
        root.addSpacing(12)
        self._spatial = SpatialAudioWidget()
        self._spatial.state_changed.connect(self._on_spatial_changed)
        root.addWidget(self._spatial)

        root.addSpacing(8)
        self._boost = BoostVolumeWidget()
        self._boost.state_changed.connect(self._on_boost_changed)
        root.addWidget(self._boost)

        root.addSpacing(8)
        self._smart = SmartVolumeWidget()
        root.addWidget(self._smart)

    def _on_spatial_changed(self):
        """Spatial audio toggle changed — re-apply game channel conf."""
        self._game_widget._schedule_apply()

    def _on_boost_changed(self):
        """Boost changed — re-apply all three channels."""
        self._game_widget._schedule_apply()
        self._chat_widget._schedule_apply()
        self._micro_widget._schedule_apply()
