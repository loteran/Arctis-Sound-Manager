# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
system_deps_checker.py — Runtime self-healing dependency check.

Phase 2 of ~/Bureau/ASM_PLAN_DEPS_CHECK.md. Issue #23 showed that any
silently-missing system dep (a LADSPA plugin in that case) can break a
whole feature with no in-app hint. The packaging mandate (Phase 1) is
that ASM declares every dep as a hard require, but that doesn't help
users who:

  * disabled `install_weak_deps` in DNF
  * wiped a package manually with `dnf remove --noautoremove`
  * are on an immutable distro (rpm-ostree, NixOS) that didn't replay
    the upgrade transaction
  * upgraded from a pre-`Requires:` ASM version where the dep was a
    no-op `Recommends:`

So at runtime we re-check everything from Phase 0 of the audit, compute
a per-distro install command for whatever's missing, and let the GUI
(Phase 4) or `asm-daemon --verify-setup` (Phase 3) act on the result.

Cross-distro coverage: Arch/CachyOS, Fedora/Nobara, Debian/Ubuntu (per
the project mandate in `feedback_crossdistro_fixes.md`).
"""
from __future__ import annotations

import ctypes
import importlib.util
import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)


class Severity(Enum):
    """How bad is it if this dep is missing.

    BLOCKING  — ASM cannot start, or a major feature is silently broken
                with no in-app way for the user to know why.
    DEGRADED  — ASM starts and most features work, but one feature is
                disabled (e.g. polling fallback for hotplug, OLED
                rendering for non-OLED devices).
    OPTIONAL  — Quality-of-life only (e.g. `gh` CLI for one-click bug
                report). Users without it get a perfectly usable app.
    """
    BLOCKING = "blocking"
    DEGRADED = "degraded"
    OPTIONAL = "optional"


class Scope(Enum):
    """Which environment a dependency belongs to.

    CONTAINER — the dependency is consumed by ASM's own processes inside the
                container (Python modules, pactl, pw-* tools). An immutable
                host does NOT block installing these — the container's
                package manager is writable even when the host's is not.
    HOST      — the dependency is consumed by a process on the HOST: udev
                rules (host's udevd), pkexec (host's polkit agent), LADSPA
                plugins (host's pipewire filter-chain). These CANNOT be
                installed from inside a container on an immutable host; the
                distrobox scripts handle them, or the user must run a command
                on the host directly.

    The default is CONTAINER so a new DepCheck added without thinking about
    scope keeps the current behaviour (install inside the container).
    """
    CONTAINER = "container"
    HOST = "host"


# Distro IDs we know how to install packages on. Anything else falls back
# to "copy the install command to the clipboard" mode in the GUI.
_KNOWN_DISTROS = {
    # dnf-based
    "fedora", "nobara", "rhel", "centos", "rocky", "almalinux",
    # apt-based
    "debian", "ubuntu", "linuxmint", "pop", "elementary", "neon",
    # pacman-based
    "arch", "cachyos", "endeavouros", "manjaro", "garuda", "artix",
}

_DNF_DISTROS = {"fedora", "nobara", "rhel", "centos", "rocky", "almalinux"}
_APT_DISTROS = {"debian", "ubuntu", "linuxmint", "pop", "elementary", "neon"}
_PACMAN_DISTROS = {"arch", "cachyos", "endeavouros", "manjaro", "garuda", "artix"}


@dataclass(frozen=True)
class DepCheck:
    """Static description of a single dep to verify."""
    name: str
    severity: Severity
    feature: str
    detect: Callable[[], bool]
    # distro id ("fedora" / "debian" / "arch" / ...) -> install argv
    # (without leading "pkexec" — the caller adds it).
    # Use "_internal" key when the fix is an ASM script, not a distro
    # package install (e.g. `asm-setup` to re-download the HRIR file).
    install_commands: dict[str, list[str]] = field(default_factory=dict)
    # distro id -> argv that removes the packages again, for the one feature
    # the user can uninstall (Clips). Deliberately not filled in for anything
    # else: the rest of these are what ASM needs to run, and offering to remove
    # them would be offering to break the app.
    #
    # The commands must never force. Every clip package is shared with the rest
    # of the desktop — ffmpeg and the GStreamer sets especially — so the right
    # outcome when something else depends on one is for the package manager to
    # refuse, not for ASM to override it.
    remove_commands: dict[str, list[str]] = field(default_factory=dict)
    # Extra step the user must take after the install command runs
    # (e.g. "log out and back in" for a group change).
    user_action: str | None = None
    # Scope: where the dependency is consumed. See the Scope enum for the rule.
    # Default is CONTAINER (writable even on an immutable host); HOST marks
    # deps consumed by host processes (udev rules, pkexec, LADSPA plugins
    # loaded by host's pipewire).
    scope: Scope = Scope.CONTAINER


@dataclass(frozen=True)
class CheckResult:
    check: DepCheck
    ok: bool
    detail: str = ""

    @property
    def name(self) -> str:
        return self.check.name


# ── Distro detection ──────────────────────────────────────────────────────────


def _read_os_release() -> dict[str, str]:
    """Parse /etc/os-release into a dict. Empty dict if file is missing."""
    out: dict[str, str] = {}
    path = Path("/etc/os-release")
    if not path.exists():
        return out
    try:
        for raw in path.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            out[key.strip()] = value.strip().strip('"').strip("'")
    except OSError as exc:
        log.warning("Could not parse /etc/os-release: %s", exc)
    return out


def detect_distro() -> str:
    """Return the canonical distro id (`fedora`, `arch`, `debian`, …) or `unknown`.

    Falls back to `ID_LIKE` when `ID` is not in the known set — that lets
    derivatives we've never seen (e.g. a fresh Fedora spin, or a new Ubuntu
    flavour) still get the right package manager.
    """
    info = _read_os_release()
    primary = info.get("ID", "").lower()
    if primary in _KNOWN_DISTROS:
        return primary
    for like in info.get("ID_LIKE", "").lower().split():
        if like in _KNOWN_DISTROS:
            return like
    return "unknown"


def _package_manager_for(distro: str) -> str | None:
    """Return `dnf` / `apt` / `pacman` for known distros, else None."""
    if distro in _DNF_DISTROS:
        return "dnf"
    if distro in _APT_DISTROS:
        return "apt"
    if distro in _PACMAN_DISTROS:
        return "pacman"
    return None


def install_command_for(check: DepCheck) -> list[str] | None:
    """Build the argv to install the missing dep on the current distro.

    Returns the argv WITHOUT a leading `pkexec` so the caller (CLI vs GUI)
    can decide how to elevate. Returns None when:
      * the distro is unknown, or
      * the dep has no install_commands entry for that distro.

    For internal fixes (re-run a script, no package install), returns the
    `_internal` entry verbatim — caller checks `argv[0]` to distinguish.
    """
    if not check.install_commands:
        return None
    distro = detect_distro()
    if distro in check.install_commands:
        return list(check.install_commands[distro])
    pkg_mgr = _package_manager_for(distro)
    # All sibling distros under the same pkg mgr share the same argv
    if pkg_mgr:
        for known in check.install_commands:
            if _package_manager_for(known) == pkg_mgr:
                return list(check.install_commands[known])
    if "_internal" in check.install_commands:
        return list(check.install_commands["_internal"])
    return None


def remove_command_for(check: DepCheck) -> list[str] | None:
    """Build the argv to remove a dep again, for the checks that allow it.

    Same distro resolution as `install_command_for`, and the same contract:
    argv without a leading `pkexec`. Returns None for every check that has no
    `remove_commands`, which is all of them except the Clips group — nothing
    else here is optional, so nothing else is removable.
    """
    if not check.remove_commands:
        return None
    distro = detect_distro()
    if distro in check.remove_commands:
        return list(check.remove_commands[distro])
    pkg_mgr = _package_manager_for(distro)
    if pkg_mgr:
        for known in check.remove_commands:
            if _package_manager_for(known) == pkg_mgr:
                return list(check.remove_commands[known])
    return None


# ── Detection helpers ─────────────────────────────────────────────────────────

# LADSPA plugins live in arch-specific dirs; check both 32 and 64 bit paths
# plus Debian's multiarch lib dir to cover all three packagers.
_LADSPA_DIRS = (
    "/usr/lib64/ladspa",
    "/usr/lib/ladspa",
    "/usr/lib/x86_64-linux-gnu/ladspa",
)

# Cache for host LADSPA listing when running in a container. Populated once
# per check pass by _host_ladspa_files(), consumed by all LADSPA detect calls.
_host_ladspa_cache: set[str] | None = None


def _running_in_container() -> bool:
    """Delegates to container.running_in_container() for consistency."""
    try:
        from arctis_sound_manager.container import running_in_container
        return running_in_container()
    except Exception:  # noqa: BLE001
        return False


def _host_exec_prefix() -> list[str] | None:
    """The argv prefix to run a command on the host, or None if unreachable."""
    try:
        from arctis_sound_manager.container import host_exec
        return host_exec()
    except Exception:  # noqa: BLE001
        return None


def _host_ladspa_files() -> set[str]:
    """List of .so filenames found in host LADSPA directories.

    When NOT in a container, returns an empty set (the local filesystem IS
    the host's, and _find_ladspa_plugin already scans it). When in a container,
    queries the host via distrobox-host-exec once and caches the result for
    the duration of the check pass.

    Only system directories are queried on the host; ~/.ladspa is bind-mounted
    into the container and already visible to the local scan.
    """
    global _host_ladspa_cache
    if not _running_in_container():
        return set()
    if _host_ladspa_cache is not None:
        return _host_ladspa_cache

    prefix = _host_exec_prefix()
    if not prefix:
        _host_ladspa_cache = set()
        return set()

    quoted_dirs = " ".join(repr(d) for d in _LADSPA_DIRS)
    script = (
        f"for d in {quoted_dirs}; do "
        "if [ -d \"$d\" ]; then "
        "find \"$d\" -maxdepth 1 -type f -name '*.so' -printf '%f\\n' 2>/dev/null; "
        "fi; "
        "done"
    )
    try:
        result = subprocess.run(
            [*prefix, "sh", "-c", script],
            capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        _host_ladspa_cache = set()
        return set()

    if result.returncode != 0:
        _host_ladspa_cache = set()
        return set()
    _host_ladspa_cache = {line.strip() for line in result.stdout.splitlines() if line.strip()}
    return _host_ladspa_cache


def _reset_host_ladspa_cache() -> None:
    """Clear the host LADSPA cache. Called at the start of each check pass."""
    global _host_ladspa_cache
    _host_ladspa_cache = None


def _ladspa_search_dirs() -> tuple[str, ...]:
    """Full LADSPA search path, honouring the same lookup that pipewire's
    module-filter-chain uses: the LADSPA_PATH env var and ~/.ladspa, then the
    standard system dirs. On read-only rootfs distros (SteamOS) the only place a
    user can add plugins is their HOME + LADSPA_PATH, so those must be searched
    or ASM would report a missing plugin the filter-chain can actually load."""
    dirs: list[str] = []
    for d in os.environ.get("LADSPA_PATH", "").split(os.pathsep):
        if d:
            dirs.append(d)
    dirs.append(str(Path.home() / ".ladspa"))
    dirs.extend(_LADSPA_DIRS)
    return tuple(dict.fromkeys(dirs))  # de-dup, preserve order


def _find_ladspa_plugin(name_pattern: str) -> str | None:
    """Return the absolute path of the first LADSPA .so matching `name_pattern`,
    or None if not found. `name_pattern` is a filename glob, e.g. `plate_1423.so`
    or `librnnoise*.so` (rnnoise has different basenames per build).

    In a container, the host's LADSPA plugins are what matters — the host's
    pipewire loads the filter-chain. This function checks the host's system
    dirs via distrobox-host-exec first, then falls back to the local scan
    (which covers ~/.ladspa, bind-mounted and shared between both sides).
    """
    import fnmatch

    # In a container, the host's system LADSPA dirs are authoritative.
    # A .so present only in the container and not on the host is a false OK.
    if _running_in_container():
        host_files = _host_ladspa_files()
        for fname in host_files:
            if fnmatch.fnmatch(fname, name_pattern):
                # The file exists on the host; report it as found. The exact
                # path is not needed for the boolean check.
                return f"(host:{fname})"

    # Local scan (covers ~/.ladspa which is shared, and is the only scan
    # when not in a container).
    for d in _ladspa_search_dirs():
        p = Path(d)
        if not p.is_dir():
            continue
        try:
            for entry in p.iterdir():
                if entry.is_file() and fnmatch.fnmatch(entry.name, name_pattern):
                    return str(entry)
        except OSError:
            continue
    return None


# ── DeepFilterNet LADSPA (opt-in second noise-cancel engine) ────────────────
#
# DeepFilterNet is a deep-learning noise suppressor that generally outperforms
# RNNoise across microphones. Its LADSPA plugin is NOT packaged by distros —
# it's a Rust plugin, built with cargo or downloaded as a prebuilt .so from the
# project's GitHub releases. ASM therefore auto-detects whatever is installed
# and, only when nothing is present, downloads a pinned build into ~/.ladspa.
_DEEPFILTER_GLOB = "libdeep_filter_ladspa*.so"
# Pinned fallback build (only used when the user has none installed). Detection
# is version-agnostic, so a NEWER version installed on the system afterwards is
# picked up and used automatically — the download is a last resort, not a pin.
_DEEPFILTER_VERSION = "0.5.6"
_DEEPFILTER_ARCH_ASSET = {
    "x86_64":  "x86_64-unknown-linux-gnu",
    "aarch64": "aarch64-unknown-linux-gnu",
    "armv7l":  "armv7-unknown-linux-gnueabihf",
    "armv8l":  "armv7-unknown-linux-gnueabihf",
}
_DEEPFILTER_VER_RE = re.compile(r"libdeep_filter_ladspa-(\d+)\.(\d+)\.(\d+)")


def _find_best_deepfilter_ladspa() -> str | None:
    """Absolute path of the best DeepFilterNet LADSPA .so across all search dirs.

    "Best" prefers the newest so a version the user installs later is used ahead
    of the pinned copy ASM may have downloaded: an unversioned filename (a user's
    own ``cargo`` build, e.g. ``libdeep_filter_ladspa.so``) wins outright, then
    the highest ``X.Y.Z`` parsed from a release asset name. Returns None when no
    DeepFilterNet plugin is present anywhere.
    """
    import fnmatch
    best: tuple[tuple[int, int, int, int], str] | None = None
    for d in _ladspa_search_dirs():
        p = Path(d)
        if not p.is_dir():
            continue
        try:
            entries = list(p.iterdir())
        except OSError:
            continue
        for entry in entries:
            if not (entry.is_file() and fnmatch.fnmatch(entry.name, _DEEPFILTER_GLOB)):
                continue
            m = _DEEPFILTER_VER_RE.search(entry.name)
            # Unversioned → (1,…) so a user build outranks any versioned asset;
            # versioned → (0, major, minor, patch) so the highest release wins.
            rank = (1, 0, 0, 0) if m is None else (0, int(m.group(1)), int(m.group(2)), int(m.group(3)))
            if best is None or rank > best[0]:
                best = (rank, str(entry))
    return best[1] if best else None


def _is_musl_libc() -> bool:
    """True on a musl system (Alpine …), where the glibc prebuilt won't load.

    The published LADSPA assets are all ``-unknown-linux-gnu`` (glibc). Loading a
    glibc .so on musl fails inside filter-chain (dlopen error → the #88 SEGV
    class), so the download must be refused there and the user pointed at a
    source build instead.
    """
    try:
        return any(Path("/lib").glob("ld-musl-*")) or any(Path("/usr/lib").glob("ld-musl-*"))
    except OSError:
        return False


def _deepfilter_asset() -> tuple[str, str] | None:
    """(download_url, asset_filename) of the pinned DeepFilterNet LADSPA build
    for this CPU arch, or None if no compatible published Linux asset exists
    (unknown arch, or a musl system the glibc build can't run on)."""
    import platform
    arch = _DEEPFILTER_ARCH_ASSET.get(platform.machine())
    if not arch or _is_musl_libc():
        return None
    name = f"libdeep_filter_ladspa-{_DEEPFILTER_VERSION}-{arch}.so"
    url = (f"https://github.com/Rikorose/DeepFilterNet/releases/download/"
           f"v{_DEEPFILTER_VERSION}/{name}")
    return url, name


def ensure_deepfilter_plugin(force_download: bool = False) -> str | None:
    """Return a usable DeepFilterNet LADSPA path, downloading the pinned build
    into ~/.ladspa if the user has none installed.

    A DeepFilterNet plugin already present anywhere (any version) is returned
    as-is and never re-downloaded, so a newer one the user installs keeps being
    used. The download is HTTPS from the project's pinned GitHub release, and
    the payload is validated as an ELF shared object of a sane size before it is
    put in place (there is no upstream per-asset checksum to verify against).

    Returns None when no plugin is present and the download can't be provided
    (unsupported arch, network failure, or a payload that fails validation) —
    the caller then keeps RNNoise / reports the engine as unavailable.
    """
    existing = _find_best_deepfilter_ladspa()
    if existing and not force_download:
        return existing

    asset = _deepfilter_asset()
    if asset is None:
        import platform
        log.warning("DeepFilterNet: no prebuilt LADSPA for arch %s — install it "
                    "manually (cargo build -p deep-filter-ladspa)", platform.machine())
        return existing
    url, name = asset

    dest_dir = Path.home() / ".ladspa"
    dest = dest_dir / name
    if dest.exists() and not force_download:
        try:
            if dest.stat().st_size > 100_000:
                return str(dest)
        except OSError:
            pass

    import urllib.request
    log.info("DeepFilterNet: downloading pinned LADSPA plugin %s", url)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "arctis-sound-manager"})
        with urllib.request.urlopen(req, timeout=120) as resp:  # nosec B310 — pinned https URL
            data = resp.read()
    except Exception as exc:  # network, TLS, HTTP error…
        log.warning("DeepFilterNet download failed (%s): %r", url, exc)
        return existing

    # Validate before trusting it: ELF magic + a floor on size (the real plugin
    # is several MB with the model baked in). Anything else is an error page or
    # a truncated download, not a plugin.
    if len(data) < 100_000 or data[:4] != b"\x7fELF":
        log.warning("DeepFilterNet download from %s is not a valid ELF .so "
                        "(%d bytes) — discarding", url, len(data))
        return existing

    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".tmp")
        tmp.write_bytes(data)
        tmp.chmod(0o644)
        os.replace(tmp, dest)
    except OSError as exc:
        log.warning("DeepFilterNet: could not save plugin to %s: %r", dest, exc)
        return existing

    log.info("DeepFilterNet: installed LADSPA plugin (%d bytes) at %s", len(data), dest)
    return str(dest)


