"""Background update checker — queries GitHub releases API once per day."""
from __future__ import annotations

import json
import logging
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QThread, Signal

log = logging.getLogger(__name__)

_CACHE_FILE = Path.home() / ".config" / "arctis_manager" / ".update_check_cache"
_CACHE_TTL_HOURS = 24
_API_TIMEOUT = 5  # seconds
_REPO = "loteran/Arctis-Sound-Manager"

# Regex: "1.0.2b" → (1, 0, 2, "b"), "1.0.3" → (1, 0, 3, "")
_VER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)([a-z]*)$")


def _parse_version(v: str) -> tuple[int, int, int, str] | None:
    m = _VER_RE.match(v.strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4)


def _version_gt(a: str, b: str) -> bool:
    """Return True if version *a* is strictly newer than *b*.

    Beta suffixes (e.g. "b") sort before the bare release:
    1.0.2b < 1.0.2 < 1.0.3.
    """
    pa, pb = _parse_version(a), _parse_version(b)
    if pa is None or pb is None:
        return False
    na, sa = pa[:3], pa[3]
    nb, sb = pb[:3], pb[3]
    if na != nb:
        return na > nb
    # Same numeric part: "" (release) > "b" (beta)
    if sa == sb:
        return False
    if sa == "":
        return True  # a is release, b is beta
    if sb == "":
        return False  # a is beta, b is release
    return sa > sb


class UpdateCheckWorker(QThread):
    """Emit (version, url) if a newer release exists, else ("", "")."""

    result = Signal(str, str)

    def __init__(self, current_version: str):
        super().__init__()
        self._current = current_version

    def run(self):
        try:
            self._check()
        except Exception as exc:
            log.debug("Update check failed: %s", exc)
            self.result.emit("", "")

    def _check(self):
        if _parse_version(self._current) is None:
            self.result.emit("", "")
            return

        # Try cache first
        latest_str, url = self._read_cache()
        if latest_str is None:
            latest_str, url = self._fetch()
            if latest_str:
                self._write_cache(latest_str, url)

        if not latest_str:
            self.result.emit("", "")
            return

        if _version_gt(latest_str, self._current):
            self.result.emit(latest_str, url)
        else:
            self.result.emit("", "")

    def _read_cache(self) -> tuple[str | None, str]:
        if not _CACHE_FILE.exists():
            return None, ""
        try:
            data = json.loads(_CACHE_FILE.read_text())
            last = datetime.fromisoformat(data["last_check"])
            age_hours = (datetime.now(timezone.utc) - last).total_seconds() / 3600
            if age_hours < _CACHE_TTL_HOURS:
                return data["latest_version"], data["release_url"]
        except Exception:
            pass
        return None, ""

    def _fetch(self) -> tuple[str, str]:
        is_beta = "b" in self._current or "dev" in self._current
        if is_beta:
            api_url = f"https://api.github.com/repos/{_REPO}/releases?per_page=5"
        else:
            api_url = f"https://api.github.com/repos/{_REPO}/releases/latest"

        req = urllib.request.Request(api_url, headers={"Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=_API_TIMEOUT) as resp:
            data = json.loads(resp.read())

        if is_beta:
            for rel in data:
                if not rel.get("draft", False):
                    tag = rel["tag_name"].lstrip("v")
                    return tag, rel["html_url"]
            return "", ""
        else:
            tag = data["tag_name"].lstrip("v")
            return tag, data["html_url"]

    @staticmethod
    def _write_cache(version: str, url: str):
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(json.dumps({
            "last_check": datetime.now(timezone.utc).isoformat(),
            "latest_version": version,
            "release_url": url,
        }))
