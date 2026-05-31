# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
asm-setup — Post-install setup for Arctis Sound Manager.

Deploys PipeWire configs to user dir, downloads HRIR for virtual surround,
and copies device configs. Designed for AUR/system package installs where
files can't be written to $HOME during package().
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from arctis_sound_manager.init_system import (
    detect_init, HOME_DINIT_SERVICE_FOLDER, filter_chain_conf_path,
    is_dinit_service_enabled, write_xdg_autostart, remove_xdg_autostart,
    _has_xdg_autostart_consumer, write_xprofile_fallback,
)

_SHARE_DIR = Path("/usr/share/arctis-sound-manager")
_PW_CONF_DIR = Path.home() / ".config" / "pipewire" / "pipewire.conf.d"
_FC_CONF_DIR = Path.home() / ".config" / "pipewire" / "filter-chain.conf.d"
_DEVICE_DIR = Path.home() / ".config" / "arctis_manager" / "devices"
_HRIR_DIR = Path.home() / ".local" / "share" / "pipewire" / "hrir_hesuvi"
_HRIR_URL = "https://raw.githubusercontent.com/loteran/Arctis-Sound-Manager/main/hrir/EAC_Default.wav"

_DINIT_FILTER_CHAIN_TEMPLATE = """\
type = process
command = /usr/bin/pipewire -c {fc_conf}
restart = true
# Adjust depends-on if PipeWire has a different service name on your distro.
depends-on = pipewire
logfile = /tmp/pipewire-filter-chain.log
"""

_DINIT_ARCTIS_MANAGER = """\
type = process
command = {asm_daemon}
restart = true
# Adjust depends-on if PipeWire has a different service name on your distro.
depends-on = pipewire
logfile = /tmp/arctis-manager.log
"""

_DINIT_ARCTIS_VIDEO_ROUTER = """\
type = process
command = {asm_router}
restart = true
depends-on = arctis-manager
logfile = /tmp/arctis-video-router.log
"""

# Written to ~/.config/dinit.d/boot only when no boot service exists at all.
# Without a `boot` service that has `waits-for.d`, `dinitctl enable` fails with
# "service 'boot' has no waits-for.d directory specified" and services won't
# autostart on next login. This matches the upstream getting-started guide for
# dinit user-mode. Never overwritten if a boot file already exists.
_DINIT_USER_BOOT_TEMPLATE = """\
type = internal
waits-for.d = boot.d
"""


def _ensure_dinit_boot_target() -> None:
    """Create a minimal user `boot` service with waits-for.d when missing.

    Artix dinit-userservd does not ship a user boot service by default, so
    `dinitctl enable` fails silently. This creates the minimal file required
    by the upstream getting-started guide — only when absent, never overwriting.
    """
    user_boot = HOME_DINIT_SERVICE_FOLDER / "boot"
    boot_d = HOME_DINIT_SERVICE_FOLDER / "boot.d"
    if not user_boot.exists():
        user_boot.write_text(_DINIT_USER_BOOT_TEMPLATE)
        print(f"  [dinit] created minimal user boot service at {user_boot}")
    boot_d.mkdir(parents=True, exist_ok=True)


