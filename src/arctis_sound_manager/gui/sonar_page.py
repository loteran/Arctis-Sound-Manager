# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

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

from arctis_sound_manager import service_control as sc
from arctis_sound_manager.i18n import I18n

from PySide6.QtCore import QThread, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
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

from arctis_sound_manager.eq_types import EqBand
from arctis_sound_manager.gui.eq_curve_widget import EqCurveWidget

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
from arctis_sound_manager.system_deps_checker import _find_ladspa_plugin
import arctis_sound_manager.gui.theme as _theme
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
)

# ── Paths ─────────────────────────────────────────────────────────────────────

_CFG          = Path.home() / ".config" / "arctis_manager"
_PRESETS_DIR  = _CFG / "sonar_presets"
_RAW_DIR      = Path(__file__).parent / "presets"

_CHANNEL_TAG  = {"game": "[Game]", "media": "[Game]", "chat": "[Chat]", "micro": "[Mic]", "output": "[Game]"}
_FAV_ROW_SIZE = 9   # slots per row before wrapping to a new row
_APPLY_DELAY  = 600   # ms debounce before restarting filter-chain

# ── Preset I/O ────────────────────────────────────────────────────────────────

def _parse_preset_data(data: dict) -> list[EqBand]:
    payload = data.get("data", data)  # GG 113+ wraps settings in a "data" key
    eq = payload.get("parametricEQ", {})
    indexed = sorted(
        ((int(k[6:]), v) for k, v in eq.items()
         if k.startswith("filter") and k[6:].isdigit()),
        key=lambda x: x[0],
    )
    bands: list[EqBand] = []
    for _, f in indexed:
        bands.append(EqBand(
            freq=float(f.get("frequency", 1000)),
            gain=float(f.get("gain", 0)),
            q=float(f.get("qFactor", 0.707)),
            type=f.get("type", "peakingEQ"),
            enabled=bool(f.get("enabled", True)),
        ))
    return bands


def _parse_preset(path: Path) -> list[EqBand]:
    return _parse_preset_data(json.loads(path.read_text()))


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


def _spatial_file_for_channel(channel: str = "game") -> Path:
    suffix = "" if channel == "game" else f"_{channel}"
    return Path.home() / ".config" / "arctis_manager" / f"sonar_spatial_audio{suffix}.json"


def _load_spatial_audio(channel: str = "game") -> dict:
    f = _spatial_file_for_channel(channel)
    if f.exists():
        try:
            d = json.loads(f.read_text())
            return {**_SPATIAL_DEFAULTS, **d}
        except Exception:
            pass
    return dict(_SPATIAL_DEFAULTS)


def _save_spatial_audio(state: dict, channel: str = "game") -> None:
    _CFG.mkdir(parents=True, exist_ok=True)
    _spatial_file_for_channel(channel).write_text(json.dumps(state, indent=2))


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


def _save_custom_preset(channel: str, name: str, bands: list,
                        macros: dict | None = None,
                        settings: dict | None = None) -> Path:
    """Save current EQ bands (and optionally macro values) as a custom preset JSON."""
    tag = _CHANNEL_TAG.get(channel, "[Game]")
    _PRESETS_DIR.mkdir(parents=True, exist_ok=True)
    preset_data: dict = {
        "parametricEQ": {
            f"filter{i+1}": {
                "enabled": b.enabled,
                "qFactor": round(b.q, 4),
                "frequency": round(b.freq, 2),
                "gain": round(b.gain, 4),
                "type": b.type,
            }
            for i, b in enumerate(bands)
        }
    }
    if macros:
        preset_data["macros"] = {k: round(float(v), 2) for k, v in macros.items()}
    if settings:
        preset_data["settings"] = settings
    path = _PRESETS_DIR / f"{name} {tag}.json"
    path.write_text(json.dumps(preset_data, indent=2, ensure_ascii=False))
    return path


def _read_preset_macros(path: Path) -> dict[str, float] | None:
    """Return the macros dict from a preset JSON, or None if absent."""
    try:
        return json.loads(path.read_text()).get("macros") or None
    except Exception:
        return None


def _is_custom_preset(channel: str, name: str) -> bool:
    tag = _CHANNEL_TAG.get(channel, "[Game]")
    return (_PRESETS_DIR / f"{name} {tag}.json").exists()