def _can_import(module: str) -> bool:
    """importlib-only — never actually imports the module (avoids side effects
    from heavy modules like PySide6 that allocate Qt resources at import time)."""
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def _which(binary: str) -> bool:
    return shutil.which(binary) is not None


def _pip_user(pkg: str) -> list[str]:
    """Root-less install of a pure-Python module into the user site (~/.local).

    This is the self-heal path that actually works on an immutable distro
    (issue #175): SteamOS / Arch put the ASM package and its deps in the
    read-only rootfs, which a system update wipes and `pacman`/`paru` can't
    write back to. ``pip install --user`` targets ~/.local — writable, on the
    interpreter's path for both the GUI and the `systemctl --user` daemon, and
    surviving OS updates. No ``--break-system-packages``: Arch/SteamOS don't
    mark the environment externally-managed, so plain ``--user`` installs.
    Runs as the invoking user (never via pkexec) — see the deps dialog.
    """
    return ["python3", "-m", "pip", "install", "--user", pkg]
def _gst_elements(*elements: str) -> bool:
    """True when every named GStreamer element is registered.

    Asked through `gst-inspect-1.0` rather than by importing Gst, because this
    runs on machines where the whole point is that PyGObject is absent — and
    importing GStreamer to find out whether GStreamer is installed also costs a
    registry scan on every startup check. A missing gst-inspect is itself the
    answer: no GStreamer, no elements.
    """
    inspect = shutil.which("gst-inspect-1.0")
    if inspect is None:
        return False
    for element in elements:
        try:
            proc = subprocess.run([inspect, element], capture_output=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            return False
        if proc.returncode != 0:
            return False
    return True


def clips_enabled() -> bool:
    """Whether the user has switched Clips on in Settings.

    Read live rather than cached: the deps dialog is opened *by* the toggle, so
    it has to see the value the toggle just wrote. Any failure reads as off,
    which is the state that asks nothing of the user.
    """
    try:
        from arctis_sound_manager.settings import GeneralSettings
        return bool(GeneralSettings.read_from_file().clips_enabled)
    except Exception as exc:  # noqa: BLE001 — a settings problem must not break the checker
        log.debug("could not read clips_enabled, assuming off: %s", exc)
        return False


def _hrir_present() -> bool:
    p = Path.home() / ".local" / "share" / "pipewire" / "hrir_hesuvi" / "hrir.wav"
    try:
        return p.is_file() and p.stat().st_size > 0
    except OSError:
        return False


def _filter_chain_unit_available() -> bool:
    """Either the system ships filter-chain.service (Arch via pipewire-audio)
    or ASM bundled its fallback to `~/.config/systemd/user/`."""
    from arctis_sound_manager.init_system import detect_init, HOME_DINIT_SERVICE_FOLDER
    if detect_init() == "dinit":
        return (HOME_DINIT_SERVICE_FOLDER / "pipewire-filter-chain").exists()
    try:
        r = subprocess.run(
            ["systemctl", "--user", "list-unit-files", "filter-chain.service",
             "--no-legend"],
            capture_output=True, text=True, timeout=3,
        )
        return r.returncode == 0 and "filter-chain.service" in r.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _pipewire_running() -> bool:
    try:
        r = subprocess.run(["pactl", "info"], capture_output=True, text=True, timeout=2)
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _pipewire_version_ok(min_major: int = 1, min_minor: int = 0) -> bool:
    """Returns True if `pw-cli --version` reports >= (min_major, min_minor)."""
    try:
        r = subprocess.run(["pw-cli", "--version"], capture_output=True, text=True, timeout=2)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    if r.returncode != 0:
        return False
    # Output format: "pw-cli\nCompiled with libpipewire 1.2.7\nLinked with libpipewire 1.2.7"
    for line in r.stdout.splitlines():
        if "libpipewire" in line:
            parts = line.split()
            if len(parts) >= 1:
                version = parts[-1]
                bits = version.split(".")
                if len(bits) >= 2:
                    try:
                        major, minor = int(bits[0]), int(bits[1])
                        return (major, minor) >= (min_major, min_minor)
                    except ValueError:
                        continue
    return False


def _libusb_loadable() -> bool:
    try:
        ctypes.cdll.LoadLibrary("libusb-1.0.so.0")
        return True
    except OSError:
        return False


def _dbus_session_available() -> bool:
    if os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        return True
    return Path(f"/run/user/{os.getuid()}/bus").exists()


def _gh_authenticated() -> bool:
    if not _which("gh"):
        return False
    try:
        r = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True, timeout=3)
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _udev_rules_valid() -> bool:
    """Delegate to the existing udev_checker module (already battle-tested)."""
    try:
        from arctis_sound_manager.udev_checker import is_udev_rules_valid
        return bool(is_udev_rules_valid())
    except Exception as exc:
        log.warning("udev_checker call failed: %s", exc)
        return False


