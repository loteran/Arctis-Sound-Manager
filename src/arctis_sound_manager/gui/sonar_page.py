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
import math
import subprocess
from pathlib import Path

from arctis_sound_manager.i18n import I18n

from PySide6.QtCore import QThread, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from arctis_sound_manager.gui.eq_curve_widget import EqBand, EqCurveWidget

_IMAGES_DIR = Path(__file__).parent / "images"

def _t(key: str) -> str:
    return I18n.translate("ui", key)


def _svg_icon(svg_name: str, color: str, size: int = 20) -> QIcon:
    """Load an SVG from images dir, replace stroke color, return QIcon."""
    svg_data = (_IMAGES_DIR / svg_name).read_text(encoding="utf-8")
    svg_data = svg_data.replace('stroke="currentColor"', f'stroke="{color}"')
    pixmap = QPixmap(size, size)
    pixmap.fill(QColor(0, 0, 0, 0))
    renderer = QSvgRenderer(svg_data.encode("utf-8"))
    if renderer.isValid():
        painter = QPainter(pixmap)
        renderer.render(painter)
        painter.end()
    return QIcon(pixmap)


class _NoWheelSlider(QSlider):
    """QSlider that ignores wheel events so the parent QScrollArea can scroll."""
    def wheelEvent(self, event):
        event.ignore()
from arctis_sound_manager.gui.qt_widgets.q_toggle import QToggle
from arctis_sound_manager.gui.theme import (
    ACCENT,
    BG_BUTTON,
    BG_BUTTON_HOVER,
    BG_CARD,
    BG_MAIN,
    BORDER,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)
from arctis_sound_manager.sonar_to_pipewire import (
    _MACRO_PARAMS as MACRO_PARAMS,
    check_and_fix_stale_configs,
    generate_sonar_eq_conf,
    generate_sonar_micro_conf,
    generate_virtual_sinks_conf,
)

# ── Paths ─────────────────────────────────────────────────────────────────────

_CFG          = Path.home() / ".config" / "arctis_manager"
_PRESETS_DIR  = _CFG / "sonar_presets"
_RAW_DIR      = Path(__file__).parent / "presets"