def _setup_dinit_services() -> None:
    """Install dinit user service files and start ASM services. Called on dinit systems only."""
    import time

    HOME_DINIT_SERVICE_FOLDER.mkdir(parents=True, exist_ok=True)

    asm_daemon = shutil.which("asm-daemon") or "/usr/bin/asm-daemon"
    asm_router = shutil.which("asm-router") or "/usr/bin/asm-router"

    (HOME_DINIT_SERVICE_FOLDER / "arctis-manager").write_text(
        _DINIT_ARCTIS_MANAGER.format(asm_daemon=asm_daemon))
    (HOME_DINIT_SERVICE_FOLDER / "arctis-video-router").write_text(
        _DINIT_ARCTIS_VIDEO_ROUTER.format(asm_router=asm_router))

    # filter-chain uses absolute path to filter-chain.conf (no WorkingDirectory in dinit)
    fc_conf = filter_chain_conf_path()
    (HOME_DINIT_SERVICE_FOLDER / "pipewire-filter-chain").write_text(
        _DINIT_FILTER_CHAIN_TEMPLATE.format(fc_conf=fc_conf))

    print("  [dinit] service files written to", HOME_DINIT_SERVICE_FOLDER)

    # Remove stale arctis-gui dinit service — replaced by XDG autostart
    old_gui_svc = HOME_DINIT_SERVICE_FOLDER / "arctis-gui"
    if old_gui_svc.exists():
        try:
            subprocess.run(["dinitctl", "disable", "arctis-gui"], check=False,
                           capture_output=True, timeout=10)
        except subprocess.TimeoutExpired:
            print("  [!] dinitctl disable arctis-gui timed out — continuing")
        try:
            subprocess.run(["dinitctl", "stop", "arctis-gui"], check=False,
                           capture_output=True, timeout=10)
        except subprocess.TimeoutExpired:
            print("  [!] dinitctl stop arctis-gui timed out — continuing")
        old_gui_svc.unlink()
        print("  [dinit] removed stale arctis-gui service (replaced by XDG autostart)")

    # Ensure a user `boot` service + boot.d/ exist so `dinitctl enable` can succeed
    _ensure_dinit_boot_target()

    # Reload stopped service definitions so dinit picks up restart=true from updated files.
    # Running services cannot be reloaded — skip them; they already have the right behaviour.
    for svc in ["arctis-video-router", "pipewire-filter-chain"]:
        try:
            st = subprocess.run(["dinitctl", "status", svc], capture_output=True, text=True,
                                timeout=10)
        except subprocess.TimeoutExpired:
            print(f"  [!] dinitctl status {svc} timed out — skipping reload")
            continue
        if "started" not in st.stdout.lower():
            try:
                subprocess.run(["dinitctl", "reload", svc], check=False, capture_output=True,
                               timeout=10)
            except subprocess.TimeoutExpired:
                print(f"  [!] dinitctl reload {svc} timed out — continuing")

    # Restart PipeWire best-effort (may fail if not a dinit service).
    # Sleep briefly after so the socket is ready before we start dependants.
    try:
        pw_restart = subprocess.run(["dinitctl", "restart", "pipewire"], check=False,
                                    capture_output=True, text=True, timeout=10)
    except subprocess.TimeoutExpired:
        print("  [!] dinitctl restart pipewire timed out — continuing")
        pw_restart = type("_FakeResult", (), {"returncode": 1})()
    if pw_restart.returncode == 0:
        time.sleep(0.5)

    # Guard: use pgrep against the actual process instead of dinitctl status to avoid a
    # timing race where the boot-sequence auto-start and this setup run overlap.
    try:
        am_already_running = subprocess.run(
            ["pgrep", "-f", "asm-daemon"], capture_output=True, timeout=10
        ).returncode == 0
    except subprocess.TimeoutExpired:
        print("  [!] pgrep asm-daemon timed out — assuming not running")
        am_already_running = False

    # Enable and start each service (use 'start' not 'restart' — idempotent if not yet running).
    # Guard every service individually to avoid double-launch (issue #25).
    for svc in ["arctis-manager", "arctis-video-router", "pipewire-filter-chain"]:
        try:
            en = subprocess.run(["dinitctl", "enable", svc], check=False,
                                capture_output=True, text=True, timeout=10)
        except subprocess.TimeoutExpired:
            print(f"  [!] dinitctl enable {svc} timed out — continuing")
            continue
        if en.returncode != 0:
            print(f"  [dinit] {svc}: enable failed ({en.stderr.strip() or 'unknown error'})")
        if svc == "arctis-manager" and am_already_running:
            print("  [dinit] arctis-manager: already running — skipping start")
            continue
        try:
            svc_st = subprocess.run(["dinitctl", "status", svc], capture_output=True, text=True,
                                    timeout=10)
        except subprocess.TimeoutExpired:
            print(f"  [!] dinitctl status {svc} timed out — skipping start")
            continue
        if svc_st.returncode == 0 and "started" in svc_st.stdout.lower():
            print(f"  [dinit] {svc}: already running — skipping start")
            continue
        try:
            st = subprocess.run(["dinitctl", "start", svc], check=False,
                                capture_output=True, text=True, timeout=10)
        except subprocess.TimeoutExpired:
            print(f"  [!] dinitctl start {svc} timed out — continuing")
            continue
        status = "ok" if st.returncode == 0 else f"start failed ({st.stderr.strip() or 'unknown error'})"
        print(f"  [dinit] {svc}: {status}")

    # GUI: use XDG autostart instead of a dinit service (dinit has no $DISPLAY/$WAYLAND_DISPLAY)
    write_xdg_autostart()
    print("  [dinit] arctis-gui: XDG autostart entry written (will launch on next login)")

    # Issue #25: bare WMs (i3, openbox, XLibre) on Artix do not run any XDG autostart
    # consumer, so the .desktop file above is inert. Fall back to ~/.xprofile, which is
    # sourced by xinit/startx and all major display managers before the WM starts.
    if not _has_xdg_autostart_consumer():
        if write_xprofile_fallback():
            print("  [dinit] no XDG autostart consumer detected — added asm-gui to ~/.xprofile")
            print("         (re-login or run `asm-gui --systray &` manually for this session)")
        else:
            print("  [!] no XDG autostart consumer AND could not write ~/.xprofile —"
                  " run `asm-gui --systray &` manually after login")


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