def _delete_custom_preset(channel: str, name: str) -> bool:
    tag = _CHANNEL_TAG.get(channel, "[Game]")
    path = _PRESETS_DIR / f"{name} {tag}.json"
    if path.exists():
        path.unlink()
        return True
    return False


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
            try:
                boost_state = _load_boost()
                boost_db = boost_state.get("db", 0.0) if boost_state.get("enabled") else 0.0
            except Exception as e:
                log.warning("boost load failed: %s — using default", e)
                boost_db = 0.0
            try:
                smart_state = _load_smart_volume()
            except Exception as e:
                log.warning("smart_volume load failed: %s — using default", e)
                smart_state = {"enabled": False, "level": 0.0, "loudness": "balanced"}

            # ── Guard: read existing conf before regenerating (1c) ───────────
            _conf_dir = Path.home() / ".config" / "pipewire" / "filter-chain.conf.d"
            _eq_conf_path = _conf_dir / (
                "sonar-micro-eq.conf" if self._channel == "micro"
                else f"sonar-{self._channel}-eq.conf"
            )
            _old_eq_conf = _eq_conf_path.read_text() if _eq_conf_path.exists() else None

            if self._channel == "micro":
                micro_proc = _load_micro_proc()
                _new_eq_conf = generate_sonar_micro_conf(
                    self._bands, self._basses, self._voix, self._aigus,
                    boost_db=boost_db,
                    noise_canceling=micro_proc.get("noiseCanceling"),
                    noise_reduction=micro_proc,
                )
            elif self._channel == "output" and _load_output_passthrough():
                # Output passthrough: send audio to the external sink as-is, no
                # EQ. Empty bands + zero macros/boost → generate_sonar_eq_conf
                # emits a plain copy at the sink's native channel count (2.0–7.1).
                _new_eq_conf = generate_sonar_eq_conf(
                    "output", [], 0.0, 0.0, 0.0,
                    boost_db=0.0,
                    smart_volume=None,
                    target_override=self._target_override,
                )
            else:
                if self._channel == "game":
                    spatial = _load_spatial_audio("game")["enabled"]
                elif self._channel == "media":
                    spatial = _load_spatial_audio("media")["enabled"]
                else:
                    spatial = True
                _new_eq_conf = generate_sonar_eq_conf(
                    self._channel, self._bands,
                    self._basses, self._voix, self._aigus,
                    spatial_audio=spatial if self._channel == "game" else True,
                    media_spatial_audio=spatial if self._channel == "media" else True,
                    boost_db=boost_db,
                    smart_volume=smart_state,
                    target_override=self._target_override,
                )

            # ── Regenerate HeSuVi config with current Spatial Audio parameters ──
            _hesuvi_unchanged = True
            if self._channel in ("game", "media"):
                spatial_state = _load_spatial_audio(self._channel)
                if spatial_state["enabled"]:
                    from arctis_sound_manager.sonar_to_pipewire import generate_hesuvi_conf
                    _hesuvi_path = _conf_dir / "sink-virtual-surround-7.1-hesuvi.conf"
                    _old_hesuvi = _hesuvi_path.read_text() if _hesuvi_path.exists() else None
                    _new_hesuvi = generate_hesuvi_conf(
                        immersion_pct=spatial_state.get("immersion", 50),
                        distance_pct=spatial_state.get("distance", 50),
                    )
                    # generate_hesuvi_conf returns "" when no device is attached
                    # (skips write) — treat as unchanged in that case.
                    if _new_hesuvi:
                        _hesuvi_unchanged = (
                            _old_hesuvi is not None and _new_hesuvi == _old_hesuvi
                        )

            # ── Guard: skip filter-chain restart if nothing changed on disk ──
            if _old_eq_conf is not None and _new_eq_conf == _old_eq_conf and _hesuvi_unchanged:
                log.debug(
                    "_ApplyWorker: conf unchanged for channel=%s — skipping restart",
                    self._channel,
                )
                self.done.emit(True)
                return

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

            # Check whether the EQ channel count changed (spatial toggle).
            # A full pipewire restart is needed so the loopback reconnects
            # to the new sink with the correct channel count.
            _ApplyWorker._last_restart = time.monotonic()

            # Restart audio services.
            #
            # We never restart the pipewire / pipewire-pulse daemons for a
            # profile/EQ change: a full restart tears down every node (including
            # the physical output) and breaks apps that don't re-enumerate when
            # the PulseAudio server connection drops — Discord (Electron) loses
            # its sink and stays silent until restarted.
            #
            # Instead: restart filter-chain only (recreates the
            # effect_input.sonar-*-eq nodes with the new curve), wait for the
            # node, then ask the daemon to recreate the Arctis_* loopbacks (fresh
            # pw-loopback processes). A freshly-created loopback re-negotiates its
            # width (2ch chat / 8ch game+media) and relinks to the EQ node by
            # node.target reliably — which WirePlumber does NOT do for a
            # pre-existing loopback when its target node is swapped underneath it.
            # pipewire / pipewire-pulse stay up, so Discord keeps its sink+audio.
            # (need_full_restart / spatial 2ch↔8ch is handled the same way: the
            # recreated loopback simply negotiates the new width.)
            if self._channel == "micro":
                target_node = "effect_output.sonar-micro-eq"
            else:
                target_node = f"effect_input.sonar-{self._channel}-eq"

            ok = sc.restart("filter-chain", timeout=15)
            if not ok:
                log.error("audio restart failed")
                self.done.emit(False)
                return

            # Wait for the recreated EQ node before recreating the loopbacks.
            if not self._wait_for_node(target_node, timeout_ms=8000):
                log.warning("Sonar node %s did not appear within timeout", target_node)
                self.done.emit(False)
                return

            # Recreate only the loopback for the edited channel.  Chat
            # (always 2ch) auto-reconnects to its EQ node without being
            # recreated, which keeps Arctis_Chat alive in Discord's device
            # list across filter-chain restarts.  Micro and Output have no
            # Arctis_* loopback of their own.
            if self._channel in ("game", "media"):
                from arctis_sound_manager.gui.dbus_wrapper import DbusWrapper
                DbusWrapper.recreate_loopback_single_sync(self._channel)

            # Wait for any saved Arctis_* target sinks to come back before
            # attempting move-sink-input (issue #22).
            _restore_remap = {
                "effect_input.sonar-game-eq":  "Arctis_Game",
                "effect_input.sonar-chat-eq":  "Arctis_Chat",
                "effect_input.sonar-media-eq": "Arctis_Media",
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
                # Remap streams that were on effect_input nodes back to their
                # Arctis_* virtual sink (the correct user-facing destination).
                _effect_remap = {
                    "effect_input.sonar-game-eq":  "Arctis_Game",
                    "effect_input.sonar-chat-eq":  "Arctis_Chat",
                    "effect_input.sonar-media-eq": "Arctis_Media",
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

            # Re-apply saved routing overrides (e.g. Discord -> Arctis_Chat).
            # Streams that were torn down during the restart — instead of merely
            # moved — are not covered by the snapshot/restore above, and apps
            # like Discord (Electron) do not re-enumerate their sink on their
            # own. This waits for the virtual sinks to reappear, then moves each
            # app's live sink-input back onto its intended channel.
            if self._channel != "micro":
                from arctis_sound_manager.pw_utils import reapply_routing_overrides
                reapply_routing_overrides()

            self.done.emit(True)
        except subprocess.TimeoutExpired:
            log.error("_ApplyWorker timeout (channel=%s)", self._channel)
            self.done.emit(False)
        except Exception as e:
            log.error("_ApplyWorker error (channel=%s): %s", self._channel, e)
            self.done.emit(False)


class _ApplyAllWorker(QThread):
    """Apply all EQ channels (game/media/chat/micro) in a single filter-chain restart."""
    done = Signal(bool)

    def __init__(self):
        super().__init__()

    def run(self):
        import logging
        log = logging.getLogger(__name__)
        try:
            try:
                boost_state = _load_boost()
                boost_db = boost_state.get("db", 0.0) if boost_state.get("enabled") else 0.0
            except Exception as e:
                log.warning("boost load failed: %s — using default", e)
                boost_db = 0.0
            try:
                smart_state = _load_smart_volume()
            except Exception as e:
                log.warning("smart_volume load failed: %s — using default", e)
                smart_state = {"enabled": False, "level": 0.0, "loudness": "balanced"}
            game_spatial = _load_spatial_audio("game")
            media_spatial = _load_spatial_audio("media")

            # Generate conf for each channel. "output" is included so its EQ
            # profile is (re)applied on every global apply and its config never
            # goes stale/missing (it also adapts 2.0–7.1 to the external sink).
            for channel in ("game", "media", "chat", "output"):
                game_sp_on = game_spatial["enabled"]
                media_sp_on = media_spatial["enabled"]
                # Output passthrough: bypass the EQ profile (empty bands/macros).
                if channel == "output" and _load_output_passthrough():
                    generate_sonar_eq_conf("output", [], 0.0, 0.0, 0.0,
                                           boost_db=0.0, smart_volume=None)
                    continue
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
                    spatial_audio=game_sp_on if channel == "game" else True,
                    media_spatial_audio=media_sp_on if channel == "media" else True,
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

            # Regenerate HeSuVi: game spatial takes precedence; fall back to media
            if game_spatial["enabled"]:
                from arctis_sound_manager.sonar_to_pipewire import generate_hesuvi_conf
                generate_hesuvi_conf(
                    immersion_pct=game_spatial.get("immersion", 50),
                    distance_pct=game_spatial.get("distance", 50),
                )
            elif media_spatial["enabled"]:
                from arctis_sound_manager.sonar_to_pipewire import generate_hesuvi_conf
                generate_hesuvi_conf(
                    immersion_pct=media_spatial.get("immersion", 50),
                    distance_pct=media_spatial.get("distance", 50),
                )

            # Profile-switch path (apply_all_from_files). Same Discord-safe
            # strategy as _ApplyWorker: restart filter-chain only (recreates all
            # effect_input.sonar-*-eq nodes), wait for them, then have the daemon
            # recreate the Arctis_* loopbacks fresh so they relink correctly.
            # Never restart pipewire / pipewire-pulse → connected apps (Discord)
            # keep their sink.
            ok = sc.restart("filter-chain", timeout=15)
            if not ok:
                log.error("audio restart failed")
                self.done.emit(False)
                return

            for node in ("effect_input.sonar-game-eq",
                         "effect_input.sonar-media-eq",
                         "effect_input.sonar-chat-eq"):
                if not _ApplyWorker._wait_for_node(node, timeout_ms=8000):
                    log.warning("%s did not appear", node)

            from arctis_sound_manager.gui.dbus_wrapper import DbusWrapper
            DbusWrapper.recreate_loopbacks_game_media_sync()

            # Profile switches recreate the loopbacks but (unlike _ApplyWorker)
            # do not snapshot/restore sink-inputs, so apps that fell off their
            # channel during the restart — Discord especially — must be pulled
            # back onto their override target explicitly.
            from arctis_sound_manager.pw_utils import reapply_routing_overrides
            reapply_routing_overrides()

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
        self._presets = presets
        self._custom_names: set[str] = {
            n for n, p in presets.items() if p.parent == _PRESETS_DIR
        }
        # Custom presets first, then built-in (alphabetical within each group)
        self._all = sorted(self._custom_names) + [
            n for n in presets.keys() if n not in self._custom_names
        ]

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
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._on_context_menu)
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
                item = QListWidgetItem(name)
                if name in self._custom_names:
                    item.setForeground(QColor(ACCENT))
                self._list.addItem(item)

    def _on_context_menu(self, pos) -> None:
        item = self._list.itemAt(pos)
        if not item:
            return
        name = item.text()
        if name not in self._custom_names:
            return
        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background: {BG_CARD}; border: 1px solid {BORDER}; color: {TEXT_PRIMARY}; }}"
            f"QMenu::item:selected {{ background: {ACCENT}; color: #fff; }}"
        )
        act_delete = menu.addAction(_t("delete_custom_preset"))
        if menu.exec(self._list.mapToGlobal(pos)) == act_delete:
            path = self._presets[name]
            path.unlink(missing_ok=True)
            self._custom_names.discard(name)
            self._all.remove(name)
            del self._presets[name]
            self._filter(self._search.text())

    def accept(self):
        item = self._list.currentItem()
        if item:
            self.selected_name = item.text()
        super().accept()