_CHANNEL_TAG  = {"game": "[Game]", "chat": "[Chat]", "micro": "[Mic]", "output": "[Game]"}
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

    # Minimum interval between filter-chain restarts (seconds).
    # Rapid restarts can lock up the USB ALSA driver.
    _MIN_RESTART_INTERVAL = 2.0
    _last_restart: float = 0.0

    def __init__(self, channel: str, bands: list[EqBand],
                 basses: float, voix: float, aigus: float,
                 target_override: str | None = None):
        super().__init__()
        self._channel = channel
        self._bands   = bands
        self._basses  = basses
        self._voix    = voix
        self._aigus   = aigus
        self._target_override = target_override

    # ── helpers ────────────────────────────────────────────────────────────
    @staticmethod
    def _wait_for_node(node_name: str, timeout_ms: int = 5000) -> bool:
        """Poll pw-cli until *node_name* appears (or timeout)."""
        import time
        deadline = time.monotonic() + timeout_ms / 1000.0
        while time.monotonic() < deadline:
            try:
                r = subprocess.run(
                    ["pw-cli", "list-objects", "Node"],
                    capture_output=True, text=True, timeout=2,
                )
                if node_name in r.stdout:
                    return True
            except Exception:
                pass
            time.sleep(0.15)
        return False

    # ── stream snapshot helpers ──────────────────────────────────────────

    @staticmethod
    def _snapshot_sink_inputs(log) -> dict[str, list[str]]:
        """Return sink-input IDs grouped by their current sink name.

        Returns {sink_name: [sink_input_id, ...]} so streams can be
        restored to the same Arctis channel after filter-chain restart.
        """
        result: dict[str, list[str]] = {}
        try:
            r = subprocess.run(
                ["pactl", "list", "sink-inputs", "short"],
                capture_output=True, text=True, timeout=3,
            )
            # Build sink index→name map
            sr = subprocess.run(
                ["pactl", "list", "sinks", "short"],
                capture_output=True, text=True, timeout=3,
            )
            idx_to_name = {}
            for line in sr.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 2:
                    idx_to_name[parts[0]] = parts[1]

            for line in r.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 2:
                    si_id, sink_idx = parts[0], parts[1]
                    sink_name = idx_to_name.get(sink_idx, "")
                    result.setdefault(sink_name, []).append(si_id)
        except Exception as e:
            log.warning("Could not snapshot sink-inputs: %s", e)
        return result

    @staticmethod
    def _snapshot_source_outputs(log) -> list[str]:
        """Return list of source-output IDs before restart."""
        try:
            r = subprocess.run(
                ["pactl", "list", "source-outputs", "short"],
                capture_output=True, text=True, timeout=3,
            )
            return [line.split()[0] for line in r.stdout.splitlines() if line.strip()]
        except Exception as e:
            log.warning("Could not snapshot source-outputs: %s", e)
            return []

    @staticmethod
    def _find_pactl_index(name: str, kind: str, log) -> str | None:
        """Find the pactl numeric index of a sink or source by node name."""
        cmd = "sinks" if kind == "sink" else "sources"
        try:
            r = subprocess.run(
                ["pactl", "list", cmd, "short"],
                capture_output=True, text=True, timeout=3,
            )
            for line in r.stdout.splitlines():
                if name in line:
                    return line.split()[0]
        except Exception as e:
            log.warning("Could not list %s: %s", cmd, e)
        return None

    @staticmethod
    def _move_streams_with_retry(
        stream_ids: list[str], target_idx: str,
        kind: str, log, retries: int = 3,
    ) -> None:
        """Move sink-inputs or source-outputs to target, with retry."""
        import time
        cmd = "move-sink-input" if kind == "sink" else "move-source-output"
        for sid in stream_ids:
            for attempt in range(retries):
                r = subprocess.run(
                    ["pactl", cmd, sid, target_idx],
                    capture_output=True, text=True, check=False, timeout=3,
                )
                if r.returncode == 0:
                    break
                if attempt < retries - 1:
                    time.sleep(0.3 * (attempt + 1))
                else:
                    log.warning("Failed to %s %s → %s after %d attempts: %s",
                                cmd, sid, target_idx, retries, r.stderr.strip())

    # ── main ───────────────────────────────────────────────────────────────
    def run(self):
        import json as _json
        import logging
        log = logging.getLogger(__name__)
        try:
            boost_state = _load_boost()
            boost_db = boost_state["db"] if boost_state["enabled"] else 0.0
            smart_state = _load_smart_volume()
            # Snapshot old Game EQ channel count before overwriting config,
            # so we can detect spatial audio toggle (2ch↔8ch) later.
            _old_game_ch = None
            if self._channel == "game":
                import re as _re
                _eq_path = Path.home() / ".config" / "pipewire" / "filter-chain.conf.d" / "sonar-game-eq.conf"
                try:
                    _m = _re.search(r"audio\.channels\s*=\s*(\d+)", _eq_path.read_text())
                    if _m:
                        _old_game_ch = int(_m.group(1))
                except Exception:
                    pass

            if self._channel == "micro":
                micro_proc = _load_micro_proc()
                generate_sonar_micro_conf(self._bands, self._basses, self._voix, self._aigus,
                                          boost_db=boost_db,
                                          noise_canceling=micro_proc.get("noiseCanceling"),
                                          noise_reduction=micro_proc)
            else:
                spatial = _load_spatial_audio()["enabled"] if self._channel == "game" else True
                generate_sonar_eq_conf(self._channel, self._bands,
                                       self._basses, self._voix, self._aigus,
                                       spatial_audio=spatial, boost_db=boost_db,
                                       smart_volume=smart_state,
                                       target_override=self._target_override)

            # ── Regenerate HeSuVi config with current Spatial Audio parameters ──
            if self._channel == "game":
                spatial_state = _load_spatial_audio()
                if spatial_state["enabled"]:
                    from arctis_sound_manager.sonar_to_pipewire import generate_hesuvi_conf
                    generate_hesuvi_conf(
                        immersion_pct=spatial_state.get("immersion", 50),
                        distance_pct=spatial_state.get("distance", 50),
                    )

            # Snapshot active streams BEFORE restart so we can restore them
            saved_sink_inputs = self._snapshot_sink_inputs(log)
            saved_source_outputs = self._snapshot_source_outputs(log)

            # Throttle restarts to avoid locking up the USB ALSA driver
            import time
            elapsed = time.monotonic() - _ApplyWorker._last_restart
            if elapsed < _ApplyWorker._MIN_RESTART_INTERVAL:
                wait = _ApplyWorker._MIN_RESTART_INTERVAL - elapsed
                log.debug("Throttling filter-chain restart (%.1fs)", wait)
                self.msleep(int(wait * 1000))

            # Check whether the Game EQ channel count changed (spatial toggle).
            # A full pipewire restart is needed so the loopback reconnects
            # to the new sink with the correct channel count.
            _ApplyWorker._last_restart = time.monotonic()
            need_full_restart = False
            if self._channel == "game" and _old_game_ch is not None:
                new_ch = 8 if spatial else 2
                need_full_restart = _old_game_ch != new_ch

            # Restart audio services.
            # For the "output" channel we only restart filter-chain so
            # active streams on pipewire/wireplumber are not killed.
            from arctis_sound_manager.init_system import detect_init
            if self._channel == "output":
                if detect_init() == "dinit":
                    result = subprocess.run(
                        ["dinitctl", "start", "pipewire-filter-chain"],
                        capture_output=True, text=True, timeout=15,
                    )
                else:
                    result = subprocess.run(
                        ["systemctl", "--user", "restart", "filter-chain"],
                        capture_output=True, text=True, timeout=15,
                    )
            else:
                if need_full_restart:
                    generate_virtual_sinks_conf(sonar=True)
                if detect_init() == "dinit":
                    for svc in ["pipewire", "wireplumber", "pipewire-pulse"]:
                        subprocess.run(["dinitctl", "restart", svc], check=False)
                    result = subprocess.run(
                        ["dinitctl", "start", "pipewire-filter-chain"],
                        capture_output=True, text=True, timeout=15,
                    )
                else:
                    result = subprocess.run(
                        ["systemctl", "--user", "restart",
                         "pipewire", "wireplumber", "pipewire-pulse", "filter-chain"],
                        capture_output=True, text=True, timeout=15,
                    )
            if result.returncode != 0:
                log.error("audio restart failed (rc=%d): %s",
                          result.returncode, result.stderr.strip())
                self.done.emit(False)
                return

            # Wait for the sink/source to actually appear in PipeWire
            if self._channel == "micro":
                target_node = "effect_output.sonar-micro-eq"
            else:
                target_node = f"effect_input.sonar-{self._channel}-eq"

            if not self._wait_for_node(target_node, timeout_ms=8000):
                log.warning("Sonar node %s did not appear within timeout", target_node)
                self.done.emit(False)
                return

            # Filter-chain restart also tears down the Arctis_* virtual sinks
            # (Game/Chat/Media). On the Output channel only filter-chain is
            # restarted, but those sinks still flap — wait for any saved
            # target sinks to come back before attempting move-sink-input,
            # otherwise streams stay orphaned on the system default (issue #22).
            _restore_remap = {
                "effect_input.sonar-game-eq": "Arctis_Game",
                "effect_input.sonar-chat-eq": "Arctis_Chat",
            }
            for sink_name in saved_sink_inputs.keys():
                target = _restore_remap.get(sink_name, sink_name)
                if target.startswith("Arctis_"):
                    self._wait_for_node(target, timeout_ms=4000)

            # After pipewire restart, stream IDs are invalid;
            # asm-router re-applies overrides automatically.
            # Only the micro default source needs explicit restore.
            if self._channel == "micro":
                source = "effect_output.sonar-micro-eq"
                source_json = _json.dumps({"name": source})
                subprocess.run(
                    ["pw-metadata", "0", "default.configured.audio.source", source_json],
                    check=False, timeout=5,
                )
                subprocess.run(
                    ["pw-metadata", "0", "default.audio.source", source_json],
                    check=False, timeout=5,
                )
                source_idx = self._find_pactl_index(source, "source", log)
                if source_idx and saved_source_outputs:
                    self._move_streams_with_retry(
                        saved_source_outputs, source_idx, "source", log,
                    )
                elif not source_idx:
                    log.warning("Sonar micro source not found in pactl, "
                                "cannot restore mic streams")
            else:
                # Game/Chat: restore each stream to its original Arctis sink.
                # Remap effect_input sinks to their Arctis equivalents.
                _effect_remap = {
                    "effect_input.sonar-game-eq": "Arctis_Game",
                    "effect_input.sonar-chat-eq": "Arctis_Chat",
                }
                if self._channel == "output":
                    _effect_remap["effect_input.sonar-output-eq"] = "effect_input.sonar-output-eq"
                for sink_name, si_ids in saved_sink_inputs.items():
                    target = _effect_remap.get(sink_name, sink_name)
                    target_idx = self._find_pactl_index(target, "sink", log)
                    if target_idx:
                        self._move_streams_with_retry(
                            si_ids, target_idx, "sink", log,
                        )
                    else:
                        log.warning("Sink %s not found, cannot restore streams", target)

            self.done.emit(True)
        except subprocess.TimeoutExpired:
            log.error("_ApplyWorker timeout (channel=%s)", self._channel)
            self.done.emit(False)
        except Exception as e:
            log.error("_ApplyWorker error (channel=%s): %s", self._channel, e)
            self.done.emit(False)


