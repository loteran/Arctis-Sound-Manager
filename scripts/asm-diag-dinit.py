#!/usr/bin/env python3
"""
asm-diag-dinit.py — Read-only diagnostic for dinit-based ASM installations.

Validates the ASM dinit service setup on Artix Linux (or any dinit system).
Checks: init detection, all 4 dinit service files, service status/enabled,
filter-chain.conf absolute path (Correction B), PipeWire virtual sinks,
udev rules, HRIR, D-Bus, and dinit implementation corrections.

Makes NO changes to the system. Safe to inspect and run.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# ── helpers ───────────────────────────────────────────────────────────────────


def run(cmd: list[str], timeout: int = 4) -> tuple[int, str, str]:
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


_DINIT_SERVICE_DIRS = [
    Path.home() / ".config" / "dinit.d",
    Path("/etc/dinit.d"),
    Path("/usr/lib/dinit.d"),
]


def _is_dinit_service_enabled(svc: str) -> bool:
    """Return True if a waits-for.d/<svc> symlink exists in any dinit service directory.

    dinit has no 'is-enabled' subcommand (verified against upstream dinitctl.cc).
    Enabling creates a symlink in the parent service's waits-for.d directory.
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


def section(title: str) -> None:
    print(f"\n{'─' * 60}")
    print(f"  {title}")
    print("─" * 60)


def ok(label: str, value: str = "") -> None:
    suffix = f"  →  {value}" if value else ""
    print(f"  [ok]  {label}{suffix}")


def err(label: str, value: str = "") -> None:
    suffix = f"  →  {value}" if value else ""
    print(f"  [!!]  {label}{suffix}")


def miss(label: str, value: str = "") -> None:
    suffix = f"  →  {value}" if value else ""
    print(f"  [--]  {label}{suffix}")


def info_line(label: str, value: str = "") -> None:
    suffix = f"  →  {value}" if value else ""
    print(f"  [ii]  {label}{suffix}")


# ── A. Init system ────────────────────────────────────────────────────────────


def check_init() -> dict:
    section("A. Init system")
    result: dict = {}

    # PID 1 comm
    try:
        comm = Path("/proc/1/comm").read_text().strip()
    except OSError:
        comm = "unknown"
    result["pid1_comm"] = comm
    if comm == "dinit":
        ok("PID 1 is dinit", comm)
        result["is_dinit"] = True
    else:
        err("PID 1 is NOT dinit — wrong diagnostic script?", f"found: {comm}")
        result["is_dinit"] = False

    # dinitctl version
    rc, out, _ = run(["dinitctl", "--version"])
    if rc == 0:
        version_str = out.splitlines()[0] if out else ""
        result["dinitctl_version"] = version_str
        ok("dinitctl accessible", version_str)
    else:
        err("dinitctl not found or not accessible")
        result["dinitctl_version"] = None

    # dinit service dirs
    service_dirs = [
        Path.home() / ".config" / "dinit.d",
        Path("/etc/dinit.d"),
        Path("/usr/lib/dinit.d"),
    ]
    result["service_dirs"] = {}
    for d in service_dirs:
        exists = d.exists()
        result["service_dirs"][str(d)] = exists
        (ok if exists else miss)(f"dinit service dir: {d}", "exists" if exists else "absent")

    return result


# ── B. ASM dinit service files ────────────────────────────────────────────────


_SERVICES = ["arctis-manager", "arctis-video-router", "arctis-gui", "pipewire-filter-chain"]
_USER_DINIT_D = Path.home() / ".config" / "dinit.d"


def _check_service_file(svc: str) -> tuple[bool, str | None]:
    """Return (exists, file_content_or_None)."""
    p = _USER_DINIT_D / svc
    if p.exists():
        try:
            return True, p.read_text()
        except OSError:
            return True, None
    return False, None


def _extract_depends_on(content: str) -> list[str]:
    deps = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("depends-on"):
            _, _, val = stripped.partition("=")
            deps.append(val.strip())
    return deps


def _extract_command(content: str) -> str | None:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("command"):
            _, _, val = stripped.partition("=")
            return val.strip()
    return None


