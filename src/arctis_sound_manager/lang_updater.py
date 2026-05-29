# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Background lang updater — checks GitHub for new/updated .ini files once per run."""
from __future__ import annotations

import json
import logging
import urllib.request
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from arctis_sound_manager.constants import HOME_LANG_FOLDER

log = logging.getLogger(__name__)

_REPO = "loteran/Arctis-Sound-Manager"
_LANG_PATH = "src/arctis_sound_manager/lang"
_SHA_CACHE = Path.home() / ".config" / "arctis_manager" / ".lang_sha_cache"
_API_TIMEOUT = 8


class LangUpdateWorker(QThread):
    """Download new or updated translation files from GitHub.

    Emits langs_updated([code, ...]) only when at least one file changed.
    """

    langs_updated: Signal = Signal(list)

    def run(self) -> None:
        try:
            updated = self._check_and_download()
            if updated:
                self.langs_updated.emit(updated)
        except Exception as exc:
            log.debug("Lang update check failed: %s", exc)

    def _check_and_download(self) -> list[str]:
        api_url = (
            f"https://api.github.com/repos/{_REPO}/contents/{_LANG_PATH}?ref=main"
        )
        req = urllib.request.Request(
            api_url, headers={"Accept": "application/vnd.github+json"}
        )
        with urllib.request.urlopen(req, timeout=_API_TIMEOUT) as resp:
            remote_files: list[dict] = json.loads(resp.read())

        sha_cache = _load_sha_cache()
        HOME_LANG_FOLDER.mkdir(parents=True, exist_ok=True)

        updated: list[str] = []
        new_cache = dict(sha_cache)

        for entry in remote_files:
            name: str = entry.get("name", "")
            if not name.endswith(".ini"):
                continue
            code = name[:-4]
            if code == "en":
                continue  # bundled source file, never overwrite
            remote_sha: str = entry.get("sha", "")
            if sha_cache.get(name) == remote_sha:
                continue  # unchanged

            dl_url: str = entry.get("download_url", "")
            if not dl_url:
                continue
            log.info("Downloading lang update: %s", name)
            try:
                with urllib.request.urlopen(
                    urllib.request.Request(dl_url), timeout=_API_TIMEOUT
                ) as r:
                    content = r.read()
                (HOME_LANG_FOLDER / name).write_bytes(content)
                new_cache[name] = remote_sha
                updated.append(code)
            except Exception as exc:
                log.warning("Failed to download %s: %s", name, exc)

        if new_cache != sha_cache:
            _save_sha_cache(new_cache)

        return updated


def _load_sha_cache() -> dict[str, str]:
    if _SHA_CACHE.exists():
        try:
            return json.loads(_SHA_CACHE.read_text())
        except Exception:
            pass
    return {}


def _save_sha_cache(cache: dict[str, str]) -> None:
    _SHA_CACHE.parent.mkdir(parents=True, exist_ok=True)
    _SHA_CACHE.write_text(json.dumps(cache))
