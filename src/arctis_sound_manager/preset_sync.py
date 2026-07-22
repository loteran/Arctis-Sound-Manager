# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Preset sync — downloads new Sonar presets from GitHub at startup (once per day)."""
from __future__ import annotations

import json
import logging
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from PySide6.QtCore import QThread, Signal

log = logging.getLogger(__name__)

_MANIFEST_URL = (
    "https://raw.githubusercontent.com/loteran/Arctis-Sound-Manager"
    "/main/presets_manifest.json"
)
_PRESET_RAW_BASE = (
    "https://raw.githubusercontent.com/loteran/Arctis-Sound-Manager"
    "/main/src/arctis_sound_manager/gui/presets/"
)
_CFG         = Path.home() / ".config" / "arctis_manager"
_PRESETS_DIR = _CFG / "sonar_presets"
_CACHE_FILE  = _CFG / ".preset_sync_cache"
_BUNDLED_DIR = Path(__file__).parent / "gui" / "presets"
_CACHE_TTL_H = 24
# Anti-burst floor for forced checks (5 minutes): a real relaunch always
# checks, a crash loop or three launches in a row does not re-fetch each time.
_FORCE_MIN_INTERVAL_H = 5 / 60
_TIMEOUT     = 10


class PresetSyncWorker(QThread):
    """Fetch manifest, download missing presets, emit count of new ones.

    *force* checks regardless of the 24 h cache, with only a short anti-burst
    guard kept. The app start-up path uses it: presets are published whenever
    SteelSeries ships a new pack, so with the daily cache alone a check that
    happened to run shortly *before* a batch landed left the user without it
    for a full day, with no way to ask for one — closing and reopening ASM
    changed nothing, since the cache decides, not the launch. Checking on every
    start matches what people expect from reopening an app, and costs one small
    JSON request.
    """

    new_presets_added = Signal(int)

    def __init__(self, force: bool = False, parent=None) -> None:
        super().__init__(parent)
        self._force = force

    def run(self) -> None:
        try:
            self._sync()
        except Exception as exc:
            log.debug("Preset sync failed: %s", exc)

    def _sync(self) -> None:
        if not self._should_check():
            return

        manifest = self._fetch_manifest()
        if manifest is None:
            return

        filenames: list[str] = manifest.get("presets", [])

        available: set[str] = set()
        if _BUNDLED_DIR.exists():
            available.update(p.name for p in _BUNDLED_DIR.glob("*.json"))
        if _PRESETS_DIR.exists():
            available.update(p.name for p in _PRESETS_DIR.glob("*.json"))

        missing = [f for f in filenames if f not in available]
        downloaded = sum(1 for f in missing if self._download(f))

        self._write_cache()

        if downloaded:
            log.info("Preset sync: %d new preset(s) downloaded.", downloaded)
            self.new_presets_added.emit(downloaded)

    def _should_check(self) -> bool:
        if not _CACHE_FILE.exists():
            return True
        try:
            data = json.loads(_CACHE_FILE.read_text())
            last = datetime.fromisoformat(data["last_check"])
            age_h = (datetime.now(timezone.utc) - last).total_seconds() / 3600
        except Exception:
            return True
        if self._force:
            # Anti-burst only: relaunching the app a few times in a row (or a
            # crash loop) must not hammer GitHub, but any real relaunch checks.
            return age_h >= _FORCE_MIN_INTERVAL_H
        return age_h >= _CACHE_TTL_H

    def _fetch_manifest(self) -> dict | None:
        try:
            req = urllib.request.Request(
                _MANIFEST_URL, headers={"Accept": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
                return json.loads(r.read())
        except Exception as exc:
            log.debug("Failed to fetch preset manifest: %s", exc)
            return None

    def _download(self, filename: str) -> bool:
        try:
            url = _PRESET_RAW_BASE + quote(filename)
            with urllib.request.urlopen(urllib.request.Request(url), timeout=_TIMEOUT) as r:
                content = r.read()
            _PRESETS_DIR.mkdir(parents=True, exist_ok=True)
            (_PRESETS_DIR / filename).write_bytes(content)
            log.info("Downloaded preset: %s", filename)
            return True
        except Exception as exc:
            log.debug("Failed to download %s: %s", filename, exc)
            return False

    def _write_cache(self) -> None:
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(
            json.dumps({"last_check": datetime.now(timezone.utc).isoformat()})
        )