# ── The dep registry ──────────────────────────────────────────────────────────


def _pipewire_pulse_restart_cmd() -> list[str]:
    """Argv to restart the PipeWire/pulse daemon for the active init system.

    Returned verbatim as an ``_internal`` remediation and executed directly by
    the CLI/GUI caller (it is not routed through ``service_control``), so the
    command must match the init manager: ``systemctl --user`` accepts several
    units at once, dinit needs ``dinitctl`` (one service per call → use a shell
    chain so a single argv still restarts both).
    """
    from arctis_sound_manager.init_system import detect_init
    if detect_init() == "dinit":
        return ["sh", "-c", "dinitctl restart pipewire && dinitctl restart pipewire-pulse"]
    return ["systemctl", "--user", "restart", "pipewire", "pipewire-pulse"]


# The RNNoise LADSPA plugin is not packaged for Debian, Ubuntu or its
# derivatives, so on those distros we build it from source. Only the LADSPA target is built
# (VST/VST3/LV2/AU disabled) so no JUCE / X11 / freetype build deps are needed —
# just git, cmake and a C/C++ toolchain. Runs as root via pkexec; output goes to
# the standard LADSPA dir. Verified to produce build/bin/ladspa/librnnoise_ladspa.so.
_RNNOISE_LADSPA_SOURCE_BUILD: list[str] = [
    "bash", "-c",
    "set -e; "
    "apt-get install -y --no-install-recommends git cmake build-essential; "
    'tmp="$(mktemp -d)"; '
    'git clone --depth 1 https://github.com/werman/noise-suppression-for-voice.git "$tmp"; '
    'cmake -S "$tmp" -B "$tmp/build" '
    "-DBUILD_VST_PLUGIN=OFF -DBUILD_VST3_PLUGIN=OFF -DBUILD_LV2_PLUGIN=OFF "
    "-DBUILD_AU_PLUGIN=OFF -DBUILD_AUV3_PLUGIN=OFF -DBUILD_TESTS=OFF "
    "-DBUILD_LADSPA_PLUGIN=ON; "
    'cmake --build "$tmp/build" -j"$(nproc)"; '
    'install -Dm644 "$tmp/build/bin/ladspa/librnnoise_ladspa.so" '
    "/usr/lib/ladspa/librnnoise_ladspa.so; "
    'rm -rf "$tmp"',
]


