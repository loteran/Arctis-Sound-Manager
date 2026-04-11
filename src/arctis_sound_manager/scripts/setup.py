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
_DEVICE_DIR = Path.home() / ".config" / "arctis_manager" / "devices"
_HRIR_DIR = Path.home() / ".local" / "share" / "pipewire" / "hrir_hesuvi"
_HRIR_URL = "https://github.com/nicehash/HeSuVi/raw/master/hrir/44/KEMAR%20Gardner%201995/kemar.wav"


def _run_systemctl(args: list[str]) -> None:
    cmd = ["systemctl", "--user"] + args
    result = subprocess.run(cmd, capture_output=True, text=True)
    label = " ".join(args)
    if result.returncode == 0:
        print(f"  [ok] systemctl --user {label}")
    else:
        print(f"  [!] systemctl --user {label} failed: {result.stderr.strip()}")


def main() -> None:
    # ── PipeWire configs ──
    pw_src = _SHARE_DIR / "pipewire"
    if pw_src.is_dir():
        _PW_CONF_DIR.mkdir(parents=True, exist_ok=True)
        for conf in pw_src.glob("*.conf"):
            dst = _PW_CONF_DIR / conf.name
            if not dst.exists():
                shutil.copy2(conf, dst)
                print(f"  [ok] {conf.name} → {dst}")
            else:
                print(f"  [skip] {dst} already exists")
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
    if hrir_file.exists():
        print("  [ok] HRIR file already present — skipping download")
    else:
        _HRIR_DIR.mkdir(parents=True, exist_ok=True)
        print("  Downloading default HRIR file (KEMAR Gardner 1995)...")
        try:
            subprocess.run(
                ["curl", "-L", "-o", str(hrir_file), _HRIR_URL],
                check=True, timeout=60,
            )
            print(f"  [ok] HRIR downloaded → {hrir_file}")
        except (subprocess.CalledProcessError, FileNotFoundError):
            try:
                subprocess.run(
                    ["wget", "-O", str(hrir_file), _HRIR_URL],
                    check=True, timeout=60,
                )
                print(f"  [ok] HRIR downloaded → {hrir_file}")
            except (subprocess.CalledProcessError, FileNotFoundError):
                print("  [!] Could not download HRIR. Install curl or wget, then re-run asm-setup")

    # ── Remove stale filter-chain copy ──
    stale = Path.home() / ".config" / "pipewire" / "filter-chain.conf.d" / "sink-virtual-surround-7.1-hesuvi.conf"
    if stale.exists():
        stale.unlink()
        print(f"  [ok] Removed stale {stale}")

    asm_cli = shutil.which("asm-cli")

    # ── Desktop entry + systemd service file ──
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
    _run_systemctl(["daemon-reload"])
    _run_systemctl(["enable", "--now", "arctis-manager.service"])
    _run_systemctl(["enable", "--now", "arctis-video-router.service"])
    _run_systemctl(["enable", "--now", "filter-chain.service"])
    _run_systemctl(["restart", "pipewire", "pipewire-pulse"])

    print("\n==> Setup complete!")


if __name__ == "__main__":
    main()