def _has_systemctl() -> bool:
    return shutil.which("systemctl") is not None


def _run_systemctl(args: list[str]) -> None:
    if not _has_systemctl():
        print(f"  [skip] systemctl not found (non-systemd init) — skipping: {' '.join(args)}")
        return
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
    if detect_init() == "dinit":
        svc_file = HOME_DINIT_SERVICE_FOLDER / "pipewire-filter-chain"
        if not svc_file.exists():
            svc_file.parent.mkdir(parents=True, exist_ok=True)
            svc_file.write_text(_DINIT_FILTER_CHAIN_TEMPLATE.format(fc_conf=filter_chain_conf_path()))
        return "pipewire-filter-chain"
    # --- existing systemd code follows unchanged ---
    if not _has_systemctl():
        print("  [skip] systemctl not found — skipping filter-chain service detection (non-systemd init)")
        return "filter-chain.service"

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


def _refuse_root() -> None:
    """asm-setup wires up the *user's* systemd, $HOME and pipewire — running it
    as root would either silently fail (`systemctl --user` outside a user
    session) or write into /root, neither of which is what the user wants.
    The package's post-install hook calls us via `su -l $REAL_USER`, so this
    branch only triggers when somebody manually runs `sudo asm-setup`."""
    if os.geteuid() == 0:
        sys.stderr.write(
            "asm-setup: refusing to run as root — this script configures the\n"
            "current user's PipeWire / systemd / $HOME, not the system as a\n"
            "whole. Re-run as the regular user that will use the headset:\n"
            "  asm-setup\n"
            "(or, from the package post-install hook, su -l $REAL_USER -c 'asm-setup').\n"
        )
        sys.exit(2)


_USER_SYSTEMD_DIR = Path.home() / ".config" / "systemd" / "user"
_GUI_SERVICE_NAME = "arctis-gui.service"


def _cleanup_stale_gui_service() -> None:
    """Remove a user-level arctis-gui.service whose ExecStart binary is gone.

    Pipx installs wrote ExecStart pointing to ~/.local/bin/asm-gui.  After
    migrating to a system package the binary no longer exists, so the service
    crashes in a restart loop and masks the correct system-level unit.
    """
    user_unit = _USER_SYSTEMD_DIR / _GUI_SERVICE_NAME
    if not user_unit.exists():
        return
    try:
        content = user_unit.read_text()
    except OSError:
        return
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped.startswith("ExecStart="):
            continue
        binary = stripped[len("ExecStart="):].split()[0]
        if not Path(binary).exists():
            try:
                user_unit.unlink()
                print(f"  [ok] Removed stale {_GUI_SERVICE_NAME} (ExecStart={binary} not found)")
            except OSError as e:
                print(f"  [!] Could not remove stale {_GUI_SERVICE_NAME}: {e}")
        break