def _build_checks() -> list[DepCheck]:
    """Single source of truth for every external dep ASM verifies.

    Phase 0 audit table (~/Bureau/ASM_PLAN_DEPS_CHECK.md) is the upstream
    spec — keep this list in sync. The drift-check CI (Phase 5) will
    flag any new external dep added to the codebase that's not here.
    """
    return [
        # Audio chain — BLOCKING because Spatial Audio + Sonar are the
        # core selling points and break silently without these.
        DepCheck(
            # plate_1423 (Spatial Audio reverb), sc4m_1916 (Smart Volume) and
            # gate_1410 (mic noise gate) all ship in the same swh-plugins
            # package. A referenced-but-missing .so can SEGV the whole
            # filter-chain (issue #88), so require all three, not just plate.
            #
            # HOST scope: the LADSPA plugin is loaded by the HOST's pipewire
            # filter-chain.service, not by any process inside the container.
            # When in a container the check must look at the host's LADSPA
            # directories, not the container's (a .so present only in the
            # container is a false OK because the host's pipewire won't find
            # it).
            name="LADSPA SWH plugins (plate_1423 / sc4m_1916 / gate_1410)",
            severity=Severity.BLOCKING,
            feature="Spatial Audio, Smart Volume, mic noise gate",
            detect=lambda: all(
                _find_ladspa_plugin(p) is not None
                for p in ("plate_1423.so", "sc4m_1916.so", "gate_1410.so")
            ),
            install_commands={
                "fedora": ["dnf", "install", "-y", "ladspa-swh-plugins"],
                "debian": ["apt-get", "install", "-y", "swh-plugins"],
                "arch":   ["pacman", "-S", "--noconfirm", "swh-plugins"],
            },
            scope=Scope.HOST,
        ),
        DepCheck(
            # Optional ClearCast mic noise suppression — the rest of ASM works
            # without it, so this is DEGRADED, not BLOCKING (issue #65).
            #
            # HOST scope: same reasoning as LADSPA SWH — the plugin is loaded
            # by the host's pipewire filter-chain.
            name="rnnoise LADSPA plugin",
            severity=Severity.DEGRADED,
            feature="ClearCast mic noise suppression",
            detect=lambda: _find_ladspa_plugin("librnnoise*.so") is not None,
            install_commands={
                # noise-suppression-for-voice is NOT in official Fedora repos;
                # it requires the lkiesow/noise-suppression-for-voice COPR.
                # The %post scriptlet already enables the COPR + triggers a
                # background install for RPM users. This command is the
                # fallback shown in the GUI for manual / pipx installs.
                "fedora": ["bash", "-c",
                           "dnf copr enable -y lkiesow/noise-suppression-for-voice"
                           " && dnf install -y ladspa-realtime-noise-suppression-plugin"],
                # noise-suppression-for-voice is NOT packaged for Debian *or*
                # Ubuntu / its derivatives (issues #65, #96), so all of them build
                # it from source (LADSPA target only).
                "debian":     _RNNOISE_LADSPA_SOURCE_BUILD,
                "ubuntu":     _RNNOISE_LADSPA_SOURCE_BUILD,
                "linuxmint":  _RNNOISE_LADSPA_SOURCE_BUILD,
                "pop":        _RNNOISE_LADSPA_SOURCE_BUILD,
                "elementary": _RNNOISE_LADSPA_SOURCE_BUILD,
                "neon":       _RNNOISE_LADSPA_SOURCE_BUILD,
                # rnnoise is in the Arch official repos (extra/).
                "arch":   ["pacman", "-S", "--noconfirm", "noise-suppression-for-voice"],
            },
            user_action=(
                "On Debian, Ubuntu and derivatives the plugin is not packaged, so Install "
                "builds it from source (downloads git/cmake/build-essential, compiles "
                "the LADSPA plugin only — takes a moment). Without it, only ClearCast "
                "mic noise suppression is unavailable."
            ),
            scope=Scope.HOST,
        ),
        DepCheck(
            # udev rules live on the HOST: udevd only ever reads the host's
            # /etc/udev/rules.d/. A distrobox container has its own /etc,
            # so checking the container would give a false 'ok' on an
            # otherwise good host install. Delegates to udev_checker which
            # already handles the cross-containment dance.
            name="udev rules",
            severity=Severity.BLOCKING,
            feature="non-root USB access",
            detect=_udev_rules_valid,
            install_commands={
                # asm-cli has the elevated write+reload helper.
                "_internal": ["asm-cli", "udev", "write-rules", "--force", "--reload"],
            },
            scope=Scope.HOST,
        ),
        DepCheck(
            name="HRIR file (EAC_Default.wav)",
            severity=Severity.BLOCKING,
            feature="Spatial Audio (HeSuVi convolution)",
            detect=_hrir_present,
            install_commands={
                # Re-run asm-setup; it re-downloads the HRIR with curl/wget.
                "_internal": ["asm-setup"],
            },
        ),
        DepCheck(
            name="filter-chain.service",
            severity=Severity.BLOCKING,
            feature="Sonar EQ + HeSuVi runtime",
            detect=_filter_chain_unit_available,
            install_commands={
                "_internal": ["asm-setup"],  # asm-setup installs the bundled fallback
            },
        ),

        # Audio runtime
        DepCheck(
            # The `pactl` CLI is called at GUI startup (default-sink save) and
            # for all EQ/Sonar sink-input routing. It ships in pulseaudio-utils
            # (libpulse on Arch), NOT in pipewire-pulse — so a clean install can
            # lack it and the GUI crashed on launch with FileNotFoundError (#117).
            name="pactl CLI (pulseaudio-utils)",
            severity=Severity.BLOCKING,
            feature="audio routing + default-sink save/restore",
            detect=lambda: shutil.which("pactl") is not None,
            install_commands={
                "fedora": ["dnf", "install", "-y", "pulseaudio-utils"],
                "debian": ["apt-get", "install", "-y", "pulseaudio-utils"],
                "arch":   ["pacman", "-S", "--noconfirm", "libpulse"],
            },
        ),
        DepCheck(
            # `pw-link` (used to detach stale loopback links) ships with the
            # PipeWire CLI tools — in the base `pipewire` package on Arch/Fedora,
            # in `pipewire-bin` on Debian/Ubuntu.
            name="pw-link (PipeWire CLI)",
            severity=Severity.DEGRADED,
            feature="loopback link cleanup",
            detect=lambda: _which("pw-link"),
            install_commands={
                "fedora": ["dnf", "install", "-y", "pipewire"],
                "debian": ["apt-get", "install", "-y", "pipewire-bin"],
                "arch":   ["pacman", "-S", "--noconfirm", "pipewire"],
            },
        ),
        DepCheck(
            # `pw-loopback` implements every Arctis virtual sink (Game/Chat/Media)
            # — without it ASM cannot create the channels at all. Ships with the
            # PipeWire CLI tools: base `pipewire` on Arch/Fedora, `pipewire-bin`
            # on Debian/Ubuntu.
            name="pw-loopback (PipeWire CLI)",
            severity=Severity.BLOCKING,
            feature="virtual Game/Chat/Media sinks",
            detect=lambda: _which("pw-loopback"),
            install_commands={
                "fedora": ["dnf", "install", "-y", "pipewire"],
                "debian": ["apt-get", "install", "-y", "pipewire-bin"],
                "arch":   ["pacman", "-S", "--noconfirm", "pipewire"],
            },
        ),
        DepCheck(
            # WirePlumber is the session manager ASM relies on for routing; its
            # binary is invoked for version detection and restarts.
            name="WirePlumber",
            severity=Severity.BLOCKING,
            feature="PipeWire session/policy management + routing",
            detect=lambda: _which("wireplumber"),
            install_commands={
                "fedora": ["dnf", "install", "-y", "wireplumber"],
                "debian": ["apt-get", "install", "-y", "wireplumber"],
                "arch":   ["pacman", "-S", "--noconfirm", "wireplumber"],
            },
        ),
        DepCheck(
            name="pipewire-pulse running",
            severity=Severity.BLOCKING,
            feature="all audio control (pulsectl)",
            detect=_pipewire_running,
            install_commands={
                # Not a package install — the daemon is just down. The GUI
                # surfaces a "Start pipewire-pulse" button that runs this argv
                # verbatim, so it must be correct for the active init system
                # (dinit takes one service per `dinitctl` call, not `systemctl`).
                # NOTE: this argv is executed directly by the CLI/GUI caller, not
                # routed through service_control; we pick the right command per
                # init here instead.
                "_internal": _pipewire_pulse_restart_cmd(),
            },
        ),
        DepCheck(
            name="PipeWire >= 1.0",
            severity=Severity.BLOCKING,
            feature="virtual sinks + filter-chain modules",
            detect=_pipewire_version_ok,
            install_commands={
                "fedora": ["dnf", "install", "-y", "pipewire"],
                "debian": ["apt-get", "install", "-y", "pipewire"],
                "arch":   ["pacman", "-S", "--noconfirm", "pipewire"],
            },
        ),
        DepCheck(
            name="wpctl (wireplumber)",
            severity=Severity.BLOCKING,
            feature="volume control + sink switching",
            detect=lambda: _which("wpctl"),
            install_commands={
                "fedora": ["dnf", "install", "-y", "wireplumber"],
                "debian": ["apt-get", "install", "-y", "wireplumber"],
                "arch":   ["pacman", "-S", "--noconfirm", "wireplumber"],
            },
        ),
        DepCheck(
            name="pw-dump (pipewire CLI)",
            severity=Severity.DEGRADED,
            feature="orphan stream detection in router",
            detect=lambda: _which("pw-dump"),
            install_commands={
                "fedora": ["dnf", "install", "-y", "pipewire"],
                "debian": ["apt-get", "install", "-y", "pipewire"],
                "arch":   ["pacman", "-S", "--noconfirm", "pipewire"],
            },
        ),
        DepCheck(
            # Only feeds the xrun self-diagnostics (#183): without it ASM cannot
            # notice the surround chain dropping frames and stays quiet, which
            # is the same as before the feature existed. Everything else keeps
            # working, hence OPTIONAL rather than DEGRADED — nothing the user
            # asked for stops functioning.
            name="pw-top (pipewire CLI)",
            severity=Severity.OPTIONAL,
            feature="audio glitch (xrun) detection on the surround chain",
            detect=lambda: _which("pw-top"),
            install_commands={
                "fedora": ["dnf", "install", "-y", "pipewire"],
                "debian": ["apt-get", "install", "-y", "pipewire"],
                "arch":   ["pacman", "-S", "--noconfirm", "pipewire"],
            },
        ),

        # USB stack
        DepCheck(
            name="libusb-1.0",
            severity=Severity.BLOCKING,
            feature="USB device control (HID commands)",
            detect=_libusb_loadable,
            install_commands={
                "fedora": ["dnf", "install", "-y", "libusb1"],
                "debian": ["apt-get", "install", "-y", "libusb-1.0-0"],
                "arch":   ["pacman", "-S", "--noconfirm", "libusb"],
            },
        ),
        # Note: "udev rules" is defined earlier (near HRIR) because it was
        # moved to Scope.HOST — keep it in alphabetical-ish order within
        # its group for readability. See the comment on that entry above.
        # It was originally positioned here (after pyusb) in the "USB stack"
        # group but needed the HOST annotation and a cross-module import.

        # Python deps that the wheel's own metadata covers but a manual
        # `dnf remove --noautoremove python3-pyudev` can still strip.
        DepCheck(
            name="babel (python module)",
            severity=Severity.BLOCKING,
            feature="UI translations (i18n)",
            detect=lambda: _can_import("babel"),
            install_commands={
                "fedora": ["dnf", "install", "-y", "python3-babel"],
                "debian": ["apt-get", "install", "-y", "python3-babel"],
                "arch":   ["pacman", "-S", "--noconfirm", "python-babel"],
            },
        ),
        DepCheck(
            name="pyudev (python module)",
            severity=Severity.DEGRADED,
            feature="USB hotplug (event-driven)",
            detect=lambda: _can_import("pyudev"),
            install_commands={
                "fedora": ["dnf", "install", "-y", "python3-pyudev"],
                "debian": ["apt-get", "install", "-y", "python3-pyudev"],
                "arch":   ["pacman", "-S", "--noconfirm", "python-pyudev"],
            },
        ),
        DepCheck(
            name="pulsectl (python module)",
            severity=Severity.BLOCKING,
            feature="audio control (sinks, streams, volumes)",
            detect=lambda: _can_import("pulsectl"),
            install_commands={
                "fedora": ["dnf", "install", "-y", "python3-pulsectl"],
                "debian": ["apt-get", "install", "-y", "python3-pulsectl"],
                # Arch has no pacman-native pkg, and on immutable SteamOS pacman
                # can't write the rootfs anyway (#175) — install into ~/.local.
                "arch":   _pip_user("pulsectl"),
            },
        ),
        DepCheck(
            name="dbus-next (python module)",
            severity=Severity.BLOCKING,
            feature="settings D-Bus service + GUI ↔ daemon comms",
            detect=lambda: _can_import("dbus_next"),
            install_commands={
                # Not in Fedora/Arch official repos — the RPM ships a bundled
                # wheel (dnf reinstall rewrites it); on Arch/SteamOS install the
                # module into ~/.local rather than reinstalling ASM (#175).
                "fedora": ["dnf", "reinstall", "-y", "arctis-sound-manager"],
                "debian": ["apt-get", "install", "-y", "python3-dbus-next"],
                "arch":   _pip_user("dbus-next"),
            },
        ),
        DepCheck(
            name="ruamel.yaml (python module)",
            severity=Severity.BLOCKING,
            feature="device YAML configs",
            detect=lambda: _can_import("ruamel.yaml"),
            install_commands={
                "fedora": ["dnf", "install", "-y", "python3-ruamel-yaml"],
                "debian": ["apt-get", "install", "-y", "python3-ruamel.yaml"],
                "arch":   _pip_user("ruamel.yaml"),
            },
        ),
        DepCheck(
            name="PySide6 (python module)",
            severity=Severity.BLOCKING,
            feature="entire GUI",
            detect=lambda: _can_import("PySide6"),
            install_commands={
                "fedora": ["dnf", "install", "-y", "python3-pyside6"],
                # Debian splits PySide6 into per-Qt-module packages — the
                # debian/control file lists each one with a `python3-pip`
                # fallback. Installing the umbrella is enough for the import
                # to succeed.
                "debian": ["apt-get", "install", "-y",
                           "python3-pyside6.qtcore",
                           "python3-pyside6.qtgui",
                           "python3-pyside6.qtwidgets",
                           "python3-pyside6.qtsvg",
                           "python3-pyside6.qtnetwork"],
                "arch":   ["pacman", "-S", "--noconfirm", "pyside6"],
            },
        ),
        DepCheck(
            name="pyusb (python module)",
            severity=Severity.BLOCKING,
            feature="USB device control (HID commands)",
            detect=lambda: _can_import("usb"),
            install_commands={
                "fedora": ["dnf", "install", "-y", "python3-pyusb"],
                "debian": ["apt-get", "install", "-y", "python3-usb"],
                "arch":   _pip_user("pyusb"),
            },
        ),
        DepCheck(
            name="pw-metadata",
            severity=Severity.DEGRADED,
            feature="EQ profile metadata + default-sink switching",
            detect=lambda: _which("pw-metadata"),
            install_commands={
                "fedora": ["dnf", "install", "-y", "pipewire"],
                "debian": ["apt-get", "install", "-y", "pipewire"],
                "arch":   ["pacman", "-S", "--noconfirm", "pipewire"],
            },
        ),
        DepCheck(
            name="dbus-send",
            severity=Severity.DEGRADED,
            feature="bug-report dialog D-Bus diagnostic dump",
            detect=lambda: _which("dbus-send"),
            install_commands={
                "fedora": ["dnf", "install", "-y", "dbus-tools"],
                "debian": ["apt-get", "install", "-y", "dbus-bin"],
                "arch":   ["pacman", "-S", "--noconfirm", "dbus"],
            },
        ),
        DepCheck(
            name="PIL / Pillow (python module)",
            severity=Severity.DEGRADED,
            feature="GameDAC OLED rendering",
            detect=lambda: _can_import("PIL"),
            install_commands={
                "fedora": ["dnf", "install", "-y", "python3-pillow"],
                "debian": ["apt-get", "install", "-y", "python3-pil"],
                "arch":   _pip_user("pillow"),
            },
        ),

        # Privilege escalation + session
        DepCheck(
            # pkexec is the POLKIT agent that elevates installs — it runs on
            # the HOST, not inside the container. Inside a container `which
            # pkexec` may succeed (polkit is often pulled as a dependency)
            # but the container's pkexec cannot elevate on the host. The
            # real authority is the host's pkexec.
            name="pkexec (polkit)",
            severity=Severity.BLOCKING,
            feature="install missing system packages from the GUI",
            detect=lambda: _which("pkexec"),
            install_commands={
                "fedora": ["dnf", "install", "-y", "polkit"],
                "debian": ["apt-get", "install", "-y", "policykit-1"],
                "arch":   ["pacman", "-S", "--noconfirm", "polkit"],
            },
            scope=Scope.HOST,
        ),
        DepCheck(
            name="D-Bus session bus",
            severity=Severity.BLOCKING,
            feature="all GUI ↔ daemon comms",
            detect=_dbus_session_available,
            # No package install fixes this — the user must log into a
            # graphical session. The dialog explains rather than offering
            # an Install button.
            install_commands={},
            user_action="Log into a graphical session (KDE / GNOME / sway / …) so /run/user/$UID/bus exists.",
        ),
        DepCheck(
            name="HRIR downloader (curl or wget)",
            severity=Severity.DEGRADED,
            feature="re-downloading the HRIR file via asm-setup",
            detect=lambda: _which("curl") or _which("wget"),
            install_commands={
                "fedora": ["dnf", "install", "-y", "curl"],
                "debian": ["apt-get", "install", "-y", "curl"],
                "arch":   ["pacman", "-S", "--noconfirm", "curl"],
            },
        ),

        DepCheck(
            name="pgrep (procps)",
            severity=Severity.DEGRADED,
            feature="detecting running asm-daemon processes in asm-setup",
            detect=lambda: _which("pgrep"),
            install_commands={
                "fedora": ["dnf", "install", "-y", "procps-ng"],
                "debian": ["apt-get", "install", "-y", "procps"],
                "arch":   ["pacman", "-S", "--noconfirm", "procps-ng"],
            },
        ),

        # Optional QoL
        DepCheck(
            name="gh CLI (authenticated)",
            severity=Severity.OPTIONAL,
            feature="one-click bug-report auto-submit",
            detect=_gh_authenticated,
            install_commands={
                "fedora": ["dnf", "install", "-y", "gh"],
                "debian": ["apt-get", "install", "-y", "gh"],
                "arch":   ["pacman", "-S", "--noconfirm", "github-cli"],
            },
            user_action="After install, run `gh auth login` once.",
        ),
    ]


