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
import re
import shutil
import subprocess
import sys
from pathlib import Path

from arctis_sound_manager import service_control as sc
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

_DINIT_ARCTIS_STREAM_GUARD = """\
type = process
command = {asm_stream_guard}
restart = true
depends-on = arctis-manager
logfile = /tmp/arctis-stream-guard.log
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
    asm_stream_guard = shutil.which("asm-stream-guard") or "/usr/bin/asm-stream-guard"

    (HOME_DINIT_SERVICE_FOLDER / "arctis-manager").write_text(
        _DINIT_ARCTIS_MANAGER.format(asm_daemon=asm_daemon))
    (HOME_DINIT_SERVICE_FOLDER / "arctis-video-router").write_text(
        _DINIT_ARCTIS_VIDEO_ROUTER.format(asm_router=asm_router))
    (HOME_DINIT_SERVICE_FOLDER / "arctis-stream-guard").write_text(
        _DINIT_ARCTIS_STREAM_GUARD.format(asm_stream_guard=asm_stream_guard))

    # filter-chain uses absolute path to filter-chain.conf (no WorkingDirectory in dinit)
    fc_conf = filter_chain_conf_path()
    (HOME_DINIT_SERVICE_FOLDER / "pipewire-filter-chain").write_text(
        _DINIT_FILTER_CHAIN_TEMPLATE.format(fc_conf=fc_conf))

    print("  [dinit] service files written to", HOME_DINIT_SERVICE_FOLDER)

    # Remove stale arctis-gui dinit service — replaced by XDG autostart.
    # Uses sc.run_raw() (a literal-command escape hatch) rather than
    # sc.stop()/sc.disable() because those map the logical name "arctis-gui"
    # through _SERVICE_MAP, which resolves to None on dinit by design (no
    # dinit service normally exists for the GUI) — that would silently no-op
    # instead of cleaning up this one-off leftover from before XDG autostart.
    old_gui_svc = HOME_DINIT_SERVICE_FOLDER / "arctis-gui"
    if old_gui_svc.exists():
        if sc.run_raw(["dinitctl", "disable", "arctis-gui"]) is None:
            print("  [!] dinitctl disable arctis-gui unavailable — continuing")
        if sc.run_raw(["dinitctl", "stop", "arctis-gui"]) is None:
            print("  [!] dinitctl stop arctis-gui unavailable — continuing")
        old_gui_svc.unlink()
        print("  [dinit] removed stale arctis-gui service (replaced by XDG autostart)")

    # Ensure a user `boot` service + boot.d/ exist so `dinitctl enable` can succeed
    _ensure_dinit_boot_target()

    # Reload stopped service definitions so dinit picks up restart=true from updated files.
    # Running services cannot be reloaded — skip them; they already have the right behaviour.
    # All dinitctl calls below go through sc.run_raw() (EXT-2): it catches
    # FileNotFoundError/OSError/TimeoutExpired internally and returns None
    # instead of raising, so a dinit box missing dinitctl from PATH prints a
    # clear per-step message and setup finishes instead of dying with an
    # uncaught traceback partway through.
    for svc in ["arctis-video-router", "arctis-stream-guard", "pipewire-filter-chain"]:
        st = sc.run_raw(["dinitctl", "status", svc])
        if st is None:
            print(f"  [!] dinitctl status {svc} unavailable — skipping reload")
            continue
        if "started" not in st.stdout.lower():
            if sc.run_raw(["dinitctl", "reload", svc]) is None:
                print(f"  [!] dinitctl reload {svc} unavailable — continuing")

    # Restart PipeWire best-effort (may fail if not a dinit service).
    # Sleep briefly after so the socket is ready before we start dependants.
    pw_restart = sc.run_raw(["dinitctl", "restart", "pipewire"])
    if pw_restart is None:
        print("  [!] dinitctl restart pipewire unavailable — continuing")
    elif pw_restart.returncode == 0:
        time.sleep(0.5)

    # Guard: use pgrep against the actual process instead of dinitctl status to avoid a
    # timing race where the boot-sequence auto-start and this setup run overlap.
    pgrep_result = sc.run_raw(["pgrep", "-f", "asm-daemon"])
    if pgrep_result is None:
        print("  [!] pgrep asm-daemon unavailable — assuming not running")
        am_already_running = False
    else:
        am_already_running = pgrep_result.returncode == 0

    # Enable and start each service (use 'start' not 'restart' — idempotent if not yet running).
    # Guard every service individually to avoid double-launch (issue #25).
    for svc in ["arctis-manager", "arctis-video-router", "arctis-stream-guard",
                "pipewire-filter-chain"]:
        en = sc.run_raw(["dinitctl", "enable", svc])
        if en is None:
            print(f"  [!] dinitctl enable {svc} unavailable — continuing")
        elif en.returncode != 0:
            print(f"  [dinit] {svc}: enable failed ({en.stderr.strip() or 'unknown error'})")

        if svc == "arctis-manager" and am_already_running:
            print("  [dinit] arctis-manager: already running — skipping start")
            continue

        svc_st = sc.run_raw(["dinitctl", "status", svc])
        if svc_st is None:
            print(f"  [!] dinitctl status {svc} unavailable — skipping start")
            continue
        if svc_st.returncode == 0 and "started" in svc_st.stdout.lower():
            print(f"  [dinit] {svc}: already running — skipping start")
            continue

        st = sc.run_raw(["dinitctl", "start", svc])
        if st is None:
            print(f"  [!] dinitctl start {svc} unavailable — continuing")
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
# A systemd user unit inherits none of the shell's environment, so PipeWire
# searches only its built-in LADSPA directories. ASM stages plugins the host
# may not have into ~/.ladspa (issue #100) and writes their absolute path into
# the generated conf. PipeWire 1.6.8 loads an absolute path directly, but a
# reporter on PipeWire 1.6.4 (issue #203) had it resolved as a *name* against
# the built-in directories instead — the plugin was never found, the
# filter-chain module carries `nofail`, and HeSuVi silently never appeared:
# the channels then pointed node.target at a node that did not exist, which is
# audio that stops half a second in. Naming the directory here makes both
# lookups succeed, on every version.
Environment=LADSPA_PATH=%h/.ladspa:/usr/lib64/ladspa:/usr/lib/ladspa:/usr/lib
ExecStart=/usr/bin/pipewire -c filter-chain.conf
Restart=on-failure

[Install]
WantedBy=pipewire-session-manager.service
"""

