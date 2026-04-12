"""
asm-setup — Post-install setup for Arctis Sound Manager.

Deploys PipeWire configs to user dir, downloads HRIR for virtual surround,
and copies device configs. Designed for AUR/system package installs where
files can't be written to $HOME during package().
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

_SHARE_DIR = Path("/usr/share/arctis-sound-manager")
_PW_CONF_DIR = Path.home() / ".config" / "pipewire" / "pipewire.conf.d"
_FC_CONF_DIR = Path.home() / ".config" / "pipewire" / "filter-chain.conf.d"
_DEVICE_DIR = Path.home() / ".config" / "arctis_manager" / "devices"
_HRIR_DIR = Path.home() / ".local" / "share" / "pipewire" / "hrir_hesuvi"
_HRIR_URL = "https://github.com/nicehash/HeSuVi/raw/master/hrir/44/KEMAR%20Gardner%201995/kemar.wav"

# Minimal filter-chain.service for distros that don't ship one (Fedora, Ubuntu…)
_FILTER_CHAIN_SERVICE = """\
[Unit]
Description=PipeWire filter-chain
Documentation=https://gitlab.freedesktop.org/pipewire/pipewire/-/wikis/Filter-Chain
After=pipewire.service
Requires=pipewire.service

[Service]
Type=simple
ExecStart=/usr/bin/pipewire -c filter-chain.conf
Restart=on-failure