def clip_dep_checks() -> list[DepCheck]:
    """The packages Clips needs, which nothing else in ASM does.

    Kept apart from `_build_checks()` because they are the *only* deps tied to
    a feature the user opts into. A mixer-and-EQ install has no use for a
    screen recorder's encoders, and listing them unconditionally would report a
    perfectly healthy machine as incomplete — which is the whole reason Clips
    is off by default.

    Returned whatever the toggle says: the Settings toggle installs from this
    list *before* the feature is on, so it cannot be gated on the feature
    being on.

    The GStreamer plugin sets are grouped rather than listed one per row. A
    user reading the dialog is answering "can this machine record?", not
    auditing plugin packages, and grouping also means one `pacman` call with
    three packages instead of three password prompts.

    The pacman commands are plain `-S`, deliberately, even though `-S` against
    a database older than the mirrors is a partial upgrade and fails exactly
    here — the repository's `gst-plugin-pipewire` depends on an *exact* pipewire
    release, so the moment the installed pipewire and the repository's disagree,
    pacman refuses with "could not satisfy dependencies". The fix for that is
    `-Syu`, which upgrades the entire machine; turning on a screen recorder is
    not a good reason to do that behind one password prompt. So the button
    installs, and when the install fails this way the screen says what to run.
    See `clips_setup.system_upgrade_command`.
    """
    return [
        DepCheck(
            # The bindings the capture is written against. Nothing else in ASM
            # imports gi, which is why a base install can skip it entirely.
            name="PyGObject (gi)",
            severity=Severity.BLOCKING,
            feature="clip capture",
            detect=lambda: _can_import("gi"),
            install_commands={
                "fedora": ["dnf", "install", "-y", "python3-gobject"],
                "debian": ["apt-get", "install", "-y", "python3-gi"],
                "arch":   ["pacman", "-S", "--noconfirm", "python-gobject"],
            },
            remove_commands={
                "fedora": ["dnf", "remove", "-y", "python3-gobject"],
                "debian": ["apt-get", "remove", "-y", "python3-gi"],
                "arch":   ["pacman", "-Rs", "--noconfirm", "python-gobject"],
            },
        ),
        DepCheck(
            # pipewiresrc is what the screen portal hands its stream to. It
            # ships separately from the main GStreamer packages on every
            # distro, and it is the one most often missing on a desktop that
            # otherwise plays video fine.
            name="GStreamer: screen capture (pipewiresrc)",
            severity=Severity.BLOCKING,
            feature="clip capture",
            detect=lambda: _gst_elements("pipewiresrc"),
            install_commands={
                "fedora": ["dnf", "install", "-y", "pipewire-gstreamer"],
                "debian": ["apt-get", "install", "-y", "gstreamer1.0-pipewire"],
                "arch":   ["pacman", "-S", "--noconfirm", "gst-plugin-pipewire"],
            },
            remove_commands={
                "fedora": ["dnf", "remove", "-y", "pipewire-gstreamer"],
                "debian": ["apt-get", "remove", "-y", "gstreamer1.0-pipewire"],
                "arch":   ["pacman", "-Rs", "--noconfirm", "gst-plugin-pipewire"],
            },
        ),
        DepCheck(
            # One row for the encode/mux chain: appsrc and opusenc (base),
            # pulsesrc and matroskamux (good), x264enc (ugly). x264enc is the
            # fallback every machine without a hardware encoder lands on, so it
            # is required rather than optional despite living in "ugly".
            name="GStreamer: encoding and muxing",
            severity=Severity.BLOCKING,
            feature="clip capture",
            detect=lambda: _gst_elements("appsrc", "opusenc", "pulsesrc",
                                         "matroskamux", "x264enc"),
            install_commands={
                # -ugly-free is the Fedora repos' build; the patent-encumbered
                # half lives in RPM Fusion and holds nothing the capture uses.
                "fedora": ["dnf", "install", "-y", "gstreamer1-plugins-base",
                           "gstreamer1-plugins-good", "gstreamer1-plugins-ugly-free"],
                "debian": ["apt-get", "install", "-y", "gstreamer1.0-plugins-base",
                           "gstreamer1.0-plugins-good", "gstreamer1.0-plugins-ugly"],
                "arch":   ["pacman", "-S", "--noconfirm", "gst-plugins-base",
                           "gst-plugins-good", "gst-plugins-ugly"],
            },
            remove_commands={
                "fedora": ["dnf", "remove", "-y", "gstreamer1-plugins-ugly-free"],
                "debian": ["apt-get", "remove", "-y", "gstreamer1.0-plugins-ugly"],
                "arch":   ["pacman", "-Rs", "--noconfirm", "gst-plugins-ugly"],
            },
        ),
        DepCheck(
            # Only consulted where the desktop has no ScreenCast portal and
            # Clips falls back to capturing X11 directly (#214). Without them
            # the whole X screen is recorded instead of one monitor, which is
            # right on a single-monitor machine and merely wide on others —
            # so optional, not degraded.
            name="xrandr",
            severity=Severity.OPTIONAL,
            feature="clip capture on X11 (picks the monitor to record)",
            detect=lambda: _which("xrandr"),
            install_commands={
                "fedora": ["dnf", "install", "-y", "xrandr"],
                "debian": ["apt-get", "install", "-y", "x11-xserver-utils"],
                "arch":   ["pacman", "-S", "--noconfirm", "xorg-xrandr"],
            },
        ),
        DepCheck(
            # The portal remembers which output the user picked. With no
            # portal there is nobody to ask, so the monitor under the pointer
            # is used — and this is what reads the pointer. Without it the
            # primary monitor is recorded, which is a reasonable answer.
            name="xdotool",
            severity=Severity.OPTIONAL,
            feature="clip capture on X11 (records the monitor you are looking at)",
            detect=lambda: _which("xdotool"),
            install_commands={
                "fedora": ["dnf", "install", "-y", "xdotool"],
                "debian": ["apt-get", "install", "-y", "xdotool"],
                "arch":   ["pacman", "-S", "--noconfirm", "xdotool"],
            },
        ),
        DepCheck(
            # Used by the bug report to say which GStreamer elements exist,
            # which is how "Clips will not record here" gets answered without
            # a round trip. It ships with the GStreamer base package
            # everywhere, so its absence means a very unusual install.
            name="gst-inspect-1.0",
            severity=Severity.OPTIONAL,
            feature="bug reports (lists the GStreamer elements present)",
            detect=lambda: _which("gst-inspect-1.0"),
            install_commands={
                "fedora": ["dnf", "install", "-y", "gstreamer1"],
                "debian": ["apt-get", "install", "-y", "gstreamer1.0-tools"],
                "arch":   ["pacman", "-S", "--noconfirm", "gstreamer"],
            },
        ),
        DepCheck(
            # Everything a clip does *after* it has been captured runs through
            # ffmpeg: the poster frame on its card, the per-channel level scan
            # that says which tracks are empty, and the export itself. The
            # capture is GStreamer and keeps working without this, which is
            # exactly what makes it worth naming — the recording succeeds and
            # then nothing can be done with it.
            name="ffmpeg / ffprobe",
            severity=Severity.DEGRADED,
            feature="clip thumbnails, track levels and export",
            detect=lambda: _which("ffmpeg") and _which("ffprobe"),
            install_commands={
                # ffmpeg-free is the Fedora repos' build; the full ffmpeg needs
                # RPM Fusion, and nothing here uses a codec it leaves out.
                "fedora": ["dnf", "install", "-y", "ffmpeg-free"],
                "debian": ["apt-get", "install", "-y", "ffmpeg"],
                "arch":   ["pacman", "-S", "--noconfirm", "ffmpeg"],
            },
            remove_commands={
                "fedora": ["dnf", "remove", "-y", "ffmpeg-free"],
                "debian": ["apt-get", "remove", "-y", "ffmpeg"],
                "arch":   ["pacman", "-Rs", "--noconfirm", "ffmpeg"],
            },
        ),
        DepCheck(
            # Deleting a clip goes to the system trash first so a mis-click on a
            # grid of near-identical cards is recoverable. Without gio it still
            # deletes — permanently, and with no way back.
            name="gio (glib2)",
            severity=Severity.OPTIONAL,
            feature="deleting clips to the trash rather than permanently",
            detect=lambda: _which("gio"),
            install_commands={
                "fedora": ["dnf", "install", "-y", "glib2"],
                "debian": ["apt-get", "install", "-y", "libglib2.0-bin"],
                "arch":   ["pacman", "-S", "--noconfirm", "glib2"],
            },
        ),
        DepCheck(
            # Qt's own opener is tried first and usually wins; this is the
            # fallback for sessions where it cannot resolve a handler.
            name="xdg-open (xdg-utils)",
            severity=Severity.OPTIONAL,
            feature="opening a clip or its folder in another app",
            detect=lambda: _which("xdg-open"),
            install_commands={
                "fedora": ["dnf", "install", "-y", "xdg-utils"],
                "debian": ["apt-get", "install", "-y", "xdg-utils"],
                "arch":   ["pacman", "-S", "--noconfirm", "xdg-utils"],
            },
        ),
        DepCheck(
            # The shutter sound on a save. canberra plays the theme's own
            # sample — the one the user chose and other apps use for the same
            # event — and clip_feedback falls back to pw-play/paplay on a file
            # when it is absent, so this only ever costs the themed version.
            name="canberra-gtk-play",
            severity=Severity.OPTIONAL,
            feature="the themed shutter sound when a clip is saved",
            detect=lambda: _which("canberra-gtk-play"),
            install_commands={
                "fedora": ["dnf", "install", "-y", "libcanberra-gtk3"],
                "debian": ["apt-get", "install", "-y", "libcanberra-gtk3-module"],
                "arch":   ["pacman", "-S", "--noconfirm", "libcanberra"],
            },
        ),
    ]


