import functools
import shutil
from pathlib import Path
from typing import Literal


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
    user_conf = Path.home() / ".config" / "pipewire" / "filter-chain.conf"
    if user_conf.exists():
        return str(user_conf)
    return "/usr/share/pipewire/filter-chain.conf"