[Install]
WantedBy=pipewire-session-manager.service
"""


def _run_systemctl(args: list[str]) -> None:
    cmd = ["systemctl", "--user"] + args
    result = subprocess.run(cmd, capture_output=True, text=True)
    label = " ".join(args)
    if result.returncode == 0:
        print(f"  [ok] systemctl --user {label}")
    else:
        print(f"  [!] systemctl --user {label} failed: {result.stderr.strip()}")


def _ensure_filter_chain_service() -> str:
    """Detect the filter-chain service name; install a bundled one if absent.

    Returns the service name to use (e.g. 'filter-chain.service').
    """
    for name in ("filter-chain.service", "pipewire-filter-chain.service"):
        result = subprocess.run(
            ["systemctl", "--user", "list-unit-files", name],
            capture_output=True, text=True,
        )
        if name.split(".")[0] in result.stdout:
            return name

    # Not found on this distro — install our bundled copy
    dest = Path.home() / ".config" / "systemd" / "user" / "filter-chain.service"
    dest.parent.mkdir(parents=True, exist_ok=True)

    # Prefer the copy shipped in /usr/share (AUR/COPR/DEB packages install it there)
    bundled = _SHARE_DIR / "filter-chain.service"
    if bundled.exists():
        shutil.copy2(bundled, dest)
    else:
        dest.write_text(_FILTER_CHAIN_SERVICE)

    print(f"  [ok] filter-chain.service installed → {dest}")
    _run_systemctl(["daemon-reload"])
    return "filter-chain.service"


def main() -> None:
    # ── PipeWire configs ──
    # 10-arctis-virtual-sinks.conf → pipewire.conf.d (loaded by pipewire itself)
    # sink-virtual-surround-7.1-hesuvi.conf → filter-chain.conf.d (loaded by filter-chain service)
    pw_src = _SHARE_DIR / "pipewire"
    if pw_src.is_dir():
        _PW_CONF_DIR.mkdir(parents=True, exist_ok=True)
        _FC_CONF_DIR.mkdir(parents=True, exist_ok=True)
        for conf in pw_src.glob("*.conf"):
            if "hesuvi" in conf.name or "surround" in conf.name:
                # HeSuVi surround → filter-chain.conf.d (avoids duplicate-node conflict)
                dst = _FC_CONF_DIR / conf.name
            else:
                dst = _PW_CONF_DIR / conf.name
            if not dst.exists():
                shutil.copy2(conf, dst)
                print(f"  [ok] {conf.name} → {dst}")
            else:
                print(f"  [skip] {dst} already exists")
        # Remove any stale copy of the surround config from pipewire.conf.d
        stale = _PW_CONF_DIR / "sink-virtual-surround-7.1-hesuvi.conf"
        if stale.exists():
            stale.unlink()
            print(f"  [ok] Removed stale surround config from pipewire.conf.d")
    else:
        print("  [info] No PipeWire configs found in /usr/share — using install.sh layout")

    # ── Device configs ──
    dev_src = _SHARE_DIR / "devices"
    if dev_src.is_dir():
        _DEVICE_DIR.mkdir(parents=True, exist_ok=True)
        for yaml in dev_src.glob("*.yaml"):
            dst = _DEVICE_DIR / yaml.name
            shutil.copy2(yaml, dst)
            print(f"  [ok] {yaml.name} → {dst}")
    else:
        print("  [info] No device configs found in /usr/share — using install.sh layout")

    # ── HRIR file ──
    hrir_file = _HRIR_DIR / "hrir.wav"
    if hrir_file.exists() and hrir_file.stat().st_size > 0:
        print("  [ok] HRIR file already present — skipping download")
    else:
        if hrir_file.exists():
            hrir_file.unlink()  # remove zero-size file from failed previous download
        _HRIR_DIR.mkdir(parents=True, exist_ok=True)
        print("  Downloading default HRIR file (KEMAR Gardner 1995)...")
        downloaded = False
        for tool, cmd in [
            ("curl", ["curl", "-L", "-o", str(hrir_file), _HRIR_URL]),
            ("wget", ["wget", "-O", str(hrir_file), _HRIR_URL]),
        ]:
            try:
                subprocess.run(cmd, check=True, timeout=60)
                if hrir_file.exists() and hrir_file.stat().st_size > 0:
                    print(f"  [ok] HRIR downloaded → {hrir_file}")
                    downloaded = True
                    break
                else:
                    hrir_file.unlink(missing_ok=True)
            except (subprocess.CalledProcessError, FileNotFoundError):
                hrir_file.unlink(missing_ok=True)
        if not downloaded:
            print("  [!] Could not download HRIR. Install curl or wget, then re-run asm-setup")
            print(f"      Or download manually: {_HRIR_URL}")
            print(f"      Save as: {hrir_file}")

    asm_cli = shutil.which("asm-cli")

    # ── Desktop entry + systemd service file ──
    # Skip if a system-level desktop entry already exists (AUR/COPR/DEB packages install one)
    system_desktop = Path("/usr/share/applications/ArctisManager.desktop")
    if system_desktop.exists():
        print("\n==> System desktop entry found — skipping asm-cli desktop write")
    else:
        print("\n==> Writing desktop entry and service file...")
        if asm_cli:
            result = subprocess.run([asm_cli, "desktop", "write"], text=True)
            if result.returncode == 0:
                print("  [ok] desktop entry and service file written")
            else:
                print("  [!] desktop write failed — run manually: asm-cli desktop write")
        else:
            print("  [!] asm-cli not found — run manually: asm-cli desktop write")

    # ── Udev rules ──
    print("\n==> Installing udev rules...")
    if asm_cli:
        result = subprocess.run(
            [asm_cli, "udev", "write-rules", "--force", "--reload"],
            text=True,
        )
        if result.returncode == 0:
            print("  [ok] udev rules installed")
        else:
            print("  [!] udev rules failed — run manually: asm-cli udev write-rules --force --reload")
    else:
        print("  [!] asm-cli not found — run manually: asm-cli udev write-rules --force --reload")

    # ── Systemd services ──
    print("\n==> Enabling services...")
    fc_service = _ensure_filter_chain_service()
    _run_systemctl(["daemon-reload"])

    # Restart PipeWire first so it picks up the new pipewire.conf.d configs
    _run_systemctl(["restart", "pipewire", "pipewire-pulse"])

    _run_systemctl(["enable", "--now", "arctis-manager.service"])
    _run_systemctl(["enable", "--now", "arctis-video-router.service"])
    _run_systemctl(["enable", "--now", fc_service])

    # Mark setup as done — checked by /etc/xdg/autostart/asm-first-run.desktop
    flag = Path.home() / ".config" / "arctis_manager" / ".setup_done"
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.touch()

    print("\n==> Setup complete!")


if __name__ == "__main__":
    main()