# ── Favorite slot button ──────────────────────────────────────────────────────

class _FavoriteSlot(QPushButton):
    remove_requested = Signal()
    delete_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedHeight(42)
        self.setMinimumWidth(52)
        self.setMaximumWidth(160)
        self._name: str | None = None
        self._is_custom: bool = False
        self._refresh()

    def set_preset(self, name: str | None):
        self._name = name
        self._refresh()

    def set_custom(self, is_custom: bool) -> None:
        self._is_custom = is_custom

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
        act_delete = menu.addAction(_t("delete_custom_preset")) if self._is_custom else None
        chosen = menu.exec(event.globalPos())
        if chosen == act_remove:
            self.remove_requested.emit()
        elif act_delete and chosen == act_delete:
            self.delete_requested.emit()

    def _refresh(self):
        if self._name:
            label = self._name[:15] + "…" if len(self._name) > 16 else self._name
            self.setText(label)
            self.setToolTip(self._name)
            self.setMinimumWidth(52)
            self.setMaximumWidth(160)
            self.setStyleSheet(f"""
                QPushButton {{
                    background: {BG_BUTTON};
                    border: 1px solid {ACCENT}44;
                    border-radius: 6px;
                    color: {TEXT_PRIMARY};
                    font-size: 9pt;
                    padding: 2px 6px;
                }}
                QPushButton:hover {{ border-color: {ACCENT}; background: {BG_BUTTON_HOVER}; }}
            """)
        else:
            self.setText("")
            self.setToolTip(_t("empty_slot"))
            self.setFixedWidth(52)
            self.setStyleSheet(f"""
                QPushButton {{
                    background: {BG_CARD};
                    border: 1px dashed {BORDER};
                    border-radius: 6px;
                    color: {TEXT_SECONDARY};
                }}
                QPushButton:hover {{ border-color: {ACCENT}55; }}
            """)


# ── Save preset dialog ────────────────────────────────────────────────────────

