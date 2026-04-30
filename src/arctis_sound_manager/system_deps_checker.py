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
    # Extra step the user must take after the install command runs
    # (e.g. "log out and back in" for a group change).
    user_action: str | None = None


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


# ── Detection helpers ─────────────────────────────────────────────────────────

# LADSPA plugins live in arch-specific dirs; check both 32 and 64 bit paths
# plus Debian's multiarch lib dir to cover all three packagers.
_LADSPA_DIRS = (
    "/usr/lib64/ladspa",
    "/usr/lib/ladspa",
    "/usr/lib/x86_64-linux-gnu/ladspa",
)


def _find_ladspa_plugin(name_pattern: str) -> str | None:
    """Return the absolute path of the first LADSPA .so matching `name_pattern`,
    or None if not found. `name_pattern` is a filename glob, e.g. `plate_1423.so`
    or `librnnoise*.so` (rnnoise has different basenames per build)."""
    import fnmatch
    for d in _LADSPA_DIRS:
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


def _can_import(module: str) -> bool:
    """importlib-only — never actually imports the module (avoids side effects
    from heavy modules like PySide6 that allocate Qt resources at import time)."""
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def _which(binary: str) -> bool:
    return shutil.which(binary) is not None


def _hrir_present() -> bool:
    p = Path.home() / ".local" / "share" / "pipewire" / "hrir_hesuvi" / "EAC_Default.wav"
    try:
        return p.is_file() and p.stat().st_size > 0
    except OSError:
        return False


def _filter_chain_unit_available() -> bool:
    """Either the system ships filter-chain.service (Arch via pipewire-audio)
    or ASM bundled its fallback to `~/.config/systemd/user/`."""
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
            name="LADSPA SWH plugins (plate_1423)",
            severity=Severity.BLOCKING,
            feature="Spatial Audio (HeSuVi 7.1 surround)",
            detect=lambda: _find_ladspa_plugin("plate_1423.so") is not None,
            install_commands={
                "fedora": ["dnf", "install", "-y", "ladspa-swh-plugins"],
                "debian": ["apt-get", "install", "-y", "swh-plugins"],
                "arch":   ["pacman", "-S", "--noconfirm", "swh-plugins"],
            },
        ),
        DepCheck(
            name="rnnoise LADSPA plugin",
            severity=Severity.BLOCKING,
            feature="ClearCast mic noise suppression",
            detect=lambda: _find_ladspa_plugin("librnnoise*.so") is not None,
            install_commands={
                "fedora": ["dnf", "install", "-y", "noise-suppression-for-voice"],
                "debian": ["apt-get", "install", "-y", "noise-suppression-for-voice"],
                # rnnoise is AUR-only on Arch — the user needs an AUR helper.
                # We don't try to invoke yay/paru from here (they're not in
                # /usr/bin and require interactive build prompts); we just
                # surface the command and the GUI shows it as copy-only.
                "arch":   ["paru", "-S", "--noconfirm", "noise-suppression-for-voice"],
            },
            user_action="On Arch/CachyOS this lives in the AUR — install it with your AUR helper (paru/yay).",
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
            name="pipewire-pulse running",
            severity=Severity.BLOCKING,
            feature="all audio control (pulsectl)",
            detect=_pipewire_running,
            install_commands={
                # Not a package install — the daemon is just down. The GUI
                # surfaces a "Start pipewire-pulse" button that runs this.
                "_internal": ["systemctl", "--user", "restart", "pipewire", "pipewire-pulse"],
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
        DepCheck(
            name="udev rules",
            severity=Severity.BLOCKING,
            feature="non-root USB access",
            detect=_udev_rules_valid,
            install_commands={
                # asm-cli has the elevated write+reload helper.
                "_internal": ["asm-cli", "udev", "write-rules", "--force", "--reload"],
            },
        ),

        # Python deps that the wheel's own metadata covers but a manual
        # `dnf remove --noautoremove python3-pyudev` can still strip.
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
                # Arch has no pacman-native pkg — the AUR PKGBUILD bundles
                # it via uv pip --no-deps. Hint the user to reinstall ASM.
                "arch":   ["paru", "-S", "--noconfirm", "arctis-sound-manager"],
            },
        ),
        DepCheck(
            name="dbus-next (python module)",
            severity=Severity.BLOCKING,
            feature="settings D-Bus service + GUI ↔ daemon comms",
            detect=lambda: _can_import("dbus_next"),
            install_commands={
                # Not in Fedora/Arch official repos — the RPM/AUR ship a
                # bundled wheel. A `dnf reinstall` / `paru -S` rewrites it.
                "fedora": ["dnf", "reinstall", "-y", "arctis-sound-manager"],
                "debian": ["apt-get", "install", "-y", "python3-dbus-next"],
                "arch":   ["paru", "-S", "--noconfirm", "arctis-sound-manager"],
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
                "arch":   ["pacman", "-S", "--noconfirm", "python-ruamel-yaml"],
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
                "arch":   ["pacman", "-S", "--noconfirm", "python-pyusb"],
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
                "arch":   ["pacman", "-S", "--noconfirm", "python-pillow"],
            },
        ),

        # Privilege escalation + session
        DepCheck(
            name="pkexec (polkit)",
            severity=Severity.BLOCKING,
            feature="install missing system packages from the GUI",
            detect=lambda: _which("pkexec"),
            install_commands={
                "fedora": ["dnf", "install", "-y", "polkit"],
                "debian": ["apt-get", "install", "-y", "policykit-1"],
                "arch":   ["pacman", "-S", "--noconfirm", "polkit"],
            },
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


def run_all_checks() -> list[CheckResult]:
    """Run every dep check and return one CheckResult per check.

    Cheap (~200 ms total on a normal install — most checks are file
    existence / `shutil.which`). Safe to call from GUI startup, the
    daemon's `--verify-setup`, or a CLI subcommand.
    """
    results: list[CheckResult] = []
    for check in _build_checks():
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