def check_services() -> dict:
    section("B. ASM dinit service files")
    result: dict = {
        "services_present": {},
        "services_running": {},
        "services_enabled": {},
        "filter_chain_conf_absolute": None,
        "filter_chain_conf_exists": None,
        "filter_chain_conf_path": None,
        "depends_on": {},
    }

    for svc in _SERVICES:
        print(f"\n  ── {svc}")

        # File exists in ~/.config/dinit.d/?
        exists, content = _check_service_file(svc)
        result["services_present"][svc] = exists
        if exists:
            ok(f"service file present", str(_USER_DINIT_D / svc))
        else:
            miss(f"service file absent", str(_USER_DINIT_D / svc))

        # dinitctl status <svc>
        rc, out, _ = run(["dinitctl", "status", svc])
        running = rc == 0 and "started" in out.lower()
        result["services_running"][svc] = running
        (ok if running else miss)(
            f"dinitctl status {svc}",
            out.splitlines()[0] if out else "not running",
        )

        # autostart check — dinit has no 'is-enabled' subcommand; enabled state
        # is encoded as a symlink in the parent service's waits-for.d directory
        enabled = _is_dinit_service_enabled(svc)
        result["services_enabled"][svc] = enabled
        (ok if enabled else miss)(
            f"autostart (waits-for.d symlink) for {svc}",
            "found" if enabled else "absent — service won't autostart after next login",
        )

        # depends-on
        if content:
            deps = _extract_depends_on(content)
            result["depends_on"][svc] = deps
            if deps:
                info_line(f"depends-on", ", ".join(deps))
            else:
                miss("no depends-on found in service file")

        # Extra checks for pipewire-filter-chain
        if svc == "pipewire-filter-chain" and content is not None:
            cmd = _extract_command(content)
            if cmd:
                info_line("command", cmd)
                # Extract path argument (e.g. "pipewire -c /absolute/path/to/conf")
                parts = cmd.split()
                conf_path: str | None = None
                for i, part in enumerate(parts):
                    if part == "-c" and i + 1 < len(parts):
                        conf_path = parts[i + 1]
                        break
                if conf_path is not None:
                    is_absolute = conf_path.startswith("/")
                    result["filter_chain_conf_path"] = conf_path
                    result["filter_chain_conf_absolute"] = is_absolute
                    if is_absolute:
                        ok("Correction B: filter-chain.conf path is absolute", conf_path)
                    else:
                        err(
                            "Correction B: filter-chain.conf path is RELATIVE — must be absolute",
                            conf_path,
                        )
                    # Check if conf file actually exists
                    conf_exists = Path(conf_path).exists() if is_absolute else False
                    result["filter_chain_conf_exists"] = conf_exists
                    if conf_exists:
                        ok("Correction B: filter-chain.conf file exists", conf_path)
                    else:
                        (err if is_absolute else miss)(
                            "Correction B: filter-chain.conf file does NOT exist",
                            conf_path,
                        )
                else:
                    miss("could not extract -c <conf> from command line")
            else:
                miss("could not extract command from service file")

    return result


# ── C. PipeWire ───────────────────────────────────────────────────────────────


def _pw_version_from_pactl(pactl_out: str) -> str | None:
    for line in pactl_out.splitlines():
        if "Server Version" in line:
            _, _, v = line.partition(":")
            return v.strip()
    return None


def _filter_chain_running_via_proc() -> bool:
    """Check /proc for a pipewire process with 'filter-chain' in its cmdline."""
    try:
        for pid_dir in Path("/proc").iterdir():
            if not pid_dir.name.isdigit():
                continue
            try:
                cmdline = (pid_dir / "cmdline").read_text().replace("\x00", " ").strip()
                if "pipewire" in cmdline and "filter-chain" in cmdline:
                    return True
            except OSError:
                continue
    except OSError:
        pass
    return False


def check_pipewire() -> dict:
    section("C. PipeWire")
    result: dict = {}

    # PipeWire running? (pactl info)
    rc, out, _ = run(["pactl", "info"])
    pw_running = rc == 0
    result["pipewire_running"] = pw_running

    pw_version = None
    if pw_running:
        pw_version = _pw_version_from_pactl(out)
        ok("PipeWire running (pactl info)", pw_version or "")
        for line in out.splitlines():
            if "Server Name" in line or "Server Version" in line:
                print(f"      {line.strip()}")
    else:
        miss("PipeWire NOT running (pactl info failed)")
    result["pipewire_version"] = pw_version

    # dinitctl status pipewire
    rc2, out2, _ = run(["dinitctl", "status", "pipewire"])
    pw_via_dinit = rc2 == 0 and "started" in out2.lower()
    result["pipewire_via_dinit"] = pw_via_dinit
    (ok if pw_via_dinit else miss)(
        "dinitctl status pipewire",
        out2.splitlines()[0] if out2 else ("running" if pw_via_dinit else "not running via dinit"),
    )

    # filter-chain process running? (via /proc)
    fc_running = _filter_chain_running_via_proc()
    result["filter_chain_running"] = fc_running
    (ok if fc_running else miss)("filter-chain process running (checked via /proc)")

    # Virtual sinks visible? (wpctl status)
    rc3, out3, _ = run(["wpctl", "status"], timeout=5)
    result["wpctl_ok"] = rc3 == 0
    virtual_sinks: list[str] = []
    sinks_visible = False
    if rc3 == 0:
        for needle in ("Arctis_Game", "Arctis_Chat", "Arctis_Media"):
            matches = [l.strip() for l in out3.splitlines() if needle in l]
            if matches:
                sinks_visible = True
                virtual_sinks.extend(matches)
        if sinks_visible:
            ok("ASM virtual sinks visible in wpctl")
            for line in virtual_sinks[:6]:
                print(f"      {line}")
        else:
            miss("ASM virtual sinks not found in wpctl output")
    else:
        miss("wpctl status failed — PipeWire not reachable?")
    result["virtual_sinks_visible"] = sinks_visible

    # Config files
    vs_conf = (
        Path.home() / ".config" / "pipewire" / "pipewire.conf.d" / "10-arctis-virtual-sinks.conf"
    )
    fc_conf = (
        Path.home()
        / ".config"
        / "pipewire"
        / "filter-chain.conf.d"
        / "sink-virtual-surround-7.1-hesuvi.conf"
    )
    for p, label in [
        (vs_conf, "virtual-sinks conf"),
        (fc_conf, "filter-chain HeSuVi conf"),
    ]:
        exists = p.exists()
        result[f"{label.replace(' ', '_')}_exists"] = exists
        (ok if exists else miss)(f"{label}", str(p))

    return result


