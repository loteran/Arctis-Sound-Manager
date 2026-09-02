# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Background update checker — queries GitHub releases API once per day."""
from __future__ import annotations

import json
import logging
import re
import shutil
import site
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


#: Queries that name the distro package owning an arbitrary file, per manager.
_OWNER_QUERIES: dict[InstallMethod, list[str]] = {
    InstallMethod.RPM:    ["rpm", "-qf", "--qf", "%{NAME}"],
    InstallMethod.PACMAN: ["pacman", "-Qoq"],
    InstallMethod.APT:    ["dpkg", "-S"],
}


def installed_package_name(method: InstallMethod) -> str | None:
    """Name of the distro package ASM is actually running from, or None.

    Not always "arctis-sound-manager": third-party repackagers pick their own
    name — Fedora's Terra ships it as `python3-arctis-sound-manager`. Looking
    up the package that owns this very module works whatever it is called,
    where a hardcoded name silently reports "not installed" and sends the user
    down the pip path for a system package (discussion #140).
    """
    query = _OWNER_QUERIES.get(method)
    if query is None:
        return None
    module_path = str(Path(__file__).resolve())
    try:
        r = subprocess.run(query + [module_path],
                           capture_output=True, text=True, timeout=5)
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    if r.returncode != 0 or not r.stdout.strip():
        return None

    out = r.stdout.strip().splitlines()[0]
    if method == InstallMethod.APT:
        # dpkg -S answers "package: /path"
        out = out.split(":", 1)[0]
    return out.strip() or None


def detect_all_install_methods() -> list[InstallMethod]:
    """Detect EVERY install method that currently has arctis-sound-manager installed.

    Returns a list (potentially with multiple entries) so callers can detect
    duplicate installations — the most common cause of stale-binary bugs after
    upgrades. Methods are returned in priority order (system packages first,
    then pipx); the empty list means nothing was detected.
    """
    found: list[InstallMethod] = []

    for cmd, args, method in (
        (["rpm", "-q", "arctis-sound-manager"],     [], InstallMethod.RPM),
        (["pacman", "-Q", "arctis-sound-manager"],  [], InstallMethod.PACMAN),
        (["dpkg", "-s", "arctis-sound-manager"],    [], InstallMethod.APT),
    ):
        try:
            r = subprocess.run(cmd + args, capture_output=True, text=True, timeout=5)
            if r.returncode == 0:
                found.append(method)
                continue
        except FileNotFoundError:
            continue
        # The package may carry another name — a third-party repackage, or a
        # distro convention. Ask which package owns this module instead.
        if installed_package_name(method):
            found.append(method)

    if shutil.which("pipx"):
        try:
            r = subprocess.run(
                ["pipx", "list", "--short"],
                capture_output=True, text=True, timeout=5,
            )
            if "arctis-sound-manager" in r.stdout:
                found.append(InstallMethod.PIPX)
        except FileNotFoundError:
            pass

    # Detect a pip --user install that shadows a system package manager install.
    # We check two complementary signals:
    #   1. The running arctis_sound_manager package lives under the user-site
    #      directory (~/.local/…/site-packages), which means a pip --user copy
    #      is loaded even when a system package is also present.
    #   2. There are multiple `asm-daemon` binaries on PATH (system + user-local).
    # Either signal alone is enough to flag a shadowing pip install.
    try:
        import arctis_sound_manager as _asm_pkg
        running_path = Path(_asm_pkg.__file__).resolve()

        # Signal 1 — running package lives under user site-packages
        _user_site = Path(site.getusersitepackages()).resolve()
        _pip_user_detected = running_path.is_relative_to(_user_site)

        # Signal 2 — multiple *distinct* asm-daemon binaries on PATH.
        # We must canonicalise each hit before counting: on usr-merged distros
        # (all modern Ubuntu/Fedora/Arch) /bin is a symlink to /usr/bin and both
        # are on PATH, so `command -v -a` lists the SAME physical binary twice
        # (/usr/bin/asm-daemon and /bin/asm-daemon). Counting raw lines flags a
        # phantom "second install" and blocks the update with a "Multiple ASM
        # installations detected" banner (issue #114). Resolve symlinks and
        # dedupe so only genuinely separate binaries count.
        if not _pip_user_detected:
            try:
                r2 = subprocess.run(
                    ["bash", "-c", "command -v -a asm-daemon 2>/dev/null"],
                    capture_output=True, text=True, timeout=5,
                )
                real_bins = {
                    str(Path(ln.strip()).resolve())
                    for ln in r2.stdout.splitlines()
                    if ln.strip()
                }
                _pip_user_detected = len(real_bins) > 1
            except Exception:
                pass

        if _pip_user_detected and InstallMethod.PIP not in found:
            found.append(InstallMethod.PIP)
    except Exception:
        pass

    return found


