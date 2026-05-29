# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
tray_eq_presets.py — EQ preset helpers for the system-tray menu.

Custom mode : flat list from eq_presets.json, applied globally via D-Bus.
Sonar mode  : per-channel favorites, applied via _ApplyWorker (filter-chain restart).
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

SONAR_CHANNELS: list[tuple[str, str]] = [
    ("game",   "Game"),
    ("media",  "Media"),
    ("chat",   "Chat"),
    ("micro",  "Micro"),
    ("output", "Output"),
]

logger = logging.getLogger(__name__)


def current_eq_mode() -> str:
    return _STATE_FILE.read_text().strip() if _STATE_FILE.exists() else "custom"


def list_custom_presets() -> list[str]:
    """Return custom preset names (eq_presets.json), capped at _MAX_PRESETS."""
    try:
        data: dict = json.loads(_CUSTOM_PRESETS_FILE.read_text())
        return list(data.keys())[:_MAX_PRESETS]
    except Exception:
        return []


def list_sonar_channel_presets(channel: str) -> list[str]:
    """Return favorite preset names for one Sonar channel, capped at _MAX_PRESETS."""
    fav_file = _CFG / f".sonar_favorites_{channel}.json"
    try:
        names: list[str] = json.loads(fav_file.read_text())
        return [n for n in names if n][:_MAX_PRESETS]
    except Exception:
        return []


def get_sonar_active_preset(channel: str) -> str:
    """Return the currently active Sonar preset name for a channel."""
    f = _CFG / f".sonar_preset_{channel}"
    return f.read_text().strip() if f.exists() else ""


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


class SonarPresetApplier(QObject):
    """Fire-and-forget applier for any Sonar channel preset.

    Holds a strong reference to the running QThread so it is not GC'd.
    Emits done(ok: bool, channel: str, name: str) when finished.
    """
    done = Signal(bool, str, str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._worker: QThread | None = None

    def is_running(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def apply(self, channel: str, name: str) -> None:
        if self.is_running():
            logger.warning("SonarPresetApplier: already running, ignoring %s/%s", channel, name)
            return

        from arctis_sound_manager.gui.sonar_page import (
            _ApplyWorker,
            _list_presets,
            _load_macro,
            _parse_preset,
            _set_active_preset,
        )

        presets = _list_presets(channel)
        path = presets.get(name)
        if path is None:
            logger.warning("Sonar preset '%s' not found for channel '%s'", name, channel)
            self.done.emit(False, channel, name)
            return

        try:
            bands = _parse_preset(path)
        except Exception as e:
            logger.error("Failed to parse sonar preset '%s': %s", name, e)
            self.done.emit(False, channel, name)
            return

        macro = _load_macro(channel)
        _set_active_preset(channel, name)

        worker = _ApplyWorker(
            channel, bands,
            macro.get("basses", 0.0),
            macro.get("voix", 0.0),
            macro.get("aigus", 0.0),
        )
        self._worker = worker
        worker.done.connect(lambda ok: self._on_done(ok, channel, name))
        worker.start()

    def _on_done(self, ok: bool, channel: str, name: str) -> None:
        self._worker = None
        self.done.emit(ok, channel, name)