# ── D. ASM install state ──────────────────────────────────────────────────────


def _asm_version() -> str | None:
    """Try asm-daemon --version, then pyproject.toml, then importlib.metadata."""
    rc, out, _ = run(["asm-daemon", "--version"])
    if rc == 0 and out:
        return out.strip()

    # importlib.metadata
    try:
        import importlib.metadata as meta
        return meta.version("arctis-sound-manager")
    except Exception:
        pass

    # Fallback: pyproject.toml next to the script
    candidates = [
        Path(__file__).parent.parent / "pyproject.toml",
        Path.cwd() / "pyproject.toml",
    ]
    for p in candidates:
        if p.exists():
            try:
                for line in p.read_text().splitlines():
                    m = re.match(r'\s*version\s*=\s*"([^"]+)"', line)
                    if m:
                        return m.group(1)
            except OSError:
                pass
    return None


def check_asm() -> dict:
    section("D. ASM install state")
    result: dict = {}

    for binary in ("asm-daemon", "asm-gui", "asm-router", "asm-cli"):
        path = shutil.which(binary)
        result[binary] = path
        (ok if path else miss)(binary, path or "not in PATH")

    version = _asm_version()
    result["asm_version"] = version
    (ok if version else miss)("ASM version", version or "could not determine")

    # HRIR file
    hrir = Path.home() / ".local" / "share" / "pipewire" / "hrir_hesuvi" / "hrir.wav"
    hrir_ok = hrir.exists() and hrir.stat().st_size > 0 if hrir.exists() else False
    result["hrir"] = hrir_ok
    (ok if hrir_ok else miss)("HRIR file", str(hrir) if hrir.exists() else "absent")

    return result


# ── E. udev rules ─────────────────────────────────────────────────────────────


def check_udev() -> dict:
    section("E. udev rules")
    result: dict = {}

    rules_path = Path("/etc/udev/rules.d/91-steelseries-arctis.rules")
    exists = rules_path.exists()
    result["udev_rules"] = exists
    if exists:
        ok("udev rules file present", str(rules_path))
        try:
            content = rules_path.read_text()
            has_pid = "1038" in content
            result["udev_contains_steelseries_pid"] = has_pid
            if has_pid:
                ok("SteelSeries vendor ID 0x1038 found in rules")
            else:
                err("SteelSeries vendor ID 0x1038 NOT found in rules")
        except OSError:
            miss("could not read udev rules file")
    else:
        miss("udev rules file absent", str(rules_path))
        result["udev_contains_steelseries_pid"] = False

    # lsusb — show connected SteelSeries devices
    if found("lsusb"):
        rc, out, _ = run(["lsusb"])
        if rc == 0:
            ss_devs = [l for l in out.splitlines() if "1038" in l or "SteelSeries" in l]
            if ss_devs:
                ok("SteelSeries devices found via lsusb")
                for line in ss_devs:
                    print(f"      {line.strip()}")
                result["steelseries_usb_devices"] = ss_devs
            else:
                miss("no SteelSeries USB devices found via lsusb")
                result["steelseries_usb_devices"] = []
    else:
        miss("lsusb not available")

    return result


# ── F. D-Bus session ──────────────────────────────────────────────────────────