class _ApplyAllWorker(QThread):
    """Apply all 3 EQ channels (game/chat/micro) in a single filter-chain restart."""
    done = Signal(bool)

    def __init__(self):
        super().__init__()

    def run(self):
        import logging
        log = logging.getLogger(__name__)
        try:
            boost_state = _load_boost()
            boost_db = boost_state["db"] if boost_state["enabled"] else 0.0
            smart_state = _load_smart_volume()
            spatial = _load_spatial_audio()

            # Generate conf for each channel
            for channel in ("game", "chat"):
                spatial_on = spatial["enabled"] if channel == "game" else True
                try:
                    bands = _parse_preset(
                        _list_presets(channel).get(_active_preset_name(channel),
                        next(iter(_list_presets(channel).values())))
                    )
                except Exception:
                    bands = []
                macro = _load_macro(channel)
                generate_sonar_eq_conf(
                    channel, bands,
                    macro.get("basses", 0.0),
                    macro.get("voix", 0.0),
                    macro.get("aigus", 0.0),
                    spatial_audio=spatial_on,
                    boost_db=boost_db,
                    smart_volume=smart_state,
                )

            # Micro channel
            try:
                micro_bands = _parse_preset(
                    _list_presets("micro").get(_active_preset_name("micro"),
                    next(iter(_list_presets("micro").values())))
                )
            except Exception:
                micro_bands = []
            micro_macro = _load_macro("micro")
            micro_proc = _load_micro_proc()
            generate_sonar_micro_conf(
                micro_bands,
                micro_macro.get("basses", 0.0),
                micro_macro.get("voix", 0.0),
                micro_macro.get("aigus", 0.0),
                boost_db=boost_db,
                noise_canceling=micro_proc.get("noiseCanceling"),
                noise_reduction=micro_proc,
            )

            # Regenerate HeSuVi if spatial on
            if spatial["enabled"]:
                from arctis_sound_manager.sonar_to_pipewire import generate_hesuvi_conf
                generate_hesuvi_conf(
                    immersion_pct=spatial.get("immersion", 50),
                    distance_pct=spatial.get("distance", 50),
                )

            # Single restart
            from arctis_sound_manager.init_system import detect_init
            if detect_init() == "dinit":
                for svc in ["pipewire", "wireplumber", "pipewire-pulse"]:
                    subprocess.run(["dinitctl", "restart", svc], check=False)
                result = subprocess.run(
                    ["dinitctl", "start", "pipewire-filter-chain"],
                    capture_output=True, text=True, timeout=15,
                )
            else:
                result = subprocess.run(
                    ["systemctl", "--user", "restart",
                     "pipewire", "wireplumber", "pipewire-pulse", "filter-chain"],
                    capture_output=True, text=True, timeout=15,
                )
            if result.returncode != 0:
                log.error("audio restart failed: %s", result.stderr.strip())
                self.done.emit(False)
                return

            if not _ApplyWorker._wait_for_node("effect_input.sonar-game-eq", timeout_ms=8000):
                log.warning("sonar-game-eq did not appear")

            self.done.emit(True)
        except Exception as exc:
            logging.getLogger(__name__).error("_ApplyAllWorker failed: %s", exc)
            self.done.emit(False)


# ── Preset search dialog ──────────────────────────────────────────────────────