def run_all_checks() -> list[CheckResult]:
    """Run every dep check and return one CheckResult per check.

    Cheap (~200 ms total on a normal install — most checks are file
    existence / `shutil.which`). Safe to call from GUI startup, the
    daemon's `--verify-setup`, or a CLI subcommand.

    The Clips deps join the list only once the feature is switched on. With
    Clips off they are not missing dependencies, they are packages this
    install has no use for, and reporting them would make a healthy machine
    look broken every time the startup check runs.
    """
    checks = _build_checks()
    if clips_enabled():
        checks += clip_dep_checks()

    results: list[CheckResult] = []
    for check in checks:
        try:
            ok = bool(check.detect())
        except Exception as exc:
            log.warning("Check %r raised %r — treating as failed", check.name, exc)
            ok = False
        results.append(CheckResult(check=check, ok=ok))
    return results


def failing(results: list[CheckResult],
            min_severity: Severity = Severity.DEGRADED) -> list[CheckResult]:
    """Return only the failed checks at or above `min_severity`.

    Default keeps BLOCKING + DEGRADED, drops OPTIONAL — that's the
    "show a dialog" threshold for the GUI (Phase 4) and the "exit non-zero"
    threshold for `asm-daemon --verify-setup` (Phase 3).
    """
    severity_order = {Severity.OPTIONAL: 0, Severity.DEGRADED: 1, Severity.BLOCKING: 2}
    floor = severity_order[min_severity]
    return [r for r in results if not r.ok and severity_order[r.check.severity] >= floor]