# Bump when _FILTER_CHAIN_SERVICE (or the packaged /usr/share copy) changes in
# a way existing installs must pick up. Same mechanism as the generated
# filter-chain confs' "# ASM-CONF-VERSION" header, for the same reason: without
# it, the copy in $HOME wins for ever and no revision ever reaches an existing
# install (PKG-3).
_UNIT_VERSION = 2
_UNIT_VERSION_MARKER = f"# ASM-UNIT-VERSION: {_UNIT_VERSION}"
_UNIT_VERSION_RE = re.compile(r"^\s*#\s*ASM-UNIT-VERSION:\s*(\d+)\s*$", re.MULTILINE)


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

    dest = Path.home() / ".config" / "systemd" / "user" / "filter-chain.service"

    for name in ("filter-chain.service", "pipewire-filter-chain.service"):
        result = subprocess.run(
            ["systemctl", "--user", "list-unit-files", name],
            capture_output=True, text=True,
        )
        if name.split(".")[0] in result.stdout:
            # The unit exists — but if it is the copy ASM itself put in $HOME,
            # this branch used to return here and never look at it again, so a
            # revision of the packaged unit could not reach an existing install
            # (PKG-3). That is the "local copy wins, never migrated" shape that
            # cost months on device profiles. Units owned by the distribution
            # (/usr/lib/systemd/user) are not ours and are left alone.
            if name == "filter-chain.service":
                _migrate_home_filter_chain_unit(dest)
            return name

    # Not found on this distro — install our bundled copy
    _write_filter_chain_unit(dest)
    print(f"  [ok] filter-chain.service installed → {dest}")
    _run_systemctl(["daemon-reload"])
    return "filter-chain.service"