class _PresetSearchDialog(QDialog):
    def __init__(self, presets: dict[str, Path], parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(_t("search_preset"))
        self.setMinimumSize(340, 480)
        self.selected_name: str | None = None
        self._all = list(presets.keys())

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        self._search = QLineEdit()
        self._search.setPlaceholderText(_t("search_dots"))
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
    remove_requested = Signal()

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

    def contextMenuEvent(self, event):
        if not self._name:
            return
        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background: {BG_CARD};
                border: 1px solid {BORDER};
                color: {TEXT_PRIMARY};
            }}
            QMenu::item:selected {{ background: {ACCENT}; }}
        """)
        act_remove = menu.addAction(f"Remove \"{self._name}\"")
        if menu.exec(event.globalPos()) == act_remove:
            self.remove_requested.emit()

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
            self.setToolTip(_t("empty_slot"))
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

        star_btn = QPushButton()
        star_btn.setFixedSize(32, 32)
        star_btn.setToolTip(_t("add_to_favorites"))
        star_btn.setIcon(_svg_icon("star_icon.svg", TEXT_PRIMARY))
        star_btn.setIconSize(star_btn.size() * 0.55)
        star_btn.setStyleSheet(self._icon_btn_ss())
        star_btn.clicked.connect(self._on_star)
        row1.addWidget(star_btn)

        reset_btn = QPushButton()
        reset_btn.setFixedSize(32, 32)
        reset_btn.setToolTip(_t("reset_to_flat"))
        reset_btn.setIcon(_svg_icon("reset_icon.svg", TEXT_PRIMARY))
        reset_btn.setIconSize(reset_btn.size() * 0.55)
        reset_btn.setStyleSheet(self._icon_btn_ss())
        reset_btn.clicked.connect(lambda: self._load_and_emit("Flat"))
        row1.addWidget(reset_btn)

        root.addLayout(row1)

        # ── Row 2: search + favorites label ─────────────────────────────────
        row2 = QHBoxLayout()
        row2.setSpacing(8)

        search_btn = QPushButton(f"🔍  {_t('search_preset')}")
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
            slot.remove_requested.connect(lambda idx=i: self._on_fav_remove(idx))
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
        self._fav_count.setText(f"{_t('favorites')} ({len(self._favs)}/{_MAX_FAV})")
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


    def _on_fav_remove(self, idx: int):
        if idx < len(self._favs):
            self._favs.pop(idx)
            _save_favorites(self._channel, self._favs)
            self._refresh_display()

    def _on_fav_slot(self, idx: int):
        name = self._favs[idx] if idx < len(self._favs) else None
        if name:
            self._load_and_emit(name)


# ── Macro sliders ─────────────────────────────────────────────────────────────

class _MacroSliders(QWidget):
    macros_changed = Signal(float, float, float)   # basses, voix, aigus

    _KEYS = [("bass", "basses"), ("voice", "voix"), ("treble", "aigus")]

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

        for i18n_key, key in self._KEYS:
            label_text = _t(i18n_key)
            col = QVBoxLayout()
            col.setSpacing(4)

            title = QLabel(label_text)
            title.setAlignment(Qt.AlignmentFlag.AlignCenter)
            title.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 10pt;")
            col.addWidget(title)

            slider = _NoWheelSlider(Qt.Orientation.Horizontal)
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
        self._target_override: str | None = None
        self._worker: _ApplyWorker | None = None
        self._pending_apply = False
        self._apply_timer = QTimer(self)
        self._apply_timer.setSingleShot(True)
        self._apply_timer.setInterval(_APPLY_DELAY)
        self._apply_timer.timeout.connect(self._do_apply)

        self.setStyleSheet(f"background-color: {BG_MAIN};")

        root = QVBoxLayout(self)
        self._root_layout = root   # exposed for subclasses
        root.setContentsMargins(0, 0, 0, 0)
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
        eq_header.addWidget(QLabel(_t("equalizer")))
        eq_header.addStretch(1)
        self._status_lbl = QLabel()
        self._status_lbl.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 9pt;")
        eq_header.addWidget(self._status_lbl)
        ecl.addLayout(eq_header)

        self._eq_widget = EqCurveWidget(eq_card)
        self._eq_widget.setMinimumHeight(200)
        self._eq_widget.setMaximumHeight(260)
        self._eq_widget.bands_changed.connect(self._on_bands_changed)
        ecl.addWidget(self._eq_widget)

        # Macro sliders
        macro_sep = QLabel(_t("macro"))
        macro_sep.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 10pt; background: transparent;")
        ecl.addWidget(macro_sep)

        self._macros = _MacroSliders(channel, eq_card)
        self._macros.macros_changed.connect(self._on_macros_changed)
        ecl.addWidget(self._macros)

        root.addWidget(eq_card)

        # ── Settings card (game / chat / output) ─────────────────────────────
        if channel == "output":
            settings_card = QWidget()
            settings_card.setObjectName("settingsCard")
            settings_card.setStyleSheet(f"""
                QWidget#settingsCard {{
                    background-color: {BG_CARD};
                    border: 1px solid {BORDER};
                    border-radius: 12px;
                }}
            """)
            scl = QVBoxLayout(settings_card)
            scl.setContentsMargins(0, 0, 0, 0)
            scl.addStretch(1)
            root.addWidget(settings_card)
        elif channel in ("game", "chat"):
            settings_card = QWidget()
            settings_card.setObjectName("settingsCard")
            settings_card.setStyleSheet(f"""
                QWidget#settingsCard {{
                    background-color: {BG_CARD};
                    border: 1px solid {BORDER};
                    border-radius: 12px;
                }}
            """)
            scl = QVBoxLayout(settings_card)
            scl.setContentsMargins(0, 0, 0, 0)
            scl.setSpacing(0)

            if channel == "game":
                from arctis_sound_manager import device_state as _ds
                _has_spatial = _ds.get_spatial_engine() != "none"
                self._spatial = SpatialAudioWidget()
                self._spatial.setVisible(_has_spatial)
                scl.addWidget(self._spatial)
                sep = QFrame()
                sep.setFrameShape(QFrame.Shape.HLine)
                sep.setStyleSheet(f"background: {BORDER}; border: none; max-height: 1px;")
                sep.setVisible(_has_spatial)
                scl.addWidget(sep)

            self._boost = BoostVolumeWidget()
            scl.addWidget(self._boost)

            sep2 = QFrame()
            sep2.setFrameShape(QFrame.Shape.HLine)
            sep2.setStyleSheet(f"background: {BORDER}; border: none; max-height: 1px;")
            scl.addWidget(sep2)

            self._smart = SmartVolumeWidget()
            scl.addWidget(self._smart)

            if channel in ("chat", "output"):
                scl.addStretch(1)

            root.addWidget(settings_card)

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
        self._status_lbl.setText(_t("pending"))
        self._apply_timer.start()

    def _do_apply(self):
        if self._worker and self._worker.isRunning():
            # Worker still running — remember to apply once it finishes
            self._pending_apply = True
            return
        self._pending_apply = False
        basses, voix, aigus = self._macros.get_values()
        self._worker = _ApplyWorker(
            self._channel, list(self._cur_bands), basses, voix, aigus,
            target_override=self._target_override,
        )
        self._worker.done.connect(self._on_apply_done)
        self._worker.start()
        self._status_lbl.setText(_t("applying"))

    @Slot(bool)
    def _on_apply_done(self, ok: bool):
        self._status_lbl.setText(_t("applied") if ok else _t("error"))
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

        self.setStyleSheet(f"""
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

        title = QLabel(_t("spatial_audio"))
        title.setStyleSheet(f"font-size: 12pt; font-weight: bold; color: {TEXT_PRIMARY};")
        row1.addWidget(title)
        row1.addStretch(1)

        note = QLabel(_t("game_channel"))
        note.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 9pt;")
        row1.addWidget(note)
        root.addLayout(row1)

        # ── Collapsible section ───────────────────────────────────────────────
        self._detail = QWidget()
        self._detail.setStyleSheet("background: transparent;")
        detail_layout = QVBoxLayout(self._detail)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.setSpacing(12)

        # Immersion slider
        detail_layout.addWidget(self._slider_row("Performance / Immersion", "immersion"))

        # Distance slider
        detail_layout.addWidget(self._slider_row("Distance", "distance"))

        root.addWidget(self._detail)
        self._detail.setVisible(self._state["enabled"])

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _slider_row(self, label: str, key: str) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        row = QHBoxLayout(w)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)

        lbl = QLabel(label)
        lbl.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 10pt; min-width: 180px;")
        row.addWidget(lbl)

        slider = _NoWheelSlider(Qt.Orientation.Horizontal)
        slider.setMinimum(0)
        slider.setMaximum(100)
        slider.setValue(self._state.get(key, 50))
        slider.setFixedWidth(160)
        slider.valueChanged.connect(lambda v, k=key: self._on_slider(k, v))
        row.addWidget(slider)

        val_lbl = QLabel(str(self._state.get(key, 50)))
        val_lbl.setFixedWidth(28)
        val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        val_lbl.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 10pt;")
        row.addWidget(val_lbl)

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

    def _on_slider(self, key: str, value: int):
        self._state[key] = value
        _save_spatial_audio(self._state)
        lbl = self.__dict__.get(f"_val_lbl_{key}")
        if lbl:
            lbl.setText(str(value))
        self.state_changed.emit()


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

        self.setStyleSheet(f"""
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
        title = QLabel(_t("volume_boost"))
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
        self._slider = _NoWheelSlider(Qt.Orientation.Horizontal)
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
        self._state["enabled"] = checked == Qt.CheckState.Checked
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
    Smart Volume — dynamic compressor (LADSPA SC4M) controlled via PipeWire filter-chain.
    """
    state_changed = Signal()

    _LOUDNESS_KEYS = ["quiet", "balanced", "loud"]

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._state = _load_smart_volume()

        self.setStyleSheet(f"""
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
        title = QLabel(_t("smart_volume"))
        title.setStyleSheet(f"font-size: 12pt; font-weight: bold; color: {TEXT_PRIMARY};")
        row1.addWidget(title)
        row1.addStretch(1)
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
        loudness_lbl = QLabel(_t("mode"))
        loudness_lbl.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 10pt; min-width: 60px;")
        loudness_row.addWidget(loudness_lbl)
        self._loudness_btns: dict[str, QPushButton] = {}
        for value in self._LOUDNESS_KEYS:
            label = _t(value)
            btn = QPushButton(label)
            btn.setFixedHeight(28)
            btn.setProperty("mode_value", value)
            btn.clicked.connect(lambda _, v=value: self._on_loudness(v))
            self._loudness_btns[value] = btn
            loudness_row.addWidget(btn)
        loudness_row.addStretch(1)
        dl.addLayout(loudness_row)
        self._refresh_loudness()

        # Level slider
        level_row = QHBoxLayout()
        level_row.setSpacing(10)
        level_lbl = QLabel(_t("level"))
        level_lbl.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 10pt; min-width: 60px;")
        level_row.addWidget(level_lbl)
        self._level_slider = _NoWheelSlider(Qt.Orientation.Horizontal)
        self._level_slider.setMinimum(0)
        self._level_slider.setMaximum(100)
        self._level_slider.setValue(int(self._state.get("level", 50)))
        self._level_slider.valueChanged.connect(self._on_level)
        level_row.addWidget(self._level_slider, 1)
        self._level_val = QLabel(str(int(self._state.get("level", 50))))
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
        self._state["enabled"] = checked == Qt.CheckState.Checked
        _save_smart_volume(self._state)
        self._detail.setVisible(self._state["enabled"])
        self.state_changed.emit()

    def _on_loudness(self, value: str):
        self._state["loudness"] = value
        _save_smart_volume(self._state)
        self._refresh_loudness()
        self.state_changed.emit()

    def _on_level(self, value: int):
        self._state["level"] = float(value)
        _save_smart_volume(self._state)
        self._level_val.setText(str(value))
        self.state_changed.emit()


# ── Micro processing persistence ──────────────────────────────────────────────

_MICRO_PROC_FILE = _CFG / "sonar_micro_processing.json"
_MICRO_PROC_DEFAULTS: dict = {
    "noiseCanceling":   {"enabled": False, "value": 0.9},
    "bgReduction":      {"enabled": False, "value": 0.0},
    "impactReduction":  {"enabled": False, "value": 0.0},
    "noiseGate":        {"enabled": False, "value": -60.0, "auto": False},
    "compressor":       {"enabled": False, "value": 0.0},
}


def _load_micro_proc() -> dict:
    if _MICRO_PROC_FILE.exists():
        try:
            saved = json.loads(_MICRO_PROC_FILE.read_text())
            result = {k: (dict(v) if isinstance(v, dict) else v)
                      for k, v in _MICRO_PROC_DEFAULTS.items()}
            for k, v in saved.items():
                if k in result and isinstance(result[k], dict) and isinstance(v, dict):
                    result[k] = {**result[k], **v}
                else:
                    result[k] = v
            return result
        except Exception:
            pass
    return {k: (dict(v) if isinstance(v, dict) else v)
            for k, v in _MICRO_PROC_DEFAULTS.items()}


def _save_micro_proc(state: dict) -> None:
    _CFG.mkdir(parents=True, exist_ok=True)
    _MICRO_PROC_FILE.write_text(json.dumps(state, indent=2))


# ── Waveform animation ────────────────────────────────────────────────────────

class _WaveformWidget(QWidget):
    """Animated two-phase waveform: noisy input (red) + processed output (grey)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(54)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def showEvent(self, event):
        super().showEvent(event)
        self._timer.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._timer.stop()

    def _tick(self):
        self._phase = (self._phase + 0.12) % (2 * math.pi * 50)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        mid = h / 2.0
        p.fillRect(self.rect(), QColor(BG_CARD))
        n = 260

        # Noisy input (red) — left 55 %
        path = QPainterPath()
        for i in range(n):
            t = i / (n - 1)
            x = t * w * 0.55
            amp = 14.0 * (0.6 + 0.4 * math.sin(t * math.pi))
            y = (mid + amp * (math.sin(self._phase + i * 0.19)
                               + 0.4 * math.sin(self._phase * 2.1 + i * 0.33)
                               + 0.15 * math.sin(self._phase * 3.7 + i * 0.57)))
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        p.setPen(QPen(QColor("#C04040"), 1.5))
        p.drawPath(path)

        # Processed output (grey) — right 55 %
        path2 = QPainterPath()
        for i in range(n):
            t = i / (n - 1)
            x = w * 0.45 + t * w * 0.55
            amp = 5.0 * math.sin(self._phase * 0.6 + i * 0.09)
            y = mid + amp
            if i == 0:
                path2.moveTo(x, y)
            else:
                path2.lineTo(x, y)
        p.setPen(QPen(QColor("#5A7080"), 1.5))
        p.drawPath(path2)
        p.end()


# ── Shared card helpers ───────────────────────────────────────────────────────

def _micro_card_style() -> str:
    return f"""
        QLabel {{ background: transparent; border: none; }}
        QSlider::groove:horizontal {{
            height: 4px; background: {BG_BUTTON}; border-radius: 2px;
        }}
        QSlider::handle:horizontal {{
            background: {ACCENT}; width: 14px; height: 14px;
            margin: -5px 0; border-radius: 7px;
        }}
        QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
        QCheckBox {{ color: {TEXT_SECONDARY}; font-size: 9pt; background: transparent; }}
        QCheckBox::indicator {{
            width: 14px; height: 14px;
            border: 1px solid {BORDER}; border-radius: 3px; background: {BG_BUTTON};
        }}
        QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
    """


def _make_header_row(title: str, toggle) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setSpacing(10)
    row.addWidget(toggle)
    lbl = QLabel(title)
    lbl.setStyleSheet(f"font-size: 10pt; font-weight: bold; color: {TEXT_PRIMARY};")
    row.addWidget(lbl)
    row.addStretch(1)
    return row


# ── ClearCast AI Noise Cancellation card ──────────────────────────────────────

class _NoiseCancelingCard(QWidget):
    state_changed = Signal()

    def __init__(self, state: dict, parent=None):
        super().__init__(parent)
        self._state = state
        self.setObjectName("microCard")
        self.setStyleSheet(_micro_card_style())

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 14, 20, 14)
        root.setSpacing(8)

        self._toggle = QToggle(is_checkbox=True)
        self._toggle.setChecked(state["noiseCanceling"]["enabled"])
        self._toggle.checkStateChanged.connect(self._on_toggle)
        root.addLayout(_make_header_row("CLEARCAST AI NOISE CANCELLATION", self._toggle))

        self._waveform = _WaveformWidget(self)
        root.addWidget(self._waveform)

        slider_row = QHBoxLayout()
        slider_row.setSpacing(10)
        lbl_min = QLabel(_t("min"))
        lbl_min.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 9pt;")
        slider_row.addWidget(lbl_min)
        self._slider = _NoWheelSlider(Qt.Orientation.Horizontal)
        self._slider.setMinimum(0)
        self._slider.setMaximum(100)
        self._slider.setValue(int(state["noiseCanceling"]["value"] * 100))
        self._slider.valueChanged.connect(self._on_slider)
        slider_row.addWidget(self._slider, 1)
        lbl_max = QLabel(_t("max"))
        lbl_max.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 9pt;")
        slider_row.addWidget(lbl_max)
        root.addLayout(slider_row)

        self._set_enabled(state["noiseCanceling"]["enabled"])

    def _set_enabled(self, enabled: bool):
        self._slider.setEnabled(enabled)
        self._waveform.setVisible(enabled)

    def _on_toggle(self, checked):
        self._state["noiseCanceling"]["enabled"] = bool(checked)
        self._set_enabled(bool(checked))
        self.state_changed.emit()

    def _on_slider(self, value: int):
        self._state["noiseCanceling"]["value"] = value / 100.0
        self.state_changed.emit()


# ── Noise Reduction card ──────────────────────────────────────────────────────

class _NoiseReductionCard(QWidget):
    state_changed = Signal()

    def __init__(self, state: dict, parent=None):
        super().__init__(parent)
        self._state = state
        self.setObjectName("microCard")
        self.setStyleSheet(_micro_card_style())

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        title_row = QHBoxLayout()
        title = QLabel(_t("noise_reduction").upper())
        title.setStyleSheet(f"font-size: 10pt; font-weight: bold; color: {TEXT_PRIMARY};")
        title_row.addWidget(title)
        title_row.addStretch(1)
        root.addLayout(title_row)

        root.addLayout(self._slider_row("Background", "bgReduction"))
        root.addLayout(self._slider_row("Impact",     "impactReduction"))
        root.addStretch(1)

    def _slider_row(self, label: str, key: str) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        toggle = QToggle(is_checkbox=True)
        toggle.setChecked(self._state[key]["enabled"])
        toggle.checkStateChanged.connect(lambda c, k=key: self._on_toggle(k, bool(c)))
        row.addWidget(toggle)
        lbl = QLabel(label)
        lbl.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 9pt; min-width: 68px;")
        row.addWidget(lbl)
        slider = _NoWheelSlider(Qt.Orientation.Horizontal)
        slider.setMinimum(0)
        slider.setMaximum(100)
        slider.setValue(int(self._state[key]["value"] * 100))
        slider.valueChanged.connect(lambda v, k=key: self._on_slider(k, v))
        row.addWidget(slider, 1)
        val_lbl = QLabel(f"{self._state[key]['value']:.2f}")
        val_lbl.setFixedWidth(34)
        val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        val_lbl.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: 9pt;")
        row.addWidget(val_lbl)
        setattr(self, f"_val_{key}", val_lbl)
        return row

    def _on_toggle(self, key: str, enabled: bool):
        self._state[key]["enabled"] = enabled
        self.state_changed.emit()

    def _on_slider(self, key: str, value: int):
        v = value / 100.0
        self._state[key]["value"] = v
        lbl = getattr(self, f"_val_{key}", None)
        if lbl:
            lbl.setText(f"{v:.2f}")
        self.state_changed.emit()


# ── Noise Gate card ───────────────────────────────────────────────────────────

class _NoiseGateCard(QWidget):
    state_changed = Signal()

    def __init__(self, state: dict, parent=None):
        super().__init__(parent)
        self._state = state
        self.setObjectName("microCard")
        self.setStyleSheet(_micro_card_style())

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        self._toggle = QToggle(is_checkbox=True)
        self._toggle.setChecked(state["noiseGate"]["enabled"])
        self._toggle.checkStateChanged.connect(self._on_toggle)
        root.addLayout(_make_header_row(_t("noise_gate").upper(), self._toggle))

        # Threshold slider
        seuil_row = QHBoxLayout()
        seuil_row.setSpacing(8)
        seuil_lbl = QLabel(_t("threshold"))
        seuil_lbl.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 9pt; min-width: 36px;")
        seuil_row.addWidget(seuil_lbl)
        self._seuil = _NoWheelSlider(Qt.Orientation.Horizontal)
        self._seuil.setMinimum(-600)    # -60.0 dB × 10
        self._seuil.setMaximum(-100)    # -10.0 dB × 10
        self._seuil.setValue(int(state["noiseGate"]["value"] * 10))
        self._seuil.valueChanged.connect(self._on_seuil)
        seuil_row.addWidget(self._seuil, 1)
        self._seuil_val = QLabel(f'{state["noiseGate"]["value"]:.1f} dB')
        self._seuil_val.setFixedWidth(60)
        self._seuil_val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._seuil_val.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: 9pt;")
        seuil_row.addWidget(self._seuil_val)
        root.addLayout(seuil_row)

        # Auto checkbox
        self._auto_cb = QCheckBox("Calcule automatiquement le seuil de l'effet noise gate")
        self._auto_cb.setChecked(state["noiseGate"]["auto"])
        self._auto_cb.toggled.connect(self._on_auto)
        root.addWidget(self._auto_cb)

        root.addStretch(1)
        self._set_enabled(state["noiseGate"]["enabled"])

    def _set_enabled(self, enabled: bool):
        self._seuil.setEnabled(enabled and not self._state["noiseGate"]["auto"])
        self._auto_cb.setEnabled(enabled)

    def _on_toggle(self, checked):
        self._state["noiseGate"]["enabled"] = bool(checked)
        self._set_enabled(bool(checked))
        self.state_changed.emit()

    def _on_seuil(self, value: int):
        db = value / 10.0
        self._state["noiseGate"]["value"] = db
        self._seuil_val.setText(f"{db:.1f} dB")
        self.state_changed.emit()

    def _on_auto(self, checked: bool):
        self._state["noiseGate"]["auto"] = checked
        self._seuil.setEnabled(self._state["noiseGate"]["enabled"] and not checked)
        self.state_changed.emit()


# ── Compressor / Volume Stabilizer card ───────────────────────────────────────

class _CompressorCard(QWidget):
    state_changed = Signal()

    def __init__(self, state: dict, parent=None):
        super().__init__(parent)
        self._state = state
        self.setObjectName("microCard")
        self.setStyleSheet(_micro_card_style())

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 14, 20, 14)
        root.setSpacing(10)

        self._toggle = QToggle(is_checkbox=True)
        self._toggle.setChecked(state["compressor"]["enabled"])
        self._toggle.checkStateChanged.connect(self._on_toggle)
        root.addLayout(_make_header_row(_t("compressor").upper(), self._toggle))

        self._detail = QWidget()
        self._detail.setStyleSheet("background: transparent;")
        dl = QHBoxLayout(self._detail)
        dl.setContentsMargins(0, 0, 0, 0)
        dl.setSpacing(10)
        niv_lbl = QLabel(_t("level"))
        niv_lbl.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 9pt; min-width: 52px;")
        dl.addWidget(niv_lbl)
        self._slider = _NoWheelSlider(Qt.Orientation.Horizontal)
        self._slider.setMinimum(0)
        self._slider.setMaximum(100)
        self._slider.setValue(int(state["compressor"]["value"] * 100))
        self._slider.valueChanged.connect(self._on_slider)
        dl.addWidget(self._slider, 1)
        self._val_lbl = QLabel(f'{state["compressor"]["value"]:.2f}')
        self._val_lbl.setFixedWidth(36)
        self._val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._val_lbl.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: 9pt;")
        dl.addWidget(self._val_lbl)
        root.addWidget(self._detail)

        self._set_enabled(state["compressor"]["enabled"])

    def _set_enabled(self, enabled: bool):
        self._detail.setVisible(enabled)

    def _on_toggle(self, checked):
        self._state["compressor"]["enabled"] = bool(checked)
        self._set_enabled(bool(checked))
        self.state_changed.emit()

    def _on_slider(self, value: int):
        v = value / 100.0
        self._state["compressor"]["value"] = v
        self._val_lbl.setText(f"{v:.2f}")
        self.state_changed.emit()