def check_dbus() -> dict:
    section("F. D-Bus session")
    result: dict = {}

    addr = os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")
    result["dbus_session_addr"] = addr
    if addr:
        ok("DBUS_SESSION_BUS_ADDRESS set", addr[:70])
    else:
        miss("DBUS_SESSION_BUS_ADDRESS not set")

    dbus_send_ok = found("dbus-send")
    result["dbus_send_available"] = dbus_send_ok
    (ok if dbus_send_ok else miss)("dbus-send available", shutil.which("dbus-send") or "absent")

    # Ping session bus
    rc, out, _ = run(
        [
            "dbus-send",
            "--session",
            "--dest=org.freedesktop.DBus",
            "--type=method_call",
            "--print-reply",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus.Peer.Ping",
        ]
    )
    dbus_ok = rc == 0
    result["dbus_ok"] = dbus_ok
    (ok if dbus_ok else miss)("D-Bus session bus reachable (Peer.Ping)")

    return result


# ── G. Correction validation ──────────────────────────────────────────────────


def check_corrections(services: dict, pipewire: dict) -> dict:
    section("G. Dinit implementation checks")
    result: dict = {}

    # Correction B: filter-chain.conf path absolute
    fc_abs = services.get("filter_chain_conf_absolute")
    if fc_abs is True:
        ok("Correction B: filter-chain.conf path is absolute")
    elif fc_abs is False:
        err("Correction B: filter-chain.conf path is RELATIVE")
    else:
        miss("Correction B: filter-chain.conf path (service file absent or command not parsed)")
    result["correction_b_absolute"] = fc_abs

    # Correction B: filter-chain.conf file exists
    fc_exists = services.get("filter_chain_conf_exists")
    if fc_exists is True:
        ok("Correction B: filter-chain.conf file exists at that path")
    elif fc_exists is False:
        err("Correction B: filter-chain.conf file does NOT exist at that path")
    else:
        miss("Correction B: filter-chain.conf file check skipped")
    result["correction_b_exists"] = fc_exists

    # Correction C: arctis-manager service file present (start idempotency)
    am_present = services["services_present"].get("arctis-manager", False)
    (ok if am_present else miss)(
        "Correction C: arctis-manager service file present (start idempotency check)"
    )
    result["correction_c_am_present"] = am_present

    # depends-on = pipewire in arctis-manager
    am_deps = services["depends_on"].get("arctis-manager", [])
    has_pw_dep = "pipewire" in am_deps
    (ok if has_pw_dep else miss)(
        "depends-on = pipewire present in arctis-manager",
        f"found: {am_deps}" if am_deps else "no depends-on found",
    )
    result["correction_c_depends_pipewire"] = has_pw_dep

    # pipewire service exists in dinit
    pw_via_dinit = pipewire.get("pipewire_via_dinit", False)
    (ok if pw_via_dinit else miss)(
        "pipewire service exists in dinit (dinitctl status pipewire)",
        "running" if pw_via_dinit else "not detected via dinitctl",
    )
    result["pipewire_via_dinit"] = pw_via_dinit

    return result


# ── main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    print("=" * 60)
    print("  ASM dinit diagnostic")
    print("  Read-only — no changes made to your system")
    print("=" * 60)

    r_init = check_init()
    r_svc = check_services()
    r_pw = check_pipewire()
    r_asm = check_asm()
    r_udev = check_udev()
    r_dbus = check_dbus()
    r_corr = check_corrections(r_svc, r_pw)

    # JSON summary
    summary = {
        "init": r_init.get("pid1_comm", "unknown"),
        "dinitctl_version": r_init.get("dinitctl_version"),
        "services_present": r_svc.get("services_present", {}),
        "services_running": r_svc.get("services_running", {}),
        "services_enabled": r_svc.get("services_enabled", {}),
        "filter_chain_conf_absolute": r_svc.get("filter_chain_conf_absolute"),
        "filter_chain_conf_exists": r_svc.get("filter_chain_conf_exists"),
        "filter_chain_running": r_pw.get("filter_chain_running", False),
        "virtual_sinks_visible": r_pw.get("virtual_sinks_visible", False),
        "udev_rules": r_udev.get("udev_rules", False),
        "hrir": r_asm.get("hrir", False),
        "dbus_ok": r_dbus.get("dbus_ok", False),
        "asm_version": r_asm.get("asm_version"),
        "pipewire_version": r_pw.get("pipewire_version"),
    }

    print("\n" + "=" * 60)
    print("  JSON summary")
    print("=" * 60)
    print(json.dumps(summary, indent=2))

    print()
    print("=" * 60)
    print("  Paste the output above in GitHub issue #25:")
    print("  https://github.com/loteran/Arctis-Sound-Manager/issues/25")
    print("=" * 60)


if __name__ == "__main__":
    main()