def _packaged_filter_chain_unit() -> str:
    """The unit ASM ships: the /usr/share copy when packaged, else the builtin."""
    bundled = _SHARE_DIR / "filter-chain.service"
    if bundled.exists():
        try:
            return bundled.read_text()
        except OSError:
            pass
    return _FILTER_CHAIN_SERVICE


def _stamped(text: str) -> str:
    """Tag *text* with the version marker, the way generated confs are."""
    if _UNIT_VERSION_RE.search(text):
        return text
    return f"{_UNIT_VERSION_MARKER}\n{text}"


def _write_filter_chain_unit(dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(_stamped(_packaged_filter_chain_unit()))


def _migrate_home_filter_chain_unit(dest: Path) -> None:
    """Refresh ASM's own copy of the unit in $HOME when the packaged one moved.

    Nothing else does: the unit is written once, at first setup, and every
    later run only checks that *a* unit by that name exists. A file with no
    version marker predates this check — it was written by ASM too, since this
    file has never been meant for hand-editing — so it is refreshed once and
    backed up first, which keeps a customised copy recoverable.
    """
    if not dest.exists():
        return

    try:
        current = dest.read_text()
    except OSError as exc:
        print(f"  [!] could not read {dest}: {exc}")
        return

    wanted = _stamped(_packaged_filter_chain_unit())
    if current == wanted:
        return

    match = _UNIT_VERSION_RE.search(current)
    if match and int(match.group(1)) == _UNIT_VERSION:
        # Same version, different content: someone edited it on purpose.
        return

    try:
        backup = dest.with_suffix(".service.bak")
        shutil.copy2(dest, backup)
        dest.write_text(wanted)
    except OSError as exc:
        print(f"  [!] could not refresh {dest}: {exc}")
        return

    print(f"  [ok] filter-chain.service refreshed → {dest} (old copy: {backup})")
    _run_systemctl(["daemon-reload"])


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


def _cli_invocation() -> list[str]:
    """Command prefix used to invoke asm-cli.

    Prefers the installed ``asm-cli`` binary; when it is not on PATH (e.g. when
    running from a source checkout for debugging), falls back to the in-tree
    module via the current interpreter so udev rules and the desktop entry still
    get written instead of being silently skipped.
    """
    asm_cli = shutil.which("asm-cli")
    if asm_cli:
        return [asm_cli]
    return [sys.executable, "-m", "arctis_sound_manager.scripts.cli"]


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
    # Deliberately NOT copied into ~/.config/arctis_manager/devices/ any more.
    # That folder takes precedence over the packaged profiles and is never
    # refreshed by an upgrade, so seeding it with copies pinned every headset to
    # whatever release the user first ran: new product ids, status offsets and EQ
    # opcodes shipped afterwards never reached them, silently and permanently.
    # ASM reads its own profiles directly; the folder is for profiles a user
    # writes themselves. Existing copies are cleaned up on every daemon start —
    # see device_override_reconcile.
    from arctis_sound_manager.constants import (HOME_CONFIG_FOLDER,
                                                SRC_CONFIG_FOLDER)
    from arctis_sound_manager.device_override_reconcile import (
        DISABLED_DIRNAME, reconcile_home_device_overrides)

    report = reconcile_home_device_overrides(HOME_CONFIG_FOLDER, SRC_CONFIG_FOLDER)
    for name in report.removed:
        print(f"  [ok] removed redundant profile copy {name} (ASM uses its own)")
    for name in report.disabled:
        print(f"  [!] {name} shadowed the packaged profile and was not receiving "
              f"updates — moved to {HOME_CONFIG_FOLDER.parent / DISABLED_DIRNAME}/")
    for name in report.kept:
        print(f"  [ok] keeping your own profile {name}")
    if not (report.removed or report.disabled or report.kept):
        print("  [ok] device profiles read from the package")

    # ── HRIR file ──
    def _hrir_valid(path: Path) -> bool:
        try:
            # Must be a real RIFF/WAV file of at least 10 KB — rules out read-only
            # Nix store stubs (3.6 KB, epoch mtime) that have a RIFF header but are
            # too short to be a usable 7.1 HRIR impulse response.
            return (path.exists()
                    and path.stat().st_size > 10_000
                    and path.read_bytes()[:4] == b"RIFF")
        except OSError:
            return False

    hrir_file = _HRIR_DIR / "hrir.wav"
    if _hrir_valid(hrir_file):
        print("  [ok] HRIR file already present — skipping download")
    else:
        hrir_file.unlink(missing_ok=True)
        _HRIR_DIR.mkdir(parents=True, exist_ok=True)

        # Try bundled assets first (available in system-package installs without network)
        import importlib.resources as _ir
        _bundled: Path | None = None
        try:
            _assets = Path(_ir.files("arctis_sound_manager")) / "hrir_assets"
            _candidates = sorted(_assets.glob("*.wav"))
            if _candidates:
                _bundled = _candidates[0]
        except Exception:
            pass

        if _bundled and _hrir_valid(_bundled):
            hrir_file.write_bytes(_bundled.read_bytes())
            print(f"  [ok] HRIR copied from package → {hrir_file}")
        else:
            print(f"  Downloading default HRIR file (EAC_Default) from {_HRIR_URL}...")
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

    asm_cli_cmd = _cli_invocation()

    # ── Desktop entry + systemd service file ──
    # Always written, even when the AUR/COPR/DEB package ships a system-level
    # entry. This used to be skipped in that case, on the assumption that a
    # second entry would show up twice in the launcher — it does not: the user
    # entry has the same file name as the system one
    # (~/.local/share/applications/ArctisManager.desktop vs
    # /usr/share/applications/ArctisManager.desktop), and the XDG spec resolves
    # entries by ID, so the user one simply *shadows* the system one.
    #
    # Skipping meant ASM had no entry of its own to fall back on, and a package
    # transaction that leaves the system file missing took the app out of the
    # launcher with nothing to restore it (reported on Discord after upgrading
    # to 1.2.7 on Fedora/COPR: "ASM disappeared from my app launcher", fixed by
    # running asm-cli desktop write by hand). The package owns the system file;
    # ASM can only guarantee the user one, so it now always writes it.
    print("\n==> Writing desktop entry and service file...")
    result = subprocess.run(asm_cli_cmd + ["desktop", "write"], text=True)
    if result.returncode == 0:
        print("  [ok] desktop entry and service file written")
    else:
        print("  [!] desktop write failed — run manually: asm-cli desktop write")

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
        result = subprocess.run(
            asm_cli_cmd + ["udev", "write-rules", "--force", "--reload"],
            text=True,
        )
        if result.returncode == 0:
            print("  [ok] udev rules installed (reload+trigger included)")
        else:
            print("  [!] udev rules failed — run manually: asm-cli udev write-rules --force --reload")
    else:
        print("  [ok] udev rules already valid — skipping write (installed by package)")
        # Fix C: detect a stale /etc rules file that udev prioritises over /usr/lib.
        # udev processes /etc before /usr/lib, so a leftover /etc file with fewer
        # PIDs silently shadows the up-to-date package rules for newer devices.
        _etc_rules = Path("/etc/udev/rules.d/91-steelseries-arctis.rules")
        if _etc_rules.exists():
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
                        asm_cli_cmd + ["udev", "write-rules", "--force", "--reload"],
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
        _run_systemctl(["enable", "--now", "arctis-stream-guard.service"])
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
