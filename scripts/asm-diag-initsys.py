#!/usr/bin/env python3
"""
asm-diag-initsys.py — Read-only diagnostic for non-systemd init system support.

Collects everything needed to implement proper ASM service management on
dinit, OpenRC, runit, s6, and other non-systemd init systems.

Makes NO changes to the system. Safe to inspect and run.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

# ── helpers ───────────────────────────────────────────────────────────────────

def run(cmd: list[str], timeout: int = 3) -> tuple[int, str, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except FileNotFoundError:
        return -1, "", "not found"
    except subprocess.TimeoutExpired:
        return -2, "", "timeout"
    except Exception as e:
        return -3, "", str(e)


def found(binary: str) -> bool:
    return shutil.which(binary) is not None


def section(title: str) -> None:
    print(f"\n{'─' * 60}")
    print(f"  {title}")
    print('─' * 60)


def ok(label: str, value: str = "") -> None:
    suffix = f"  →  {value}" if value else ""
    print(f"  [ok]  {label}{suffix}")


def warn(label: str, value: str = "") -> None:
    suffix = f"  →  {value}" if value else ""
    print(f"  [??]  {label}{suffix}")


def miss(label: str, value: str = "") -> None:
    suffix = f"  →  {value}" if value else ""
    print(f"  [--]  {label}{suffix}")


# ── 1. OS / distro ────────────────────────────────────────────────────────────

def check_os() -> dict:
    section("OS & distribution")
    info: dict = {}

    info["platform"] = platform.platform()
    print(f"  platform   : {info['platform']}")

    # /etc/os-release
    osr: dict[str, str] = {}
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if "=" in line:
                k, _, v = line.partition("=")
                osr[k.strip()] = v.strip().strip('"')
    except OSError:
        pass
    info["os_release"] = osr
    print(f"  distro     : {osr.get('PRETTY_NAME', 'unknown')}")
    print(f"  id         : {osr.get('ID', '?')}  id_like={osr.get('ID_LIKE', '(none)')}")

    # kernel
    rc, out, _ = run(["uname", "-r"])
    info["kernel"] = out
    print(f"  kernel     : {out}")

    # python
    info["python"] = sys.version
    print(f"  python     : {sys.version.split()[0]}")

    return info


# ── 2. Init system detection ──────────────────────────────────────────────────

def check_init() -> dict:
    section("Init system")
    info: dict = {}

    # PID 1
    try:
        comm = Path("/proc/1/comm").read_text().strip()
    except OSError:
        comm = "unknown"
    info["pid1_comm"] = comm
    print(f"  PID 1 comm : {comm}")

    try:
        exe = os.readlink("/proc/1/exe")
    except OSError:
        exe = "unknown"
    info["pid1_exe"] = exe
    print(f"  PID 1 exe  : {exe}")

    # Detect by binary presence + PID 1
    candidates = {
        "systemd":  found("systemctl"),
        "dinit":    found("dinitctl"),
        "openrc":   found("rc-service") or found("openrc"),
        "runit":    found("sv") and Path("/etc/runit").exists(),
        "s6":       found("s6-svscan"),
        "s6-rc":    found("s6-rc"),
        "shepherd": found("herd"),
        "66":       found("66"),
    }
    info["detected_inits"] = {k: v for k, v in candidates.items() if v}

    for name, present in candidates.items():
        (ok if present else miss)(name)

    # Active init
    active = "unknown"
    if "dinit" in comm or "dinit" in exe:
        active = "dinit"
    elif "systemd" in comm or "systemd" in exe:
        active = "systemd"
    elif comm in ("runit", "s6-svscan", "openrc-init", "66"):
        active = comm
    elif candidates.get("dinit"):
        active = "dinit (suspected)"
    elif candidates.get("openrc"):
        active = "openrc (suspected)"
    info["active_init"] = active
    print(f"\n  ► active init: {active}")

    return info


# ── 3. Service management tools ───────────────────────────────────────────────

def check_service_tools() -> dict:
    section("Service management tools")
    info: dict = {}

    tools = {
        "dinitctl":    "dinit",
        "rc-service":  "OpenRC",
        "rc-update":   "OpenRC",
        "sv":          "runit",
        "s6-svc":      "s6",
        "s6-rc":       "s6-rc",
        "herd":        "GNU Shepherd",
        "66-enable":   "66",
        "systemctl":   "systemd",
    }
    for binary, init in tools.items():
        if found(binary):
            rc, out, _ = run([binary, "--version"], timeout=2)
            ver = out.splitlines()[0] if out else ""
            ok(f"{binary}  ({init})", ver[:60] if ver else shutil.which(binary))
        else:
            miss(binary)
        info[binary] = found(binary)

    # dinit user service directory
    for d in [
        Path.home() / ".config" / "dinit.d",
        Path("/etc/dinit.d"),
        Path("/lib/dinit.d"),
        Path("/usr/lib/dinit.d"),
    ]:
        exists = d.exists()
        (ok if exists else miss)(f"dinit service dir: {d}", "exists" if exists else "absent")
        info[f"dinit_dir_{d.name}"] = exists

    # OpenRC user services
    for d in [Path.home() / ".config" / "openrc", Path("/etc/init.d")]:
        exists = d.exists()
        (ok if exists else miss)(f"openrc dir: {d}", "exists" if exists else "absent")

    # runit user services
    for d in [
        Path.home() / ".config" / "sv",
        Path("/etc/sv"),
        Path("/var/service"),
    ]:
        exists = d.exists()
        (ok if exists else miss)(f"runit dir: {d}", "exists" if exists else "absent")

    return info


# ── 4. PipeWire ───────────────────────────────────────────────────────────────

def check_pipewire() -> dict:
    section("PipeWire")
    info: dict = {}

    # Version
    rc, out, _ = run(["pipewire", "--version"])
    info["pipewire_version"] = out if rc == 0 else None
    (ok if rc == 0 else miss)("pipewire binary", out.splitlines()[0] if out else "not found")

    rc, out, _ = run(["wireplumber", "--version"])
    info["wireplumber_version"] = out if rc == 0 else None
    (ok if rc == 0 else miss)("wireplumber binary", out.splitlines()[0] if out else "not found")

    # Running?
    rc, out, _ = run(["pactl", "info"])
    info["pipewire_running"] = rc == 0
    (ok if rc == 0 else miss)("PipeWire running (pactl info)")

    # Server name / version from pactl
    if rc == 0:
        for line in out.splitlines():
            if "Server Name" in line or "Server Version" in line:
                print(f"    {line.strip()}")

    # pipewire process
    rc2, out2, _ = run(["pgrep", "-a", "pipewire"])
    if rc2 == 0:
        for line in out2.splitlines()[:4]:
            print(f"    process: {line.strip()}")
        info["pipewire_processes"] = out2.splitlines()

    # filter-chain process?
    rc3, out3, _ = run(["pgrep", "-a", "-f", "filter-chain"])
    info["filter_chain_running"] = rc3 == 0
    (ok if rc3 == 0 else miss)("filter-chain process running")
    if rc3 == 0:
        for line in out3.splitlines()[:3]:
            print(f"    {line.strip()}")

    # wpctl status (surfaces active sinks)
    rc4, out4, _ = run(["wpctl", "status"], timeout=4)
    info["wpctl_ok"] = rc4 == 0
    (ok if rc4 == 0 else miss)("wpctl status")
    if rc4 == 0:
        # Look for ASM virtual sinks
        asm_lines = [l for l in out4.splitlines() if "Arctis" in l or "arctis" in l.lower()]
        if asm_lines:
            ok("ASM virtual sinks visible in wpctl")
            for l in asm_lines[:5]:
                print(f"    {l.strip()}")
        else:
            miss("ASM virtual sinks (not found in wpctl output)")

    # How PipeWire was started (try to detect)
    section_sub = "  PipeWire startup mechanism"
    print(section_sub)
    for method, cmd in [
        ("via systemd user session", ["systemctl", "--user", "status", "pipewire"]),
        ("via dinit",               ["dinitctl", "status", "pipewire"]),
        ("via openrc (user)",       ["rc-service", "pipewire", "status"]),
    ]:
        rc5, out5, _ = run(cmd)
        if rc5 == 0:
            ok(method)
            info["pipewire_start_method"] = method
        else:
            miss(method)

    return info


# ── 5. PipeWire config files ──────────────────────────────────────────────────

def check_pipewire_configs() -> dict:
    section("PipeWire config files")
    info: dict = {}

    paths = {
        "filter-chain.conf (system)": [
            Path("/usr/share/pipewire/filter-chain.conf"),
            Path("/etc/pipewire/filter-chain.conf"),
        ],
        "filter-chain.conf (user)": [
            Path.home() / ".config" / "pipewire" / "filter-chain.conf",
        ],
        "filter-chain.conf.d (user)": [
            Path.home() / ".config" / "pipewire" / "filter-chain.conf.d",
        ],
        "pipewire.conf.d (virtual sinks)": [
            Path.home() / ".config" / "pipewire" / "pipewire.conf.d",
        ],
        "filter-chain.service (systemd user)": [
            Path.home() / ".config" / "systemd" / "user" / "filter-chain.service",
            Path("/usr/lib/systemd/user/filter-chain.service"),
            Path("/usr/share/pipewire/filter-chain.service"),
        ],
    }

    for label, candidates in paths.items():
        found_any = False
        for p in candidates:
            if p.exists():
                size = f"{p.stat().st_size} bytes" if p.is_file() else "dir"
                ok(f"{label}", f"{p}  ({size})")
                info[label] = str(p)
                found_any = True
                if p.is_dir():
                    for child in sorted(p.iterdir())[:8]:
                        print(f"      {child.name}")
                break
        if not found_any:
            miss(label)
            info[label] = None

    return info


# ── 6. ASM install state ──────────────────────────────────────────────────────

def check_asm() -> dict:
    section("ASM install state")
    info: dict = {}

    for binary in ("asm-daemon", "asm-gui", "asm-setup", "asm-cli"):
        path = shutil.which(binary)
        (ok if path else miss)(binary, path or "not in PATH")
        info[binary] = path

    # Version
    rc, out, _ = run(["asm-cli", "--version"])
    info["asm_version"] = out if rc == 0 else None
    (ok if rc == 0 else miss)("asm version", out)

    # udev rules
    for p in [
        Path("/etc/udev/rules.d/99-arctis.rules"),
        Path("/usr/lib/udev/rules.d/99-arctis.rules"),
        Path("/lib/udev/rules.d/99-arctis.rules"),
    ]:
        if p.exists():
            ok(f"udev rules", str(p))
            info["udev_rules"] = str(p)
            break
    else:
        miss("udev rules (not found)")
        info["udev_rules"] = None

    # asm-daemon running?
    rc2, out2, _ = run(["pgrep", "-a", "asm-daemon"])
    info["daemon_running"] = rc2 == 0
    (ok if rc2 == 0 else miss)("asm-daemon running")

    # ~/.local/share/pipewire/hrir_hesuvi/hrir.wav
    hrir = Path.home() / ".local" / "share" / "pipewire" / "hrir_hesuvi" / "hrir.wav"
    info["hrir_present"] = hrir.exists() and hrir.stat().st_size > 0 if hrir.exists() else False
    (ok if info["hrir_present"] else miss)("HRIR file", str(hrir) if hrir.exists() else "absent")

    return info


# ── 7. D-Bus session ─────────────────────────────────────────────────────────

def check_dbus() -> dict:
    section("D-Bus session")
    info: dict = {}

    addr = os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")
    info["dbus_session_addr"] = addr
    (ok if addr else miss)("DBUS_SESSION_BUS_ADDRESS", addr[:60] if addr else "not set")

    rc, out, _ = run(["dbus-send", "--session", "--dest=org.freedesktop.DBus",
                       "--type=method_call", "--print-reply",
                       "/org/freedesktop/DBus", "org.freedesktop.DBus.ListNames"])
    info["dbus_session_reachable"] = rc == 0
    (ok if rc == 0 else miss)("D-Bus session reachable")

    return info


# ── 8. Package manager ────────────────────────────────────────────────────────

def check_pkg() -> dict:
    section("Package manager")
    info: dict = {}

    for pm in ("pacman", "paru", "yay", "dnf", "apt-get", "zypper", "xbps-install"):
        if found(pm):
            ok(pm, shutil.which(pm))
            info["pkg_manager"] = pm
            break
    else:
        miss("no known package manager found")
        info["pkg_manager"] = None

    # Installed ASM package?
    if found("pacman"):
        rc, out, _ = run(["pacman", "-Q", "arctis-sound-manager"])
        (ok if rc == 0 else miss)("arctis-sound-manager package", out if rc == 0 else "not installed via pacman")
        info["pacman_pkg"] = out if rc == 0 else None

    return info


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    print("=" * 60)
    print("  ASM init-system diagnostic")
    print("  Read-only — no changes made to your system")
    print("=" * 60)

    result = {
        "os":       check_os(),
        "init":     check_init(),
        "services": check_service_tools(),
        "pipewire": check_pipewire(),
        "configs":  check_pipewire_configs(),
        "asm":      check_asm(),
        "dbus":     check_dbus(),
        "pkg":      check_pkg(),
    }

    print("\n" + "=" * 60)
    print("  Summary (paste this in the GitHub issue)")
    print("=" * 60)
    print(json.dumps({
        "init":              result["init"].get("active_init"),
        "pid1":              result["init"].get("pid1_comm"),
        "dinitctl":          result["services"].get("dinitctl"),
        "pipewire_running":  result["pipewire"].get("pipewire_running"),
        "filter_chain_proc": result["pipewire"].get("filter_chain_running"),
        "asm_daemon":        result["asm"].get("daemon_running"),
        "hrir":              result["asm"].get("hrir_present"),
        "udev_rules":        result["asm"].get("udev_rules"),
        "dbus_ok":           result["dbus"].get("dbus_session_reachable"),
        "pkg_manager":       result["pkg"].get("pkg_manager"),
        "asm_version":       result["asm"].get("asm_version"),
        "pipewire_version":  result["pipewire"].get("pipewire_version"),
        "filter_chain_conf": result["configs"].get("filter-chain.conf (system)"),
    }, indent=2))

    print("\nPlease copy the full output above and paste it in the GitHub issue.")


if __name__ == "__main__":
    main()