def main() -> None:
    _refuse_root()

    # ── PipeWire configs ──
    # sink-virtual-surround-7.1-hesuvi.conf → filter-chain.conf.d (loaded by filter-chain service)
    #
    # NOTE: 10-arctis-virtual-sinks.conf is intentionally NOT installed here.
    # Virtual sinks (Arctis_Game / Arctis_Chat / Arctis_Media) are now created
    # dynamically by the daemon at runtime via pw-loopback child processes
    # (LoopbackManager).  A static pipewire.conf.d entry would create duplicate
    # loopbacks that cannot be unloaded at runtime ("Access denied" from pactl).
    # Any legacy copy is removed by check_and_fix_stale_configs() on daemon start.
    pw_src = _SHARE_DIR / "pipewire"
    if pw_src.is_dir():
        _PW_CONF_DIR.mkdir(parents=True, exist_ok=True)
        _FC_CONF_DIR.mkdir(parents=True, exist_ok=True)
        for conf in pw_src.glob("*.conf"):
            # Skip the static virtual-sinks file — loopbacks are now dynamic.
            if conf.name == "10-arctis-virtual-sinks.conf":
                print(f"  [skip] {conf.name} — loopbacks are now dynamic (managed by daemon)")
                # Remove any stale copy that was installed by a previous version.
                stale_sinks = _PW_CONF_DIR / conf.name
                if stale_sinks.exists():
                    stale_sinks.unlink()
                    print(f"  [ok] Removed legacy static loopback config from pipewire.conf.d")
                continue
            if "hesuvi" in conf.name or "surround" in conf.name:
                # HeSuVi surround → filter-chain.conf.d (avoids duplicate-node conflict)
                dst = _FC_CONF_DIR / conf.name
            else:
                dst = _PW_CONF_DIR / conf.name
            if not dst.exists():
                text = conf.read_text()
                if "${HRIR_DIR}" in text:
                    # Substitute placeholder with the absolute HRIR path so PipeWire
                    # can find the WAV regardless of its working directory.
                    text = text.replace("${HRIR_DIR}", str(_HRIR_DIR))
                    dst.write_text(text)
                else:
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
    def _hrir_valid(path: Path) -> bool:
        try:
            return path.exists() and path.read_bytes()[:4] == b"RIFF"
        except OSError:
            return False

    hrir_file = _HRIR_DIR / "hrir.wav"
    if _hrir_valid(hrir_file):
        print("  [ok] HRIR file already present — skipping download")
    else:
        hrir_file.unlink(missing_ok=True)
        _HRIR_DIR.mkdir(parents=True, exist_ok=True)
        print("  Downloading default HRIR file (EAC_Default)...")
        downloaded = False
        for tool, cmd in [
            ("curl", ["curl", "-fsSL", "-o", str(hrir_file), _HRIR_URL]),
            ("wget", ["wget", "-q", "-O", str(hrir_file), _HRIR_URL]),
        ]:
            try:
                subprocess.run(cmd, check=True, timeout=60)
                if _hrir_valid(hrir_file):
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
    from arctis_sound_manager.udev_checker import is_udev_rules_valid, _expected_pids, _pids_in_rules

    # Fix D: write .setup_done BEFORE any elevated step so that a cancelled
    # pkexec prompt doesn't cause the autostart to re-trigger asm-setup on every
    # subsequent login. Any remaining udev issue is caught by the GUI's _check_udev().
    flag = Path.home() / ".config" / "arctis_manager" / ".setup_done"
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.touch()

    rules_already_valid = is_udev_rules_valid()
    if not rules_already_valid:
        if asm_cli:
            result = subprocess.run(
                [asm_cli, "udev", "write-rules", "--force", "--reload"],
                text=True,
            )
            if result.returncode == 0:
                print("  [ok] udev rules installed (reload+trigger included)")
            else:
                print("  [!] udev rules failed — run manually: asm-cli udev write-rules --force --reload")
        else:
            print("  [!] asm-cli not found — run manually: asm-cli udev write-rules --force --reload")
    else:
        print("  [ok] udev rules already valid — skipping write (installed by package)")
        # Fix C: detect a stale /etc rules file that udev prioritises over /usr/lib.
        # udev processes /etc before /usr/lib, so a leftover /etc file with fewer
        # PIDs silently shadows the up-to-date package rules for newer devices.
        _etc_rules = Path("/etc/udev/rules.d/91-steelseries-arctis.rules")
        if _etc_rules.exists() and asm_cli:
            try:
                _expected = {pid for _, pid in _expected_pids()}
                _covered = _pids_in_rules(_etc_rules.read_text())
                if _expected and not _expected.issubset(_covered):
                    _missing = sorted(_expected - _covered)
                    print(
                        "  [!] Stale /etc udev rules found (missing "
                        + ", ".join(f"0x{p:04x}" for p in _missing)
                        + ") — updating in-place..."
                    )
                    result = subprocess.run(
                        [asm_cli, "udev", "write-rules", "--force", "--reload"],
                        text=True,
                    )
                    if result.returncode == 0:
                        print("  [ok] /etc rules updated and reloaded")
                    else:
                        print("  [!] could not update /etc rules — run manually: "
                              "asm-cli udev write-rules --force --reload")
            except OSError:
                pass
    # Fix B: do NOT call 'reload-rules' separately when rules are valid.
    # The package post-install hook (pacman/rpm/deb) already ran udevadm
    # reload+trigger as root at install time. Calling it again from a user
    # session only triggers an unnecessary pkexec prompt on every asm-setup run.

    # ── Stale user-level arctis-gui.service cleanup ──
    # Pipx installs used to write ~/.config/systemd/user/arctis-gui.service with
    # ExecStart pointing to ~/.local/bin/asm-gui. After migrating to a system
    # package (AUR/COPR/DEB) that binary no longer exists, causing a crash loop.
    # Remove the stale override so the system-level unit takes precedence.
    _cleanup_stale_gui_service()

    # ── Systemd / dinit services ──
    print("\n==> Enabling services...")
    if detect_init() == "dinit":
        _setup_dinit_services()
    else:
        fc_service = _ensure_filter_chain_service()
        _run_systemctl(["daemon-reload"])

        # Restart PipeWire first so it picks up the new pipewire.conf.d configs
        _run_systemctl(["restart", "pipewire", "pipewire-pulse"])

        _run_systemctl(["enable", "--now", "arctis-manager.service"])
        _run_systemctl(["enable", "--now", "arctis-video-router.service"])
        _run_systemctl(["enable", "--now", fc_service])
        from arctis_sound_manager.autostart import set_autostart
        set_autostart(True)

    # ── Validate device YAML overrides ──
    # asm-setup keeps copying YAMLs into ~/.config/arctis_manager/devices/ on
    # every run (the user can override them) — but if a previous run was
    # interrupted mid-copy, one of those files might be corrupt and would
    # crash the daemon. Check each is valid YAML before considering setup done.
    bad_yamls: list[str] = []
    if _DEVICE_DIR.is_dir():
        try:
            from ruamel.yaml import YAML
            yaml = YAML(typ='safe')
            for f in _DEVICE_DIR.glob('*.yaml'):
                try:
                    payload = yaml.load(f)
                    if not isinstance(payload, dict) or 'device' not in payload:
                        bad_yamls.append(f.name)
                except Exception:
                    bad_yamls.append(f.name)
        except Exception:
            pass
    if bad_yamls:
        print(f"  [!] Invalid device YAMLs in {_DEVICE_DIR}: {bad_yamls} — daemon will skip them.")

    if bad_yamls:
        print("\n==> Setup complete with warnings (see above).")
        sys.exit(1)

    # A stale crash report from a pre-setup launch (broken install, old version) is
    # no longer relevant once setup finishes — clear it so the GUI does not nag.
    try:
        from arctis_sound_manager.bug_reporter import clear_crash_report
        clear_crash_report()
    except Exception:
        pass

    print("\n==> Setup complete!")


if __name__ == "__main__":
    main()