# ── Sonar Micro widget ────────────────────────────────────────────────────────

class SonarMicroWidget(SonarChannelWidget):
    """Micro tab: EQ preset + ClearCast + Noise Reduction + Noise Gate + Compressor."""

    def __init__(self, parent=None):
        super().__init__("micro", parent)
        self._micro_state = _load_micro_proc()
        root = self._root_layout

        # Réduire l'écart entre l'EQ et les cartes de traitement
        root.setSpacing(8)

        # ── Micro processing card ────────────────────────────────────────────
        micro_settings = QWidget()
        micro_settings.setObjectName("microSettingsCard")
        micro_settings.setStyleSheet(f"""
            QWidget#microSettingsCard {{
                background-color: {BG_CARD};
                border: 1px solid {BORDER};
                border-radius: 12px;
            }}
        """)
        msl = QVBoxLayout(micro_settings)
        msl.setContentsMargins(0, 0, 0, 0)
        msl.setSpacing(0)

        self._nc_card = _NoiseCancelingCard(self._micro_state)
        self._nc_card.state_changed.connect(self._on_micro_changed)
        msl.addWidget(self._nc_card)

        sep1 = QFrame()
        sep1.setFrameShape(QFrame.Shape.HLine)
        sep1.setStyleSheet(f"background: {BORDER}; border: none; max-height: 1px;")
        msl.addWidget(sep1)

        # Noise Reduction + Noise Gate side by side
        side = QWidget()
        side.setStyleSheet("background: transparent;")
        side_hl = QHBoxLayout(side)
        side_hl.setContentsMargins(20, 12, 20, 12)
        side_hl.setSpacing(8)
        self._nr_card = _NoiseReductionCard(self._micro_state)
        self._ng_card = _NoiseGateCard(self._micro_state)
        self._nr_card.state_changed.connect(self._on_micro_changed)
        self._ng_card.state_changed.connect(self._on_micro_changed)
        side_hl.addWidget(self._nr_card, 6)
        side_hl.addWidget(self._ng_card, 4)
        msl.addWidget(side)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"background: {BORDER}; border: none; max-height: 1px;")
        msl.addWidget(sep2)

        self._comp_card = _CompressorCard(self._micro_state)
        self._comp_card.state_changed.connect(self._on_micro_changed)
        msl.addWidget(self._comp_card)

        root.addWidget(micro_settings)
        root.addStretch(1)

    def _on_micro_changed(self):
        _save_micro_proc(self._micro_state)
        # Re-generate filter-chain conf (EQ part — DSP for noise/gate pending plugins)
        self._schedule_apply()


