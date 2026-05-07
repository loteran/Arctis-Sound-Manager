import functools
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