def detect_install_method() -> InstallMethod:
    """Backward-compat: return the first detected install method (or PIP fallback)."""
    methods = detect_all_install_methods()
    return methods[0] if methods else InstallMethod.PIP


#: Upgrade command per manager, with {pkg} filled in by package_manager_command().
_PACKAGE_MANAGER_TEMPLATES: dict[InstallMethod, str] = {
    # --refresh forces dnf to re-sync COPR metadata; without it a stale cache
    # can report "nothing to upgrade" even when a newer package exists.
    InstallMethod.RPM:    "sudo dnf upgrade --refresh {pkg} && asm-setup",
    InstallMethod.PACMAN: "paru -S {pkg} && asm-setup",
    InstallMethod.APT:    "sudo apt update && sudo apt upgrade {pkg} && asm-setup",
}


def _pacman_upgrade_command(pkg: str) -> str:
    """Upgrade command for an Arch-family install.

    ASM reaches Arch systems two ways, and they do not upgrade alike:

    - the signed binary repository (also what makes ASM visible to PackageKit,
      and therefore to Discover) — plain ``pacman`` upgrades it, no AUR helper
      involved, and the system's own updater picks it up unprompted;
    - the AUR package, which pacman cannot upgrade at all and which needs paru
      or yay to rebuild.

    Offering ``paru -S`` to someone who installed from the repository asks them
    to install an AUR helper for no reason — and offering ``pacman -Syu`` to an
    AUR user reports "nothing to do" forever. So ask pacman whether the package
    exists in a sync database: if it does, it is ours to upgrade.
    """
    try:
        r = subprocess.run(["pacman", "-Si", pkg],
                           capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            return f"sudo pacman -Syu {pkg} && asm-setup"
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        pass

    for helper in ("paru", "yay"):
        if shutil.which(helper):
            return f"{helper} -S {pkg} && asm-setup"
    return f"paru -S {pkg} && asm-setup"


def package_manager_command(method: InstallMethod) -> str | None:
    """Upgrade command naming the package actually installed.

    Upgrading a hardcoded "arctis-sound-manager" is a no-op when the installed
    package goes by another name: the manager reports success, nothing changes,
    and the app keeps offering the same update. Two users hit exactly that with
    Fedora's Terra build, which is named python3-arctis-sound-manager
    (discussion #140).
    """
    template = _PACKAGE_MANAGER_TEMPLATES.get(method)
    if template is None:
        return None
    pkg = installed_package_name(method) or "arctis-sound-manager"
    if method is InstallMethod.PACMAN:
        return _pacman_upgrade_command(pkg)
    return template.format(pkg=pkg)


#: Back-compat view for callers that only need the default package name.
PACKAGE_MANAGER_COMMANDS: dict[InstallMethod, str] = {
    method: template.format(pkg="arctis-sound-manager")
    for method, template in _PACKAGE_MANAGER_TEMPLATES.items()
}


# ── Hand-installed vs repository-tracked ──────────────────────────────────────
#
# The in-app update runs a package-manager upgrade command. That only works when
# the installed package is tracked by a repository the manager can pull a newer
# version from. Install ASM from a hand-downloaded .deb / .rpm / .pkg and no
# repository owns it — the upgrade command finds nothing, reports success, and
# the version never changes, so the update banner comes back forever (#163).
# These decide, per manager, whether the upgrade has a source at all, so the
# dialog can offer to add the repository (or download the release) instead of a
# command that quietly does nothing.

#: Command that adds ASM's own repository for a manager, so updates apply after.
_REPO_SETUP_COMMANDS: dict[InstallMethod, str] = {
    InstallMethod.APT:
        "sudo add-apt-repository ppa:loteran/arctis-sound-manager && "
        "sudo apt update && sudo apt install arctis-sound-manager",
    InstallMethod.RPM:
        "sudo dnf copr enable loteran/arctis-sound-manager && "
        "sudo dnf install arctis-sound-manager",
    # The signed pacman repository, exactly as the README documents it. This
    # is what Arch users are told to install from while the AUR is frozen.
    #
    # This used to pipe scripts/install.sh into bash, which could not work on
    # two counts: that script needs the repository checkout around it (it
    # copies PipeWire configs and device YAMLs out of it), and piping any
    # script into bash leaves BASH_SOURCE unset, which aborts it on the spot
    # under `set -u`. Arch and CachyOS are the largest install base ASM has,
    # and this is the command the update dialog handed every one of them.
    #
    # Idempotent on purpose: the repository block is appended only when it is
    # not already in pacman.conf, so running it twice cannot duplicate it.
    InstallMethod.PACMAN:
        "curl -fsSL https://github.com/loteran/Arctis-Sound-Manager/releases/"
        "download/pacman-repo/arctis-sound-manager.key -o /tmp/asm.key && "
        "sudo pacman-key --add /tmp/asm.key && "
        "sudo pacman-key --lsign-key \"$(gpg --show-keys --with-colons "
        "/tmp/asm.key | awk -F: '/^fpr/ {print $10; exit}')\" && "
        # Raw strings: the backslashes belong to grep and printf, not Python.
        r"(grep -q '^\[arctis-sound-manager\]' /etc/pacman.conf || "
        r"printf '\n[arctis-sound-manager]\nServer = https://github.com/"
        r"loteran/Arctis-Sound-Manager/releases/download/pacman-repo\n' | "
        "sudo tee -a /etc/pacman.conf >/dev/null) && "
        "sudo pacman -Sy arctis-sound-manager",
}


def repo_setup_command(method: InstallMethod) -> str | None:
    """The command that adds ASM's repository for *method* (or None for pip)."""
    return _REPO_SETUP_COMMANDS.get(method)


def _apt_repo_tracks(pkg: str) -> bool:
    """True when a configured APT repository — not just the local dpkg status —
    can supply *pkg*. A hand-installed .deb lists only `/var/lib/dpkg/status` as
    its source; a PPA/repo shows an `http(s)://` (or `file:/`) source line."""
    try:
        r = subprocess.run(["apt-cache", "policy", pkg],
                           capture_output=True, text=True, timeout=8)
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return True  # can't tell — never cry wolf
    if r.returncode != 0:
        return True
    return "://" in r.stdout


#: `from_repo` values that mean "not from a repository" across dnf4/dnf5.
_DNF_HAND_ORIGINS = {"@commandline", "@@commandline", "@system", "@local", "commandline", ""}


def _dnf_repo_tracks(pkg: str) -> bool:
    """True when *pkg* was installed from a real dnf repository (COPR), not from
    a hand-downloaded .rpm (whose origin reads `@commandline` / `@System`)."""
    try:
        r = subprocess.run(
            ["dnf", "repoquery", "--installed", "--queryformat", "%{from_repo}", pkg],
            capture_output=True, text=True, timeout=8)
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return True
    if r.returncode != 0:
        return True
    origins = {ln.strip() for ln in r.stdout.splitlines() if ln.strip()}
    if not origins:
        return True
    return any(o.lower() not in _DNF_HAND_ORIGINS for o in origins)


def upgrade_source_available(method: InstallMethod, pkg: str | None = None) -> bool:
    """Whether the manager can actually fetch a newer version for this install.

    False means ASM was installed from a package no repository tracks, so the
    upgrade command is a no-op — the caller should offer the repository-setup
    command (``repo_setup_command``) or a release download instead. Unknowable
    cases return True: a false "installed by hand" is worse than none.
    """
    pkg = pkg or installed_package_name(method) or "arctis-sound-manager"
    if method is InstallMethod.APT:
        return _apt_repo_tracks(pkg)
    if method is InstallMethod.RPM:
        return _dnf_repo_tracks(pkg)
    if method is InstallMethod.PACMAN:
        # A sync-db package is pacman's to upgrade; an AUR one a helper's. Either
        # is a real source; only "no sync entry and no helper" is a dead end.
        try:
            if subprocess.run(["pacman", "-Si", pkg], capture_output=True,
                              text=True, timeout=5).returncode == 0:
                return True
        except (FileNotFoundError, subprocess.SubprocessError, OSError):
            pass
        return any(shutil.which(h) for h in ("paru", "yay"))
    # pip / pipx / unknown: the upgrade path doesn't depend on a distro repo.
    return True

log = logging.getLogger(__name__)


# ── What the user's own repository can actually install ───────────────────────
#
# A release exists upstream hours before the repositories that ship it finish
# rebuilding — COPR lagged a release by roughly seven hours in discussion #140.
# Announcing GitHub's newest tag during that window offers an update the user's
# dnf cannot serve: they click "Update now", the upgrade command reports
# nothing to do, and the banner comes back. What the manager could install
# right now is the only answer that is actionable, so it is the one ASM asks
# for. GitHub stays the fallback for installs no distro repository tracks
# (pipx, pip) and for the AUR, whose PKGBUILD builds from the tag itself.
#
# Exception — pacman: the signed Arch repository is the GitHub pacman-repo
# asset, so a newer GitHub tag is always installable via ``pacman -Syu``
# (which refreshes the sync db on upgrade). The only lag is the local sync
# database, which can sit hours behind the asset. For pacman installs the
# checker takes the newer of the repo metadata and the GitHub tag. COPR/PPA
# are independent rebuilds and stay repo-only (#140).

_REPO_QUERY_TIMEOUT = 10

#: Managers that can be asked which version they would install.
_AVAILABLE_VERSION_QUERIES = (
    InstallMethod.RPM, InstallMethod.APT, InstallMethod.PACMAN,
)


def _normalize_pkg_version(raw: str) -> str:
    """Reduce a distro version string to the upstream version alone.

    Distro versions carry packaging metadata the upstream one does not: apt
    answers `1.4.12-1` and pacman `1.4.12-1`, either possibly behind an epoch
    (`1:1.4.12-1`). _parse_version only understands the bare `1.4.12` form and
    returns None for the rest, which would read as "cannot tell" forever.
    """
    raw = raw.strip()
    if ":" in raw:                      # strip an epoch prefix
        raw = raw.split(":", 1)[1]
    return raw.split("-", 1)[0].strip()


def _run_query(argv: list[str]) -> str | None:
    """Run a read-only manager query, or None if it cannot be answered."""
    try:
        r = subprocess.run(argv, capture_output=True, text=True,
                           timeout=_REPO_QUERY_TIMEOUT)
    except (FileNotFoundError, subprocess.SubprocessError, OSError) as exc:
        log.debug("repo version query failed (%s): %r", argv[0], exc)
        return None
    if r.returncode != 0:
        return None
    return r.stdout


def repo_available_version(method: InstallMethod, pkg: str | None = None) -> str | None:
    """The newest version *method* could install right now, or None.

    None means the question has no answer here — an unsupported install
    method, a manager that isn't present, a package no configured repository
    carries (a hand-installed .rpm, or an AUR build pacman knows nothing
    about). The caller must fall back rather than treat it as "no update":
    reporting nothing available because a query failed would hide real
    updates.

    Deliberately reads the metadata already on disk — no `--refresh`. This
    runs on every daily check, and forcing a full metadata re-sync would add
    seconds to it for a lag that resolves itself within hours anyway.
    """
    if method not in _AVAILABLE_VERSION_QUERIES:
        return None
    pkg = pkg or installed_package_name(method) or "arctis-sound-manager"

    if method is InstallMethod.RPM:
        out = _run_query(["dnf", "repoquery", "--quiet", "--available",
                          "--latest-limit", "1",
                          "--queryformat", "%{version}", pkg])
        if not out:
            return None
        line = out.strip().splitlines()[-1] if out.strip() else ""
        return _normalize_pkg_version(line) or None

    if method is InstallMethod.APT:
        out = _run_query(["apt-cache", "policy", pkg])
        if not out:
            return None
        for line in out.splitlines():
            key, sep, value = line.strip().partition(":")
            if sep and key.strip().lower() == "candidate":
                value = value.strip()
                # apt prints "(none)" for a package no repository supplies.
                if not value or value.startswith("("):
                    return None
                return _normalize_pkg_version(value) or None
        return None

    # pacman: only the sync databases answer. An AUR install has no entry, so
    # this returns None and the GitHub tag — which is what the PKGBUILD builds
    # from — takes over.
    out = _run_query(["pacman", "-Si", pkg])
    if not out:
        return None
    for line in out.splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip().lower() == "version":
            return _normalize_pkg_version(value) or None
    return None


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

    def __init__(self, current_version: str, force: bool = False):
        super().__init__()
        self._current = current_version
        self._force = force

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

        # The user's own repository decides. Only when it cannot answer — pipx,
        # pip, an AUR build, a hand-installed package — does the GitHub tag
        # stand in. See the block above repo_available_version().
        offered, url, wheel_url = self._repo_offer()
        if offered is None:
            offered, url, wheel_url = self._github_offer()
        elif InstallMethod.PACMAN in detect_all_install_methods():
            # pacman: the repo is the GitHub pacman-repo asset; a newer tag is
            # installable via "pacman -Syu" (which refreshes). Take the max.
            try:
                gh_version, gh_url, _ = self._github_offer()
                if gh_version and _version_gt(gh_version, offered):
                    offered, url = gh_version, gh_url
                    # wheel_url stays "" — pacman upgrades through package manager
            except Exception as exc:  # noqa: BLE001 — network failure, keep repo
                log.debug("GitHub fetch failed during pacman max: %r", exc)

        if not offered:
            self.result.emit("", "", "")
            return

        if _version_gt(offered, self._current):
            self.result.emit(offered, url, wheel_url)
        else:
            self.result.emit("", "", "")

    def _repo_offer(self) -> tuple[str | None, str, str]:
        """What the installed-from repository could give us, live.

        Not cached on purpose: the whole point is to follow the repository as
        it catches up, and a day-old answer would reinstate the lag this
        replaces. The queries read local metadata, and this runs off the GUI
        thread.

        No wheel URL: a repository-managed install upgrades through its
        manager (package_manager_command), never through the wheel installer.
        """
        try:
            methods = detect_all_install_methods()
        except Exception as exc:            # noqa: BLE001 — never break the check
            log.debug("install-method detection failed: %r", exc)
            return None, "", ""

        for method in methods:
            version = repo_available_version(method)
            if version and _parse_version(version):
                return version, f"https://github.com/{_REPO}/releases/tag/v{version}", ""
        return None, "", ""

    def _github_offer(self) -> tuple[str | None, str, str]:
        """The newest upstream tag, cached for a day as before."""
        latest_str, url, wheel_url = (None, "", "") if self._force else self._read_cache()
        if latest_str is None:
            latest_str, url, wheel_url = self._fetch()
            if latest_str:
                self._write_cache(latest_str, url, wheel_url)
        return latest_str, url, wheel_url

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


_TERMINAL_CANDIDATES: list[tuple[str, list[str]]] = [
    # (binary, args_before_cmd) — {} is replaced by the shell command string
    ("konsole",        ["-e", "bash", "-c"]),
    ("gnome-terminal", ["--", "bash", "-c"]),
    ("xfce4-terminal", ["--hold", "-x", "bash", "-c"]),
    ("mate-terminal",  ["--", "bash", "-c"]),
    ("xterm",          ["-e", "bash", "-c"]),
    ("kitty",          ["bash", "-c"]),
    ("alacritty",      ["-e", "bash", "-c"]),
    ("foot",           ["bash", "-c"]),
]

_TERMINALS_WITHOUT_HOLD = ("konsole", "xterm", "kitty", "alacritty", "foot")


def build_terminal_cmd(inner_cmd: str) -> list[str] | None:
    """Return a subprocess arg list that opens a terminal running *inner_cmd*.

    The terminal is left open after the command finishes so the user can read
    the output.  Returns None if no supported terminal emulator is found.
    """
    for binary, args in _TERMINAL_CANDIDATES:
        if shutil.which(binary):
            if binary in _TERMINALS_WITHOUT_HOLD:
                inner_cmd = (
                    f"{inner_cmd}; "
                    r'echo; read -rp "Press Enter to close…"'
                )
            return [binary] + args + [inner_cmd]
    return None


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
