"""
tray_eq_presets.py — EQ preset helpers for the system-tray menu.

Reads the current EQ mode and returns the appropriate preset list:
  - custom mode → presets from eq_presets.json
  - sonar mode  → game-channel favorites from .sonar_favorites_game.json

apply_custom_preset() is synchronous (instant D-Bus command).
SonarPresetApplier wraps _ApplyWorker in a fire-and-forget pattern.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal

_CFG = Path.home() / ".config" / "arctis_manager"
_STATE_FILE = _CFG / ".eq_mode"
_CUSTOM_PRESETS_FILE = _CFG / "eq_presets.json"

_MAX_PRESETS = 15

logger = logging.getLogger(__name__)


def current_eq_mode() -> str:
    """Return 'custom' or 'sonar' based on the persisted mode file."""
    return _STATE_FILE.read_text().strip() if _STATE_FILE.exists() else "custom"


def list_tray_presets() -> tuple[str, list[str]]:
    """Return (mode, preset_names) capped at _MAX_PRESETS entries.

    For custom mode: names from eq_presets.json.
    For sonar mode: game-channel favorites from .sonar_favorites_game.json.
    """
    mode = current_eq_mode()
    if mode == "custom":
        try:
            data: dict = json.loads(_CUSTOM_PRESETS_FILE.read_text())
            return "custom", list(data.keys())[:_MAX_PRESETS]
        except Exception:
            return "custom", []
    else:
        fav_file = _CFG / ".sonar_favorites_game.json"
        try:
            names: list[str] = json.loads(fav_file.read_text())
            return "sonar", [n for n in names if n][:_MAX_PRESETS]
        except Exception:
            return "sonar", []


def apply_custom_preset(name: str) -> bool:
    """Apply a custom EQ preset by name. Returns True on success."""
    try:
        data: dict = json.loads(_CUSTOM_PRESETS_FILE.read_text())
        bands: list[int] | None = data.get(name)
        if bands is None:
            return False
        from arctis_sound_manager.gui.dbus_wrapper import DbusWrapper
        DbusWrapper.send_eq_command(list(bands))
        return True
    except Exception as e:
        logger.error("apply_custom_preset(%s) failed: %s", name, e)
        return False


def _get_sonar_active_game_preset() -> str:
    """Return the currently active Sonar game preset name."""
    f = _CFG / ".sonar_preset_game"
    return f.read_text().strip() if f.exists() else ""


class SonarPresetApplier(QObject):
    """Fire-and-forget applier for a Sonar game-channel preset.

    Holds a strong reference to the running QThread so it is not GC'd.
    Emits done(ok: bool, name: str) when finished.
    """
    done = Signal(bool, str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._worker: QThread | None = None

    def is_running(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def apply(self, name: str) -> None:
        if self.is_running():
            logger.warning("SonarPresetApplier: already running, ignoring %s", name)
            return

        from arctis_sound_manager.gui.sonar_page import (
            _ApplyWorker,
            _list_presets,
            _load_favorites,
            _load_macro,
            _parse_preset,
            _set_active_preset,
        )
        from arctis_sound_manager.gui.eq_curve_widget import EqBand

        presets = _list_presets("game")
        path = presets.get(name)
        if path is None:
            logger.warning("Sonar preset '%s' not found", name)
            self.done.emit(False, name)
            return

        try:
            bands = _parse_preset(path)
        except Exception as e:
            logger.error("Failed to parse sonar preset '%s': %s", name, e)
            self.done.emit(False, name)
            return

        macro = _load_macro("game")
        _set_active_preset("game", name)

        worker = _ApplyWorker(
            "game", bands,
            macro.get("basses", 0.0),
            macro.get("voix", 0.0),
            macro.get("aigus", 0.0),
        )
        self._worker = worker
        worker.done.connect(lambda ok: self._on_done(ok, name))
        worker.start()

    def _on_done(self, ok: bool, name: str) -> None:
        self._worker = None
        self.done.emit(ok, name)