# ── Sonar page (top-level) ────────────────────────────────────────────────────

class SonarPage(QWidget):
    def __init__(self, embedded: bool = False, parent: QWidget | None = None):
        super().__init__(parent)

        # Fix stale configs (broken builtins, wrong locations, duplicate HeSuVi node, …)
        fixed, needs_pw_restart = check_and_fix_stale_configs()
        if fixed:
            import logging
            if needs_pw_restart:
                # One-time migration: static HeSuVi was in pipewire.conf.d and created a
                # duplicate node that silenced the Game channel.  A full PipeWire restart
                # is required to unload the old node; subsequent runs won't need this.
                logging.getLogger(__name__).info(
                    "Stale static HeSuVi config removed — restarting PipeWire to clear duplicate node"
                )
                from arctis_sound_manager.init_system import detect_init
                if detect_init() == "dinit":
                    for svc in ["pipewire", "wireplumber", "pipewire-pulse"]:
                        subprocess.run(["dinitctl", "restart", svc], check=False)
                    subprocess.run(["dinitctl", "start", "pipewire-filter-chain"],
                                   check=False, timeout=20)
                else:
                    subprocess.run(
                        ["systemctl", "--user", "restart",
                         "pipewire", "wireplumber", "pipewire-pulse", "filter-chain"],
                        check=False, timeout=20,
                    )
            else:
                logging.getLogger(__name__).info("Stale Sonar configs fixed, restarting filter-chain")
                from arctis_sound_manager.init_system import detect_init
                if detect_init() == "dinit":
                    subprocess.run(["dinitctl", "start", "pipewire-filter-chain"],
                                   check=False, timeout=15)
                else:
                    subprocess.run(["systemctl", "--user", "restart", "filter-chain"],
                                   check=False, timeout=15)

        self.setStyleSheet(f"background-color: {BG_MAIN};")

        root = QVBoxLayout(self)
        root.setContentsMargins(0 if embedded else 36, 0 if embedded else 28, 0 if embedded else 36, 0 if embedded else 28)
        root.setSpacing(0)

        if not embedded:
            title = QLabel(_t("app_name"))
            title.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: 28pt; font-weight: bold; background: transparent;")
            root.addWidget(title)
            root.addSpacing(4)

            subtitle = QLabel(_t("sonar"))
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

        self._game_widget   = SonarChannelWidget("game")
        self._chat_widget   = SonarChannelWidget("chat")
        self._micro_widget  = SonarMicroWidget()
        self._output_widget = SonarChannelWidget("output")

        self._tabs.addTab(self._game_widget,   _t("game"))
        self._tabs.addTab(self._chat_widget,   _t("chat"))
        self._tabs.addTab(self._micro_widget,  _t("micro"))
        self._tabs.addTab(self._output_widget, _t("output"))

        root.addWidget(self._tabs, 1)

        # Load external output target from settings
        self._load_output_target()

        # ── Connect settings signals from tab widgets ────────────────────────
        self._game_widget._spatial.state_changed.connect(self._on_spatial_changed)
        self._game_widget._boost.state_changed.connect(self._on_boost_changed)
        self._game_widget._smart.state_changed.connect(self._on_smart_changed)
        self._chat_widget._boost.state_changed.connect(self._on_boost_changed)
        self._chat_widget._smart.state_changed.connect(self._on_smart_changed)

    def _load_output_target(self):
        """Read external_output_device from settings and set it on the output widget.

        Falls back to auto-detecting the first non-SteelSeries ALSA output sink.
        """
        import pulsectl
        from ruamel.yaml import YAML

        nick: str | None = None
        settings_file = Path.home() / ".config" / "arctis_manager" / "settings" / "general_settings.yaml"
        if settings_file.exists():
            try:
                raw = YAML(typ='safe').load(settings_file) or {}
                nick = raw.get("external_output_device")
            except Exception:
                pass

        try:
            with pulsectl.Pulse("asm-output-lookup") as p:
                for s in p.sink_list():
                    if nick:
                        if s.proplist.get("node.nick", "") == nick:
                            self._output_widget._target_override = s.name
                            return
                    else:
                        # Auto-detect: first alsa_output not from SteelSeries
                        if s.name.startswith("alsa_output") \
                                and s.proplist.get("device.vendor.id", "") != "0x1038":
                            self._output_widget._target_override = s.name
                            return
        except Exception:
            pass

    def _on_spatial_changed(self):
        """Spatial audio toggle changed — re-apply game channel conf."""
        self._game_widget._schedule_apply()

    def _on_boost_changed(self):
        """Boost changed — re-apply all three channels."""
        self._game_widget._schedule_apply()
        self._chat_widget._schedule_apply()
        self._micro_widget._schedule_apply()

    def _on_smart_changed(self):
        """Smart Volume changed — re-apply game and chat channels."""
        self._game_widget._schedule_apply()
        self._chat_widget._schedule_apply()

    def apply_all_from_files(self) -> None:
        """Re-apply all 3 EQ channels from current config files (used by profile system)."""
        worker = _ApplyAllWorker()
        worker.done.connect(lambda ok: None)  # fire-and-forget
        worker.start()
        self._apply_all_worker = worker  # prevent GC