class _SavePresetDialog(QDialog):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(_t("save_preset"))
        self.setMinimumWidth(340)
        from arctis_sound_manager.gui.theme import BG_MAIN
        self.setStyleSheet(f"background-color: {BG_MAIN}; color: {TEXT_PRIMARY};")
        self.name: str = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        layout.addWidget(QLabel(
            _t("save_preset_label"),
            styleSheet=f"color: {TEXT_PRIMARY}; font-size: 11pt; background: transparent;"
        ))

        self._edit = QLineEdit()
        self._edit.setPlaceholderText(_t("preset_name_placeholder"))
        self._edit.setStyleSheet(
            f"QLineEdit {{ background: {BG_BUTTON}; border: 1px solid {BORDER}; "
            f"border-radius: 6px; color: {TEXT_PRIMARY}; padding: 6px 10px; font-size: 11pt; }}"
            f"QLineEdit:focus {{ border-color: {ACCENT}; }}"
        )
        layout.addWidget(self._edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_save(self) -> None:
        name = self._edit.text().strip()
        if not name:
            self._edit.setFocus()
            return
        self.name = name
        self.accept()


# ── Preset bar ────────────────────────────────────────────────────────────────

class _PresetBar(QWidget):
    preset_selected = Signal(str, list)         # name, list[EqBand]
    save_requested  = Signal()
    macros_loaded   = Signal(float, float, float)  # basses, voix, aigus
    settings_loaded = Signal(dict)

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

        save_btn = QPushButton()
        save_btn.setFixedSize(32, 32)
        save_btn.setToolTip(_t("save_custom_preset"))
        save_btn.setIcon(_svg_icon("save_icon.svg", TEXT_PRIMARY))
        save_btn.setIconSize(save_btn.size() * 0.55)
        save_btn.setStyleSheet(self._icon_btn_ss())
        save_btn.clicked.connect(self._on_save_custom)
        row1.addWidget(save_btn)

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

        import_btn = QPushButton()
        import_btn.setFixedSize(32, 32)
        import_btn.setToolTip(_t("import_preset"))
        import_btn.setIcon(_svg_icon("import_icon.svg", TEXT_PRIMARY))
        import_btn.setIconSize(import_btn.size() * 0.55)
        import_btn.setStyleSheet(self._icon_btn_ss())
        import_btn.clicked.connect(self._on_import)
        row1.addWidget(import_btn)

        self._export_btn = QPushButton()
        self._export_btn.setFixedSize(32, 32)
        self._export_btn.setToolTip(_t("export_preset"))
        self._export_btn.setIcon(_svg_icon("export_icon.svg", TEXT_PRIMARY))
        self._export_btn.setIconSize(self._export_btn.size() * 0.55)
        self._export_btn.setStyleSheet(self._icon_btn_ss())
        self._export_btn.clicked.connect(self._on_export)
        row1.addWidget(self._export_btn)

        self._export_status = QLabel("")
        self._export_status.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 9pt; background: transparent;"
        )
        row1.addWidget(self._export_status)

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

        # ── Favorite slots (unlimited, wrapped in rows of _FAV_ROW_SIZE) ────────
        self._slots_container = QWidget()
        self._slots_container.setStyleSheet("background: transparent;")
        self._slots_layout = QVBoxLayout(self._slots_container)
        self._slots_layout.setContentsMargins(0, 0, 0, 0)
        self._slots_layout.setSpacing(4)
        self._slots: list[_FavoriteSlot] = []
        root.addWidget(self._slots_container)

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
        n = len(self._favs)
        self._fav_count.setText(f"{_t('favorites')} ({n})")

        # Remove old rows — hide() immediately so floating children don't stay
        # visible while Qt's async deleteLater() is pending.
        while self._slots_layout.count():
            item = self._slots_layout.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        self._slots.clear()

        # Rebuild rows
        for row_start in range(0, max(n, 1), _FAV_ROW_SIZE):
            row_w = QWidget()
            row_w.setStyleSheet("background: transparent;")
            row_layout = QHBoxLayout(row_w)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(4)
            for i in range(row_start, min(row_start + _FAV_ROW_SIZE, n)):
                name = self._favs[i]
                slot = _FavoriteSlot()
                slot.set_preset(name)
                slot.set_custom(_is_custom_preset(self._channel, name))
                slot.clicked.connect(lambda checked, idx=i: self._on_fav_slot(idx))
                slot.remove_requested.connect(lambda idx=i: self._on_fav_remove(idx))
                slot.delete_requested.connect(lambda idx=i: self._on_delete_custom(idx))
                row_layout.addWidget(slot)
                self._slots.append(slot)
            row_layout.addStretch(1)
            self._slots_layout.addWidget(row_w)
            if n == 0:
                break

    def _load_and_emit(self, name: str):
        presets = _list_presets(self._channel)
        if name not in presets:
            return
        path = presets[name]
        try:
            data = json.loads(path.read_text())
        except Exception:
            return
        bands = _parse_preset_data(data)
        self._cur_bands = bands
        self._active = name
        _set_active_preset(self._channel, name)
        self._refresh_display()
        self.preset_selected.emit(name, bands)
        macros = data.get("macros") or None
        if macros is not None:
            self.macros_loaded.emit(
                macros.get("basses", 0.0),
                macros.get("voix", 0.0),
                macros.get("aigus", 0.0),
            )
        else:
            self.macros_loaded.emit(0.0, 0.0, 0.0)
        settings = data.get("settings") or None
        if settings is not None:
            self.settings_loaded.emit(settings)

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_search(self):
        presets = _list_presets(self._channel)
        dlg = _PresetSearchDialog(presets, self)
        dlg.exec()
        self._presets = _list_presets(self._channel)
        if dlg.result() == QDialog.DialogCode.Accepted and dlg.selected_name:
            self._load_and_emit(dlg.selected_name)
        else:
            self._refresh_display()

    def _on_star(self):
        if self._active and self._active not in self._favs:
            self._favs.append(self._active)
            _save_favorites(self._channel, self._favs)
            self._refresh_display()

    def _on_save_custom(self):
        self.save_requested.emit()

    def notify_saved(self, name: str) -> None:
        """Called by SonarChannelWidget after the preset file has been written."""
        self._presets = _list_presets(self._channel)
        _set_active_preset(self._channel, name)
        self._active = name
        self._refresh_display()

    def _on_delete_custom(self, idx: int) -> None:
        name = self._favs[idx] if idx < len(self._favs) else None
        if not name:
            return
        _delete_custom_preset(self._channel, name)
        self._presets = _list_presets(self._channel)
        self._favs = [f for f in self._favs if f != name]
        _save_favorites(self._channel, self._favs)
        if self._active == name:
            self._active = "Flat"
            _set_active_preset(self._channel, "Flat")
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

    def _on_import(self) -> None:
        from arctis_sound_manager.gui.preset_import_dialog import PresetImportDialog
        dlg = PresetImportDialog(self._channel, self)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.imported_name:
            self._presets = _list_presets(self._channel)
            self._load_and_emit(dlg.imported_name)

    def _on_export(self) -> None:
        from arctis_sound_manager.gui.preset_share import build_asm_link
        from arctis_sound_manager.gui.preset_export_dialog import PresetExportDialog
        presets = _list_presets(self._channel)
        path = presets.get(self._active)
        if not path:
            self._export_status.setText(_t("export_no_preset"))
            return

        try:
            data = json.loads(path.read_text())
        except Exception:
            self._export_status.setText(_t("export_no_preset"))
            return

        # Invert _CHANNEL_TAG to get virtualAudioDevice from channel
        _TAG_TO_DEVICE = {v: k for k, v in _CHANNEL_TAG.items()}
        tag = _CHANNEL_TAG.get(self._channel, "[Game]")
        virtual_device = _TAG_TO_DEVICE.get(tag, self._channel)

        link = build_asm_link(self._active, virtual_device, data)
        dlg = PresetExportDialog(self._active, link, data, self)
        dlg.exec()


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

    def apply_theme(self, t=None) -> None:
        """Restyle macro sliders for the active theme."""
        _slider_qss = f"""
            QSlider::groove:horizontal {{
                height: 4px;
                background: {_theme.c('BG_BUTTON')};
                border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                background: {_theme.c('ACCENT')};
                width: 14px;
                height: 14px;
                margin: -5px 0;
                border-radius: 7px;
            }}
            QSlider::sub-page:horizontal {{
                background: {_theme.c('ACCENT')};
                border-radius: 2px;
            }}
        """
        for slider in self._sliders.values():
            slider.setStyleSheet(_slider_qss)
        for lbl in self._labels.values():
            lbl.setStyleSheet(
                f"color: {_theme.c('TEXT_PRIMARY')}; font-size: 10pt; font-weight: bold;"
            )

    def get_values(self) -> tuple[float, float, float]:
        return (
            self._sliders["basses"].value() / 10.0,
            self._sliders["voix"].value() / 10.0,
            self._sliders["aigus"].value() / 10.0,
        )

    def set_values(self, basses: float, voix: float, aigus: float,
                   emit: bool = True) -> None:
        for key, val in (("basses", basses), ("voix", voix), ("aigus", aigus)):
            self._sliders[key].blockSignals(True)
            self._sliders[key].setValue(int(val * 10))
            self._labels[key].setText(self._fmt(val))
            self._sliders[key].blockSignals(False)
        _save_macro(self._channel, {"basses": basses, "voix": voix, "aigus": aigus})
        if emit:
            self.macros_changed.emit(basses, voix, aigus)


