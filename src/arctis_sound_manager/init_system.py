import functools
import os
import shutil
from pathlib import Path
from typing import Literal

XDG_AUTOSTART_DIR = Path.home() / ".config" / "autostart"
_XDG_GUI_AUTOSTART = XDG_AUTOSTART_DIR / "arctis-gui-autostart.desktop"


@functools.lru_cache(maxsize=None)
def detect_init() -> Literal["systemd", "dinit", "unknown"]:
    """Detect the running init system by reading /proc/1/comm."""
    try:
        comm = Path("/proc/1/comm").read_text().strip()
        if comm == "dinit":
            return "dinit"
        if comm == "systemd":
            return "systemd"
    except OSError:
        pass
    if shutil.which("dinitctl") and not shutil.which("systemctl"):
        return "dinit"
    if shutil.which("systemctl"):
        return "systemd"
    return "unknown"


HOME_DINIT_SERVICE_FOLDER = Path.home() / ".config" / "dinit.d"

FILTER_CHAIN_SERVICE_NAME: dict[str, str] = {
    "systemd": "filter-chain",
    "dinit": "pipewire-filter-chain",
}


def filter_chain_conf_path() -> str:
    """Return absolute path to filter-chain.conf for dinit service (no WorkingDirectory on dinit)."""
    candidates = [
        Path.home() / ".config" / "pipewire" / "filter-chain.conf",
        Path("/usr/share/pipewire/filter-chain.conf"),
        Path("/etc/pipewire/filter-chain.conf"),
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    # Default to user conf even if absent — asm-setup will create it
    return str(candidates[0])


_DINIT_SERVICE_DIRS = [
    HOME_DINIT_SERVICE_FOLDER,
    Path("/etc/dinit.d"),
    Path("/usr/lib/dinit.d"),
]


def write_xdg_autostart() -> None:
    """Write XDG autostart desktop file for asm-gui.

    dinit services run without $DISPLAY/$WAYLAND_DISPLAY; XDG autostart
    is the correct mechanism to launch GUI apps after login on any compositor.
    """
    asm_gui = shutil.which("asm-gui") or "/usr/bin/asm-gui"
    XDG_AUTOSTART_DIR.mkdir(parents=True, exist_ok=True)
    _XDG_GUI_AUTOSTART.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Arctis Sound Manager\n"
        f"Exec={asm_gui} --systray\n"
        "Hidden=false\n"
        "NoDisplay=false\n"
        "X-GNOME-Autostart-enabled=true\n"
    )


def remove_xdg_autostart() -> None:
    """Remove XDG autostart desktop file for asm-gui."""
    _XDG_GUI_AUTOSTART.unlink(missing_ok=True)


def is_xdg_autostart_enabled() -> bool:
    """Return True if the XDG autostart entry for asm-gui exists."""
    return _XDG_GUI_AUTOSTART.exists()


_XPROFILE_MARKER = "# arctis-sound-manager-autostart"
_XPROFILE_PATH = Path.home() / ".xprofile"


def _has_xdg_autostart_consumer() -> bool:
    """Return True if the current environment will actually launch XDG autostart entries.

    Full DEs (KDE, GNOME, XFCE, MATE, LXDE, LXQt, …) run an XDG autostart launcher
    as part of their session startup. Standalone tools like `dex` also qualify.
    Bare WMs (i3, openbox, XLibre, raw xinit) return False — the .desktop file
    written by write_xdg_autostart() would be ignored without an extra fallback.
    """
    xdg = (os.environ.get("XDG_CURRENT_DESKTOP") or "").lower()
    known_des = {
        "kde", "plasma", "gnome", "unity", "pantheon", "xfce",
        "mate", "lxde", "lxqt", "cinnamon", "budgie", "deepin",
    }
    for token in xdg.split(":"):
        if token.strip() in known_des:
            return True
    for tool in ("dex", "xdg-launch", "fyi"):
        if shutil.which(tool):
            return True
    return False


def write_xprofile_fallback() -> bool:
    """Append an `asm-gui --systray` launch line to ~/.xprofile when no XDG autostart
    consumer is present. Idempotent (guarded by _XPROFILE_MARKER).

    ~/.xprofile is sourced by xinit/startx and all major display managers
    (xdm, lightdm, sddm, gdm) before the WM starts, regardless of WM choice.
    This is the most portable fallback for bare X11 setups (i3/openbox/XLibre)
    on dinit-based distros like Artix. Returns True on success.
    """
    asm_gui = shutil.which("asm-gui") or "/usr/bin/asm-gui"
    line = f'{_XPROFILE_MARKER}\n[ -x "{asm_gui}" ] && "{asm_gui}" --systray &\n'
    try:
        if _XPROFILE_PATH.exists():
            text = _XPROFILE_PATH.read_text(errors="replace")
            if _XPROFILE_MARKER in text:
                return True  # idempotent
            sep = "" if text.endswith("\n") else "\n"
            _XPROFILE_PATH.write_text(text + sep + line)
        else:
            _XPROFILE_PATH.write_text("#!/bin/sh\n" + line)
        try:
            _XPROFILE_PATH.chmod(0o755)
        except OSError:
            pass
        return True
    except OSError:
        return False


def remove_xprofile_fallback() -> bool:
    """Remove our asm-gui block from ~/.xprofile. Idempotent. Returns True on success."""
    if not _XPROFILE_PATH.exists():
        return True
    try:
        lines = _XPROFILE_PATH.read_text(errors="replace").splitlines(keepends=True)
        out: list[str] = []
        skip_next = False
        for raw in lines:
            if skip_next:
                skip_next = False
                continue
            if raw.rstrip("\n") == _XPROFILE_MARKER:
                skip_next = True
                continue
            out.append(raw)
        _XPROFILE_PATH.write_text("".join(out))
        return True
    except OSError:
        return False


def is_xprofile_fallback_active() -> bool:
    """Return True if our marker is present in ~/.xprofile."""
    if not _XPROFILE_PATH.exists():
        return False
    try:
        return _XPROFILE_MARKER in _XPROFILE_PATH.read_text(errors="replace")
    except OSError:
        return False


def is_dinit_service_enabled(svc: str) -> bool:
    """Return True if a waits-for.d/<svc> symlink exists in any dinit service directory.

    dinit has no 'is-enabled' subcommand (verified against upstream dinitctl.cc).
    Enabling a service creates a symlink in the parent service's waits-for.d directory;
    this function walks all known dinit service dirs to detect that symlink.
    """
    for base in _DINIT_SERVICE_DIRS:
        if not base.is_dir():
            continue
        for wfd in base.glob("*.waits-for.d"):
            if (wfd / svc).exists():
                return True
        if (base / "boot.d" / svc).exists():
            return True
    return False
