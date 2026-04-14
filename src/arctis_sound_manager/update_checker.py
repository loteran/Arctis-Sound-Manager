"""Background update checker — queries GitHub releases API once per day."""
from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import urllib.request
from datetime import datetime, timezone
from enum import Enum, auto
from pathlib import Path

from PySide6.QtCore import QThread, Signal


class InstallMethod(Enum):
    RPM = auto()    # dnf / COPR / rpm
    PACMAN = auto() # pacman / AUR
    APT = auto()    # apt / PPA / deb
    PIPX = auto()   # pipx (source install)
    PIP = auto()    # pip --user fallback
    UNKNOWN = auto()


def detect_install_method() -> InstallMethod:
    """Detect how ASM was installed by checking package managers."""
    try:
        r = subprocess.run(
            ["rpm", "-q", "arctis-sound-manager"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            return InstallMethod.RPM
    except FileNotFoundError:
        pass

    try:
        r = subprocess.run(
            ["pacman", "-Q", "arctis-sound-manager"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            return InstallMethod.PACMAN
    except FileNotFoundError:
        pass

    try:
        r = subprocess.run(
            ["dpkg", "-s", "arctis-sound-manager"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            return InstallMethod.APT
    except FileNotFoundError:
        pass

    if shutil.which("pipx"):
        try:
            r = subprocess.run(
                ["pipx", "list", "--short"],
                capture_output=True, text=True, timeout=5,
            )
            if "arctis-sound-manager" in r.stdout:
                return InstallMethod.PIPX
        except FileNotFoundError:
            pass

    return InstallMethod.PIP


PACKAGE_MANAGER_COMMANDS: dict[InstallMethod, str] = {
    InstallMethod.RPM:    "sudo dnf upgrade arctis-sound-manager",
    InstallMethod.PACMAN: "paru -Syu arctis-sound-manager",
    InstallMethod.APT:    "sudo apt update && sudo apt upgrade arctis-sound-manager",
}

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


def _find_wheel_url(assets: list[dict]) -> str:
    """Find the .whl asset URL from a GitHub release."""
    for asset in assets:
        name = asset.get("name", "")
        if name.endswith(".whl") and "arctis_sound_manager" in name:
            return asset["browser_download_url"]
    return ""


class UpdateCheckWorker(QThread):
    """Emit (version, url, wheel_url) if a newer release exists, else ("", "", "")."""

    result = Signal(str, str, str)

    def __init__(self, current_version: str):
        super().__init__()
        self._current = current_version

    def run(self):
        try:
            self._check()
        except Exception as exc:
            log.debug("Update check failed: %s", exc)
            self.result.emit("", "", "")

    def _check(self):
        if _parse_version(self._current) is None:
            self.result.emit("", "", "")
            return

        # Try cache first
        latest_str, url, wheel_url = self._read_cache()
        if latest_str is None:
            latest_str, url, wheel_url = self._fetch()
            if latest_str:
                self._write_cache(latest_str, url, wheel_url)

        if not latest_str:
            self.result.emit("", "", "")
            return

        if _version_gt(latest_str, self._current):
            self.result.emit(latest_str, url, wheel_url)
        else:
            self.result.emit("", "", "")

    def _read_cache(self) -> tuple[str | None, str, str]:
        if not _CACHE_FILE.exists():
            return None, "", ""
        try:
            data = json.loads(_CACHE_FILE.read_text())
            last = datetime.fromisoformat(data["last_check"])
            age_hours = (datetime.now(timezone.utc) - last).total_seconds() / 3600
            if age_hours < _CACHE_TTL_HOURS:
                return data["latest_version"], data["release_url"], data.get("wheel_url", "")
        except Exception:
            pass
        return None, "", ""

    def _fetch(self) -> tuple[str, str, str]:
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
                    wheel_url = _find_wheel_url(rel.get("assets", []))
                    return tag, rel["html_url"], wheel_url
            return "", "", ""
        else:
            tag = data["tag_name"].lstrip("v")
            wheel_url = _find_wheel_url(data.get("assets", []))
            return tag, data["html_url"], wheel_url

    @staticmethod
    def _write_cache(version: str, url: str, wheel_url: str):
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(json.dumps({
            "last_check": datetime.now(timezone.utc).isoformat(),
            "latest_version": version,
            "release_url": url,
            "wheel_url": wheel_url,
        }))


class UpdateInstallWorker(QThread):
    """Download a wheel and install it. Emits (success, message)."""

    finished = Signal(bool, str)

    def __init__(self, wheel_url: str):
        super().__init__()
        self._wheel_url = wheel_url

    def run(self):
        import tempfile

        try:
            # Download wheel
            tmp = tempfile.mkdtemp(prefix="asm_update_")
            filename = self._wheel_url.rsplit("/", 1)[-1]
            wheel_path = Path(tmp) / filename
            log.info("Downloading %s", self._wheel_url)
            req = urllib.request.Request(self._wheel_url)
            with urllib.request.urlopen(req, timeout=30) as resp:
                wheel_path.write_bytes(resp.read())

            # Detect install method and install
            pipx = shutil.which("pipx")
            if pipx:
                log.info("Installing via pipx")
                r = subprocess.run(
                    [pipx, "install", str(wheel_path), "--force"],
                    capture_output=True, text=True, timeout=120,
                )
            else:
                log.info("Installing via pip")
                pip = shutil.which("pip3") or shutil.which("pip")
                if pip:
                    r = subprocess.run(
                        [pip, "install", "--user", "--force-reinstall", str(wheel_path)],
                        capture_output=True, text=True, timeout=120,
                    )
                else:
                    r = subprocess.run(
                        ["python3", "-m", "pip", "install", "--user", "--force-reinstall", str(wheel_path)],
                        capture_output=True, text=True, timeout=120,
                    )

            # Cleanup temp
            shutil.rmtree(tmp, ignore_errors=True)

            if r.returncode == 0:
                # Clear update cache so the banner disappears on restart
                _CACHE_FILE.unlink(missing_ok=True)
                self.finished.emit(True, "")
            else:
                log.error("Install failed: %s", r.stderr)
                self.finished.emit(False, r.stderr.strip().split("\n")[-1])

        except Exception as exc:
            log.error("Update install failed: %s", exc)
            self.finished.emit(False, str(exc))