# ── Channel widget ────────────────────────────────────────────────────────────

class SonarChannelWidget(QWidget):
    def __init__(self, channel: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._channel = channel
        self._target_override: str | None = None
        self._worker: _ApplyWorker | None = None
        self._pending_apply = False
        self._cur_bands: list = []
        self._committed_bands: list = []
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
        self._preset_bar.save_requested.connect(self._on_save_custom_preset)
        self._preset_bar.macros_loaded.connect(
            lambda b, v, a: self._macros.set_values(b, v, a, emit=False)
        )
        self._preset_bar.settings_loaded.connect(self._on_settings_loaded)
        pcl.addWidget(self._preset_bar)

        # ── Output passthrough toggle ─────────────────────────────────────────
        # On the Output tab, let the user send audio to the external sink as-is,
        # bypassing the EQ profile entirely. When enabled the EQ curve/macros are
        # greyed out because they no longer affect anything.
        self._passthrough_cb: QCheckBox | None = None
        if channel == "output":
            self._passthrough_cb = QCheckBox(_t("output_passthrough"))
            self._passthrough_cb.setToolTip(_t("output_passthrough_hint"))
            self._passthrough_cb.setChecked(_load_output_passthrough())
            self._passthrough_cb.toggled.connect(self._on_passthrough_toggled)
            pcl.addWidget(self._passthrough_cb)

        root.addWidget(preset_card)

        # ── EQ curve card ─────────────────────────────────────────────────────
        eq_card = self._card()
        ecl = QVBoxLayout(eq_card)
        ecl.setContentsMargins(16, 14, 16, 14)
        ecl.setSpacing(12)

        eq_header = QHBoxLayout()
        eq_header.addWidget(QLabel(_t("equalizer")))
        eq_header.addStretch(1)

        self._apply_btn = QPushButton(_t("apply_eq"))
        self._apply_btn.setVisible(False)
        self._apply_btn.setStyleSheet(f"""
            QPushButton {{
                background: {ACCENT};
                color: #fff;
                border: none;
                border-radius: 6px;
                padding: 4px 14px;
                font-size: 10pt;
                font-weight: bold;
            }}
            QPushButton:hover {{ background: #7a5af8; }}
        """)
        self._apply_btn.clicked.connect(self._on_apply_eq)
        eq_header.addWidget(self._apply_btn)

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
        elif channel in ("game", "chat", "media"):
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
                self._spatial = SpatialAudioWidget(channel="game")
                self._spatial.setVisible(_has_spatial)
                scl.addWidget(self._spatial)
                sep = QFrame()
                sep.setFrameShape(QFrame.Shape.HLine)
                sep.setStyleSheet(f"background: {BORDER}; border: none; max-height: 1px;")
                sep.setVisible(_has_spatial)
                scl.addWidget(sep)
            elif channel == "media":
                from arctis_sound_manager import device_state as _ds
                _has_spatial = _ds.get_spatial_engine() != "none"
                self._spatial = SpatialAudioWidget(channel="media")
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

            if channel in ("chat", "media", "output"):
                scl.addStretch(1)

            root.addWidget(settings_card)

        # Load initial preset
        self._load_initial()

        # Output tab: reflect the persisted passthrough state (grey out the EQ
        # controls when passthrough is already on).
        if self._passthrough_cb is not None and self._passthrough_cb.isChecked():
            self._set_eq_controls_enabled(False)

        # Apply the currently-active theme on first paint.
        self.apply_theme()

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

    def apply_theme(self, t=None) -> None:
        """Restyle this channel widget for the active theme."""
        self.setStyleSheet(f"background-color: {_theme.c('BG_MAIN')};")

        # Restyle all sonarCard widgets (preset card, eq card, settings card)
        for w in self.findChildren(QWidget, "sonarCard"):
            w.setStyleSheet(f"""
                QWidget#sonarCard {{
                    background-color: {_theme.c('BG_CARD')};
                    border: 1px solid {_theme.c('BORDER')};
                    border-radius: 12px;
                }}
            """)

        # Restyle settings card variants (settingsCard, microSettingsCard)
        for obj_name in ("settingsCard", "microSettingsCard"):
            for w in self.findChildren(QWidget, obj_name):
                w.setStyleSheet(f"""
                    QWidget#{obj_name} {{
                        background-color: {_theme.c('BG_CARD')};
                        border: 1px solid {_theme.c('BORDER')};
                        border-radius: 12px;
                    }}
                """)

        # Macro sliders — rebuild their QSS
        if hasattr(self, "_macros"):
            self._macros.apply_theme(t)

        # SpatialAudio / BoostVolume / SmartVolume sub-widgets
        if hasattr(self, "_spatial"):
            self._spatial.apply_theme(t)
        if hasattr(self, "_boost"):
            self._boost.apply_theme(t)
        if hasattr(self, "_smart"):
            self._smart.apply_theme(t)

        # EQ curve widget (already self-themed via paintEvent, just repaint)
        if hasattr(self, "_eq_widget"):
            self._eq_widget.apply_theme(t)

        # Apply button
        if hasattr(self, "_apply_btn"):
            self._apply_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {_theme.c('ACCENT')};
                    color: #fff;
                    border: none;
                    border-radius: 6px;
                    padding: 4px 14px;
                    font-size: 10pt;
                    font-weight: bold;
                }}
                QPushButton:hover {{ background: {_theme.c('BG_BUTTON_HOVER')}; }}
            """)

        # Status label
        if hasattr(self, "_status_lbl"):
            self._status_lbl.setStyleSheet(
                f"color: {_theme.c('TEXT_SECONDARY')}; font-size: 9pt;"
            )

    def _load_initial(self):
        name = _active_preset_name(self._channel)
        presets = _list_presets(self._channel)
        if name in presets:
            bands = _parse_preset(presets[name])
        else:
            bands = []
        self._cur_bands = bands
        self._committed_bands = list(bands)
        self._eq_widget.set_bands(bands)
        self._update_macro_curve()

    # ── Signals ───────────────────────────────────────────────────────────────

    @Slot(str, list)
    def _on_preset_selected(self, name: str, bands: list):
        self._cur_bands = bands
        self._committed_bands = list(bands)
        self._eq_widget.set_bands(bands)
        self._update_macro_curve()
        self._apply_btn.setVisible(False)
        self._schedule_apply()

    def _on_bands_changed(self, bands: list):
        self._cur_bands = bands
        self._apply_btn.setVisible(True)
        self._status_lbl.setText(_t("eq_pending"))

    def _on_apply_eq(self):
        self._committed_bands = list(self._cur_bands)
        self._apply_btn.setVisible(False)
        self._do_apply()

    def _on_save_custom_preset(self) -> None:
        dlg = _SavePresetDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.name:
            return
        bands = self._eq_widget.get_bands()
        basses, voix, aigus = self._macros.get_values()
        settings: dict = {}
        if hasattr(self, "_spatial"):
            settings["spatial"] = self._spatial.get_state()
        if hasattr(self, "_boost"):
            settings["boost"] = self._boost.get_state()
        if hasattr(self, "_smart"):
            settings["smart_volume"] = self._smart.get_state()
        _save_custom_preset(
            self._channel, dlg.name, bands,
            macros={"basses": basses, "voix": voix, "aigus": aigus},
            settings=settings or None,
        )
        self._committed_bands = list(bands)
        self._apply_btn.setVisible(False)
        self._preset_bar.notify_saved(dlg.name)

    def _on_settings_loaded(self, settings: dict) -> None:
        # Called when a preset is loaded; _on_preset_selected already scheduled
        # an apply via _schedule_apply(), so suppressing the state_changed /
        # macros_changed re-emissions prevents ~10 redundant filter-chain restarts
        # per preset click (emit=False on all sub-widget set_state calls).
        if not settings:
            return
        if hasattr(self, "_spatial") and "spatial" in settings:
            self._spatial.set_state(settings["spatial"], emit=False)
        if hasattr(self, "_boost") and "boost" in settings:
            self._boost.set_state(settings["boost"], emit=False)
        if hasattr(self, "_smart") and "smart_volume" in settings:
            self._smart.set_state(settings["smart_volume"], emit=False)

    def _on_macros_changed(self, basses: float, voix: float, aigus: float):
        self._update_macro_curve()
        self._schedule_apply()

    def _on_passthrough_toggled(self, on: bool):
        """Output tab: toggle EQ-bypass passthrough to the external sink."""
        _save_output_passthrough(on)
        self._set_eq_controls_enabled(not on)
        # Regenerate the output config immediately in the new mode.
        self._committed_bands = list(self._cur_bands)
        self._do_apply()

    def _set_eq_controls_enabled(self, enabled: bool) -> None:
        """Grey out the EQ curve + macros — used when Output passthrough is on,
        since the EQ profile no longer affects anything in that mode."""
        if hasattr(self, "_eq_widget"):
            self._eq_widget.setEnabled(enabled)
        if hasattr(self, "_macros"):
            self._macros.setEnabled(enabled)

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
        if self._worker is not None:
            # A worker is in flight (running, or finishing but not yet cleaned
            # up) — remember to re-apply once it is fully done.
            self._pending_apply = True
            return
        self._pending_apply = False
        basses, voix, aigus = self._macros.get_values()
        worker = _ApplyWorker(
            self._channel, list(self._committed_bands), basses, voix, aigus,
            target_override=self._target_override,
        )
        self._worker = worker
        worker.done.connect(self._on_apply_result)
        # Clean up only once the QThread has FULLY stopped. Dropping the
        # reference (and letting it be GC'd) from the `done` handler — which
        # fires from inside run() before the thread has actually stopped —
        # aborts with "QThread: Destroyed while thread is still running" when
        # the slider is dragged quickly (rapid re-applies), crashing the GUI
        # (issue #63). `finished` is emitted only after the thread has stopped.
        worker.finished.connect(self._on_worker_finished)
        worker.start()
        self._status_lbl.setText(_t("applying"))

    @Slot(bool)
    def _on_apply_result(self, ok: bool):
        self._status_lbl.setText(_t("applied") if ok else _t("error"))
        QTimer.singleShot(2000, self, lambda: self._status_lbl.setText(""))

    @Slot()
    def _on_worker_finished(self):
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.deleteLater()
        if self._pending_apply:
            self._pending_apply = False
            self._do_apply()


# ── Boost de Volume / Smart Volume — persistence ─────────────────────────────

_BOOST_FILE  = _CFG / "sonar_boost.json"
_SMART_FILE  = _CFG / "sonar_smart_volume.json"
# Output-channel passthrough: when enabled, sonar-output-eq is a plain copy of
# the audio to the external sink (no EQ profile applied), at the sink's native
# channel count. Kept per-channel-independent so only the Output tab uses it.
_OUTPUT_PASSTHROUGH_FILE = _CFG / "sonar_output_passthrough.json"

_BOOST_DEFAULTS: dict  = {"enabled": False, "db": 0.0}
_SMART_DEFAULTS: dict  = {"enabled": False, "level": 0.0, "loudness": "balanced"}


def _load_output_passthrough() -> bool:
    if _OUTPUT_PASSTHROUGH_FILE.exists():
        try:
            return bool(json.loads(_OUTPUT_PASSTHROUGH_FILE.read_text()).get("enabled", False))
        except Exception:
            pass
    return False


def _save_output_passthrough(enabled: bool) -> None:
    _CFG.mkdir(parents=True, exist_ok=True)
    _OUTPUT_PASSTHROUGH_FILE.write_text(json.dumps({"enabled": bool(enabled)}))


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

    def __init__(self, channel: str = "game", parent: QWidget | None = None):
        super().__init__(parent)
        self._channel = channel
        self._state = _load_spatial_audio(channel)
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

        note_key = "game_channel" if channel == "game" else "media_channel"
        note = QLabel(_t(note_key))
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
        self.__dict__[f"_slider_{key}"] = slider

        return w

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_toggle(self, checked):
        # checkStateChanged emits a Qt.CheckState; bool(Qt.CheckState.Unchecked)
        # is True in PySide6 (the enum is always truthy), so bool(checked) would
        # never register the OFF state — the Spatial Audio toggle was never saved
        # as disabled (issue #62). Compare against Checked explicitly.
        enabled = checked == Qt.CheckState.Checked
        self._state["enabled"] = enabled
        _save_spatial_audio(self._state, self._channel)
        self._detail.setVisible(enabled)
        self.state_changed.emit()

    def _on_slider(self, key: str, value: int):
        self._state[key] = value
        _save_spatial_audio(self._state, self._channel)
        lbl = self.__dict__.get(f"_val_lbl_{key}")
        if lbl:
            lbl.setText(str(value))
        self.state_changed.emit()

    def apply_theme(self, t=None) -> None:
        """Restyle spatial audio widget sliders for the active theme."""
        _slider_qss = f"""
            QLabel {{ background: transparent; border: none; }}
            QSlider::groove:horizontal {{
                height: 4px; background: {_theme.c('BG_BUTTON')}; border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                background: {_theme.c('ACCENT')}; width: 14px; height: 14px;
                margin: -5px 0; border-radius: 7px;
            }}
            QSlider::sub-page:horizontal {{ background: {_theme.c('ACCENT')}; border-radius: 2px; }}
        """
        self.setStyleSheet(_slider_qss)

    def get_state(self) -> dict:
        return dict(self._state)

    def set_state(self, state: dict, emit: bool = True) -> None:
        if not state:
            return
        self._state.update(state)
        _save_spatial_audio(self._state, self._channel)
        self._toggle.blockSignals(True)
        self._toggle.setChecked(self._state.get("enabled", False))
        self._toggle.blockSignals(False)
        self._detail.setVisible(self._state.get("enabled", False))
        for key in ("immersion", "distance"):
            slider = self.__dict__.get(f"_slider_{key}")
            lbl = self.__dict__.get(f"_val_lbl_{key}")
            val = self._state.get(key, 50)
            if slider:
                slider.blockSignals(True)
                slider.setValue(int(val))
                slider.blockSignals(False)
            if lbl:
                lbl.setText(str(val))
        if emit:
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

    def apply_theme(self, t=None) -> None:
        """Restyle boost volume widget for the active theme."""
        _slider_qss = f"""
            QLabel {{ background: transparent; border: none; }}
            QSlider::groove:horizontal {{
                height: 4px; background: {_theme.c('BG_BUTTON')}; border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                background: {_theme.c('ACCENT')}; width: 14px; height: 14px;
                margin: -5px 0; border-radius: 7px;
            }}
            QSlider::sub-page:horizontal {{ background: {_theme.c('ACCENT')}; border-radius: 2px; }}
        """
        self.setStyleSheet(_slider_qss)
        self._db_label.setStyleSheet(
            f"font-size: 11pt; font-weight: bold; color: {_theme.c('ACCENT')}; min-width: 60px;"
        )

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

    def get_state(self) -> dict:
        return dict(self._state)

    def set_state(self, state: dict, emit: bool = True) -> None:
        if not state:
            return
        self._state.update(state)
        _save_boost(self._state)
        self._toggle.blockSignals(True)
        self._toggle.setChecked(self._state.get("enabled", False))
        self._toggle.blockSignals(False)
        self._slider.blockSignals(True)
        self._slider.setValue(int(self._state.get("db", 0.0) * 10))
        self._slider.blockSignals(False)
        self._db_label.setText(self._fmt(self._state.get("db", 0.0)))
        self._detail.setVisible(self._state.get("enabled", False))
        if emit:
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

    def apply_theme(self, t=None) -> None:
        """Restyle smart volume widget for the active theme."""
        _slider_qss = f"""
            QLabel {{ background: transparent; border: none; }}
            QSlider::groove:horizontal {{
                height: 4px; background: {_theme.c('BG_BUTTON')}; border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                background: {_theme.c('ACCENT')}; width: 14px; height: 14px;
                margin: -5px 0; border-radius: 7px;
            }}
            QSlider::sub-page:horizontal {{ background: {_theme.c('ACCENT')}; border-radius: 2px; }}
        """
        self.setStyleSheet(_slider_qss)
        self._refresh_loudness()

    def _refresh_loudness(self):
        active = self._state.get("loudness", "balanced")
        for value, btn in self._loudness_btns.items():
            selected = value == active
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: {_theme.c('ACCENT') if selected else _theme.c('BG_BUTTON')};
                    color: {"#fff" if selected else _theme.c('TEXT_SECONDARY')};
                    border: 1px solid {_theme.c('ACCENT') if selected else _theme.c('BORDER')};
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

    def get_state(self) -> dict:
        return dict(self._state)

    def set_state(self, state: dict, emit: bool = True) -> None:
        if not state:
            return
        self._state.update(state)
        _save_smart_volume(self._state)
        self._toggle.blockSignals(True)
        self._toggle.setChecked(self._state.get("enabled", False))
        self._toggle.blockSignals(False)
        self._level_slider.blockSignals(True)
        self._level_slider.setValue(int(self._state.get("level", 50)))
        self._level_slider.blockSignals(False)
        self._level_val.setText(str(int(self._state.get("level", 50))))
        self._detail.setVisible(self._state.get("enabled", False))
        self._refresh_loudness()
        if emit:
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

    # Cached at class level so we only hit the filesystem once per session.
    _rnnoise_available: bool | None = None

    @classmethod
    def _check_rnnoise(cls) -> bool:
        if cls._rnnoise_available is None:
            cls._rnnoise_available = _find_ladspa_plugin("librnnoise*.so") is not None
        return cls._rnnoise_available

    def __init__(self, state: dict, parent=None):
        super().__init__(parent)
        self._state = state
        self.setObjectName("microCard")
        self.setStyleSheet(_micro_card_style())

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 14, 20, 14)
        root.setSpacing(8)

        self._toggle = QToggle(is_checkbox=True)

        rnnoise_ok = self._check_rnnoise()
        if rnnoise_ok:
            self._toggle.setChecked(state["noiseCanceling"]["enabled"])
        else:
            # Plugin missing: force toggle off and lock it so the user gets
            # a clear error message instead of a silent no-op.
            self._toggle.setChecked(False)
            self._toggle.setEnabled(False)
            _tip = (
                "noise-suppression-for-voice is not installed.\n"
                "ClearCast AI Noise Cancellation requires the rnnoise LADSPA plugin.\n\n"
                "Install it with:\n"
                "  Fedora/Nobara:  sudo dnf copr enable lkiesow/noise-suppression-for-voice\n"
                "                  sudo dnf install ladspa-realtime-noise-suppression-plugin\n"
                "  Debian/Ubuntu:  not packaged — ASM builds it from source\n"
                "                  (use the dependency installer, or build manually)\n"
                "  Arch/CachyOS:   sudo pacman -S noise-suppression-for-voice"
            )
            self._toggle.setToolTip(_tip)

        root.addLayout(_make_header_row("CLEARCAST AI NOISE CANCELLATION", self._toggle))

        if not rnnoise_ok:
            missing_lbl = QLabel(
                "⚠️  noise-suppression-for-voice not installed — "
                "ClearCast unavailable"
            )
            missing_lbl.setStyleSheet(
                "color: #e8a000; font-size: 9pt; font-style: italic;"
            )
            missing_lbl.setWordWrap(True)
            missing_lbl.setToolTip(self._toggle.toolTip())
            root.addWidget(missing_lbl)

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

        self._toggle.checkStateChanged.connect(self._on_toggle)
        self._set_enabled(rnnoise_ok and state["noiseCanceling"]["enabled"])

    def _set_enabled(self, enabled: bool):
        self._slider.setEnabled(enabled)
        self._waveform.setVisible(enabled)

    def _on_toggle(self, checked):
        # bool(Qt.CheckState.Unchecked) is True in PySide6 — compare explicitly
        # so the OFF state actually registers (same class of bug as issue #62).
        enabled = checked == Qt.CheckState.Checked
        self._state["noiseCanceling"]["enabled"] = enabled
        self._set_enabled(enabled)
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
        # bool(Qt.CheckState.Unchecked) is True in PySide6 — compare explicitly,
        # otherwise this reduction could never be turned off (issue #62 class).
        toggle.checkStateChanged.connect(
            lambda c, k=key: self._on_toggle(k, c == Qt.CheckState.Checked))
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
        # bool(Qt.CheckState.Unchecked) is True in PySide6 — compare explicitly
        # so the OFF state actually registers (same class of bug as issue #62).
        enabled = checked == Qt.CheckState.Checked
        self._state["noiseGate"]["enabled"] = enabled
        self._set_enabled(enabled)
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
        # bool(Qt.CheckState.Unchecked) is True in PySide6 — compare explicitly
        # so the OFF state actually registers (same class of bug as issue #62).
        enabled = checked == Qt.CheckState.Checked
        self._state["compressor"]["enabled"] = enabled
        self._set_enabled(enabled)
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

        # Generate sonar-micro-eq.conf if it doesn't exist yet (first run)
        _micro_conf = Path.home() / ".config" / "pipewire" / "filter-chain.conf.d" / "sonar-micro-eq.conf"
        if not _micro_conf.exists():
            self._schedule_apply()

    def _on_micro_changed(self):
        _save_micro_proc(self._micro_state)
        # Re-generate filter-chain conf (EQ part — DSP for noise/gate pending plugins)
        self._schedule_apply()


# ── Sonar page (top-level) ────────────────────────────────────────────────────

class SonarPage(QWidget):
    def __init__(self, embedded: bool = False, parent: QWidget | None = None):
        super().__init__(parent)

        # Anti-amplification state for boost/smart global apply (1a)
        self._apply_all_worker: _ApplyAllWorker | None = None
        self._pending_apply_all: bool = False

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
                sc.restart("pipewire", "wireplumber", "pipewire-pulse", "filter-chain",
                           timeout=20)
            else:
                logging.getLogger(__name__).info("Stale Sonar configs fixed, restarting filter-chain")
                sc.restart("filter-chain", timeout=15)

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
        self._media_widget  = SonarChannelWidget("media")
        self._chat_widget   = SonarChannelWidget("chat")
        self._micro_widget  = SonarMicroWidget()
        self._output_widget = SonarChannelWidget("output")

        self._tabs.addTab(self._game_widget,   _t("game"))
        self._tabs.addTab(self._media_widget,  _t("media"))
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
        self._media_widget._spatial.state_changed.connect(self._on_media_spatial_changed)
        self._media_widget._boost.state_changed.connect(self._on_boost_changed)
        self._media_widget._smart.state_changed.connect(self._on_smart_changed)
        self._chat_widget._boost.state_changed.connect(self._on_boost_changed)
        self._chat_widget._smart.state_changed.connect(self._on_smart_changed)

        # Apply the currently-active theme so a saved non-default theme renders
        # correctly on first paint.
        self.apply_theme()

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

    def _on_media_spatial_changed(self):
        """Media spatial audio toggle changed — re-apply media channel conf."""
        self._media_widget._schedule_apply()

    def _on_boost_changed(self):
        """Boost changed — re-apply all EQ channels via a single ApplyAll restart."""
        self._schedule_apply_all()

    def _on_smart_changed(self):
        """Smart Volume changed — re-apply all EQ channels via a single ApplyAll restart."""
        self._schedule_apply_all()

    def _schedule_apply_all(self) -> None:
        """Start (or queue) a single _ApplyAllWorker, coalescing rapid calls.

        Anti-amplification debounce (fix #100 / LOT 1a): boost or smart-volume
        changes used to fan out to 3–4 individual _ApplyWorkers, each triggering
        its own filter-chain restart.  This method ensures at most ONE restart is
        in flight at any time; a second call while a worker is running sets the
        _pending_apply_all flag so that exactly one more restart is scheduled
        when the current worker finishes.
        """
        if self._apply_all_worker is not None:
            # A restart is already in flight — remember to run one more after.
            self._pending_apply_all = True
            return
        self._pending_apply_all = False
        worker = _ApplyAllWorker()
        self._apply_all_worker = worker
        worker.done.connect(self._on_apply_all_done)
        worker.finished.connect(self._on_apply_all_finished)
        worker.start()

    @Slot(bool)
    def _on_apply_all_done(self, ok: bool):
        import logging
        if not ok:
            logging.getLogger(__name__).warning("_ApplyAllWorker reported failure")

    @Slot()
    def _on_apply_all_finished(self):
        worker = self._apply_all_worker
        self._apply_all_worker = None
        if worker is not None:
            worker.deleteLater()
        if self._pending_apply_all:
            self._pending_apply_all = False
            self._schedule_apply_all()

    def apply_theme(self, t=None) -> None:
        """Restyle the Sonar page and all its channel widgets for the active theme."""
        self.setStyleSheet(f"background-color: {_theme.c('BG_MAIN')};")

        self._tabs.setStyleSheet(f"""
            QTabWidget::pane {{
                border: none;
                background: transparent;
            }}
            QTabBar::tab {{
                background: {_theme.c('BG_BUTTON')};
                color: {_theme.c('TEXT_SECONDARY')};
                border: 1px solid {_theme.c('BORDER')};
                border-bottom: none;
                border-radius: 6px 6px 0 0;
                padding: 7px 22px;
                margin-right: 3px;
                font-size: 11pt;
            }}
            QTabBar::tab:selected {{
                background: {_theme.c('BG_CARD')};
                color: {_theme.c('TEXT_PRIMARY')};
                border-color: {_theme.c('BORDER')};
            }}
            QTabBar::tab:hover {{
                background: {_theme.c('BG_BUTTON_HOVER')};
                color: {_theme.c('TEXT_PRIMARY')};
            }}
        """)

        # Propagate to each channel widget (they own cards, macro sliders, etc.)
        for widget in (self._game_widget, self._media_widget, self._chat_widget,
                       self._micro_widget, self._output_widget):
            widget.apply_theme(t)

    def apply_all_from_files(self) -> None:
        """Re-apply all 3 EQ channels from current config files (used by profile system)."""
        worker = _ApplyAllWorker()
        worker.done.connect(lambda ok: None)  # fire-and-forget
        worker.start()
        self._apply_all_worker = worker  # prevent GC

    def notify_external_preset_change(self, channel: str, name: str) -> None:
        """Refresh displayed preset after an external apply (e.g. tray picker).

        Updates the EQ curve, preset label and macro curve without
        triggering a second filter-chain restart.
        """
        widget_map = {
            "game":   self._game_widget,
            "media":  self._media_widget,
            "chat":   self._chat_widget,
            "output": self._output_widget,
        }
        widget = widget_map.get(channel)
        if widget is None:
            return

        presets = _list_presets(channel)
        path = presets.get(name)
        if path is None:
            return
        try:
            bands = _parse_preset(path)
        except Exception:
            return

        widget._cur_bands = bands
        widget._committed_bands = list(bands)
        widget._eq_widget.set_bands(bands)
        widget._update_macro_curve()
        widget._preset_bar._active = name
        widget._preset_bar._refresh_display()
        widget._preset_bar.updateGeometry()
        widget._preset_bar.update()
