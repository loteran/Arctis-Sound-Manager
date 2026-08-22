#!/usr/bin/env python3
# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""
diagnose-sonar-eq.py — Where does the Sonar Equalizer chain break?

Answers one question for a user whose EQ presets seem to do nothing (issue
#181, root-caused with measurements in issue #203): where, between a preset
click and the headset's speaker, does the signal chain stop carrying the EQ?

The five things this checks, in the order they can mask each other:

  1. Safe mode          — ASM set the EQ configs aside after a filter-chain
                           crash-loop. Everything below is expected to look
                           broken while this is armed.
  2. filter-chain unit   — which unit file systemd will actually run, and
                           whether it sets LADSPA_PATH (fixed in v1.4.5, but a
                           stale copy in ~/.config/systemd/user/ outranks the
                           packaged one).
  3. LADSPA plugin load  — every `plugin =` reference in the generated confs
                           resolves to a file that exists, cross-checked
                           against the journal for an actual load failure.
                           PipeWire's own behaviour here is version-dependent,
                           so the running PipeWire version is reported too.
  4. Graph presence      — the EQ nodes and the HeSuVi nodes exist, and each
                           Arctis_*_sink_out loopback actually links into its
                           EQ node (an unlinked chain is the #203 permissions
                           case).
  5. Bypass detection    — whether a channel's conf carries real filters or
                           is a flat passthrough, which explains "presets
                           change nothing" outright.

Strictly read-only: no service is restarted, no config is edited, no link is
touched. Every external command is a query (systemctl show/is-active,
journalctl, pw-dump, pw-link -l) — nothing that mutates the running graph.

Works from inside or outside a Distrobox container. Under Distrobox the
filter-chain service actually runs on the HOST — systemctl/pw-dump/journalctl
reach it transparently either way because Distrobox shares the host's D-Bus
session and PipeWire socket (see CHANGELOG.md's #88 entry) — but the
*filesystem* is not shared for system-wide directories (only $HOME is
bind-mounted), so a LADSPA plugin found at an absolute system path from
inside the container may not exist on the host at all. Check 3 flags that
case explicitly and, when `distrobox-host-exec` is available, verifies the
host side directly instead of guessing.

Usage:
    python3 scripts/diagnose-sonar-eq.py

Output: coloured verdicts on stdout, full detail written to a timestamped
file (~/asm-sonar-eq-diag-<timestamp>.txt), and a short paste-into-the-issue
summary at the end.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Best-effort import of ASM's own container-detection helper, so this script
# does not carry a second hand-written copy that can drift from the one
# bug_reporter.py already ships and tests (the two-build-paths lesson).
# Falls back to "unknown" if the package is not importable — this script must
# still run standalone, the way diagnose-usb-access.sh does.
# ---------------------------------------------------------------------------
_REPO_SRC = Path(__file__).resolve().parent.parent / "src"
if _REPO_SRC.is_dir():
    sys.path.insert(0, str(_REPO_SRC))

try:
    from arctis_sound_manager.bug_reporter import _detect_container_env
except Exception:
    _detect_container_env = None


# ===========================================================================
# Pure parsing / classification logic — unit-tested in
# tests/test_diagnose_sonar_eq.py. Nothing below this point touches disk,
# the network, or a subprocess.
# ===========================================================================

_PLUGIN_RE = re.compile(r"plugin\s*=\s*(\S+)")
_LABEL_RE = re.compile(r"label\s*=\s*(\S+)")
_UNIT_VERSION_RE = re.compile(r"^\s*#\s*ASM-UNIT-VERSION:\s*(\d+)\s*$", re.MULTILINE)


def extract_ladspa_plugin_refs(conf_text: str) -> list[str]:
    """All ``plugin = <ref>`` tokens in a generated filter-chain conf.

    Scans the whole file rather than requiring ``type = ladspa`` on the same
    line. Several node kinds (DeepFilterNet, RNNoise, the compressor, the
    noise gate — see sonar_to_pipewire.py's noise-cancel/-gate/compressor
    blocks) put ``type = ladspa`` and ``plugin =`` on separate lines of the
    same node entry; a same-line-only scan (as ``_conf_has_bare_ladspa`` in
    sonar_to_pipewire.py does, for a narrower purpose) would silently miss
    those. ``plugin =`` never appears outside a LADSPA node entry in these
    generated confs, so a whole-file scan is safe.
    """
    return _PLUGIN_RE.findall(conf_text)


def extract_node_labels(conf_text: str) -> list[str]:
    """All ``label = <name>`` tokens — one per filter-graph node.

    Only node entries carry a ``label =`` field in these confs (capture.props
    / playback.props never do), so a whole-file scan is equivalent to, and
    simpler than, isolating the ``nodes = [ ... ]`` block by hand."""
    return _LABEL_RE.findall(conf_text)


def classify_filter_nodes(labels: list[str]) -> tuple[int, int]:
    """Return ``(real_filter_count, passthrough_count)`` for a label list.

    A ``copy``/``copy_L``/``copy_R`` node is the passthrough _bypass_conf()
    writes; every other label (bq_peaking, bq_lowshelf, bq_highshelf, or a
    LADSPA label like ``plate``/``sc4m``) is a real processing node, even if
    its gain happens to be 0 — a flat EQ the user set on purpose still has
    real filter nodes, only a bypass conf has none."""
    passthrough = sum(1 for label in labels if label in ("copy", "copy_L", "copy_R"))
    return len(labels) - passthrough, passthrough


def is_bypass_conf(conf_text: str) -> bool:
    """True if every node in *conf_text* is a copy/passthrough node."""
    labels = extract_node_labels(conf_text)
    return bool(labels) and all(label in ("copy", "copy_L", "copy_R") for label in labels)


def ladspa_ref_is_absolute(ref: str) -> bool:
    return ref.startswith("/")


def parse_systemctl_show(output: str) -> dict[str, str]:
    """Parse ``systemctl show -p K1 -p K2 ...`` ``Key=Value`` output.

    One property per line; a property with no value still emits ``Key=``,
    which is kept as an empty string rather than dropped, so callers can
    still tell "the unit exists but the property is unset" from "no output
    at all" (a missing/unknown unit)."""
    result: dict[str, str] = {}
    for line in output.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            result[key] = value
    return result


def extract_ladspa_path_from_environment(env_value: str) -> Optional[str]:
    """Pull ``LADSPA_PATH`` out of a systemd ``Environment=`` show value.

    ``systemctl show -p Environment`` reports every ``Environment=``
    assignment across the unit and its drop-ins on one space-separated line
    (e.g. ``LADSPA_PATH=/a:/b MALLOC_ARENA_MAX=1``). A value can contain
    ``:`` (it is a search path) but never a literal space — systemd quotes
    values that do and this diagnostic does not need to unquote them, since
    LADSPA_PATH itself is never one of those — so splitting on whitespace
    between assignments is safe."""
    if not env_value:
        return None
    for token in env_value.split():
        if token.startswith("LADSPA_PATH="):
            return token.split("=", 1)[1]
    return None


def describe_filter_chain_unit(show_props: dict[str, str], home: str) -> dict:
    """Summarise a parsed ``systemctl show`` result for filter-chain.service.

    *home* is the value of ``$HOME`` to compare ``FragmentPath`` against, so
    a unit copied into ``~/.config/systemd/user`` (which outranks the
    packaged one, and can be stale) is distinguished from the packaged or
    distro-shipped copy."""
    fragment_path = show_props.get("FragmentPath", "") or ""
    drop_ins_raw = show_props.get("DropInPaths", "") or ""
    drop_ins = [p for p in drop_ins_raw.split() if p]
    ladspa_path = extract_ladspa_path_from_environment(show_props.get("Environment", ""))
    return {
        "fragment_path": fragment_path or "(unit not found)",
        "found": bool(fragment_path),
        "drop_in_paths": drop_ins,
        "sets_ladspa_path": ladspa_path is not None,
        "ladspa_path": ladspa_path,
        "is_home_copy": bool(fragment_path) and fragment_path.startswith(home),
        "active_state": show_props.get("ActiveState", "unknown"),
        "load_state": show_props.get("LoadState", "unknown"),
    }


def extract_unit_version_marker(unit_text: str) -> Optional[int]:
    """The ``# ASM-UNIT-VERSION: N`` integer from a filter-chain.service copy
    ASM wrote, or ``None`` if the file has no marker (pre-migration, or not
    an ASM-managed copy at all)."""
    match = _UNIT_VERSION_RE.search(unit_text)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


# ===========================================================================
# Reporting: coloured stdout verdicts + full detail to a report file.
# ===========================================================================

PASS, WARN, FAIL, SKIP = "PASS", "WARN", "FAIL", "SKIP"
_SYMBOLS = {
    PASS: ("32", "[PASS]"),
    WARN: ("33", "[WARN]"),
    FAIL: ("31", "[FAIL]"),
    SKIP: ("90", "[SKIP]"),
}


class Reporter:
    def __init__(self) -> None:
        self.use_color = sys.stdout.isatty() and "NO_COLOR" not in os.environ
        self._file_lines: list[str] = []
        self.counts = {PASS: 0, WARN: 0, FAIL: 0, SKIP: 0}
        self.verdicts: list[tuple[str, str, str]] = []  # (level, section, label)
        self._section = ""

    def _c(self, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.use_color else text

    def header(self, text: str) -> None:
        self._section = text
        print(f"\n{self._c('1', f'=== {text} ===')}")
        self._file_lines.append(f"\n=== {text} ===")

    def note(self, text: str = "") -> None:
        print(f"  {text}")
        self._file_lines.append(f"  {text}")

    def verdict(self, level: str, label: str, detail: str = "") -> None:
        self.counts[level] += 1
        self.verdicts.append((level, self._section, label))
        color, tag = _SYMBOLS[level]
        print(f"  {self._c(color, tag)} {label}")
        self._file_lines.append(f"  {tag} {label}")
        for line in detail.splitlines():
            print(f"        {line}")
            self._file_lines.append(f"        {line}")

    def detail_only(self, text: str) -> None:
        """Goes to the report file only — raw command output, full listings."""
        self._file_lines.append(text)

    def write_report(self, path: Path, header_text: str) -> None:
        path.write_text(header_text + "\n".join(self._file_lines) + "\n")


# ===========================================================================
# Live probing helpers. All read-only.
# ===========================================================================

def have(tool: str) -> bool:
    return shutil.which(tool) is not None


def run(cmd: list[str], timeout: float = 6.0) -> Optional[subprocess.CompletedProcess]:
    if not have(cmd[0]):
        return None
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception:
        return None


def systemctl_show(unit: str, props: list[str]) -> Optional[dict[str, str]]:
    args = ["systemctl", "--user", "show", unit]
    for p in props:
        args += ["-p", p]
    result = run(args)
    if result is None:
        return None
    return parse_systemctl_show(result.stdout)


def pw_dump() -> Optional[list[dict]]:
    result = run(["pw-dump"], timeout=10.0)
    if result is None or result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except (ValueError, TypeError):
        return None


def container_env() -> str:
    if _detect_container_env is not None:
        try:
            return _detect_container_env()
        except Exception:
            pass
    # Minimal inline fallback mirroring bug_reporter._detect_container_env,
    # used only when the package is not importable at all.
    if os.environ.get("FLATPAK_ID"):
        return f"flatpak (FLATPAK_ID={os.environ['FLATPAK_ID']})"
    if os.environ.get("SNAP"):
        return f"snap (SNAP={os.environ['SNAP']})"
    c = os.environ.get("container", "")
    if c == "distrobox" or os.environ.get("DISTROBOX_ENTER_PATH") or os.environ.get("CONTAINER_ID"):
        return f"distrobox (container={c or '?'})"
    if c:
        return f"container ({c})"
    if Path("/.dockerenv").exists():
        return "docker"
    return "native"


# ===========================================================================
# The five checks.
# ===========================================================================

_SAFE_MODE_MARKER = Path.home() / ".config" / "arctis_manager" / "filter_chain_safe_mode.json"
_CONF_DIR = Path.home() / ".config" / "pipewire" / "filter-chain.conf.d"

_CHANNEL_CONF = {
    "game": "sonar-game-eq.conf",
    "chat": "sonar-chat-eq.conf",
    "media": "sonar-media-eq.conf",
}
_CHANNEL_EQ_NODE = {
    "game": "effect_input.sonar-game-eq",
    "chat": "effect_input.sonar-chat-eq",
    "media": "effect_input.sonar-media-eq",
}
_CHANNEL_SINK_OUT = {
    "game": "Arctis_Game_sink_out",
    "chat": "Arctis_Chat_sink_out",
    "media": "Arctis_Media_sink_out",
}
_HESUVI_CONF = {
    "game": "sink-virtual-surround-7.1-hesuvi.conf",
    "media": "sink-virtual-surround-7.1-hesuvi-media.conf",
}
_HESUVI_NODE = {
    "game": "effect_input.virtual-surround-7.1-hesuvi",
    "media": "effect_input.virtual-surround-7.1-hesuvi-media",
}


def check_safe_mode(r: Reporter) -> bool:
    """Check 1. Returns True if safe mode is armed."""
    r.header("1. Safe mode")
    if not _SAFE_MODE_MARKER.exists():
        r.verdict(PASS, "Safe mode is not armed", f"marker absent: {_SAFE_MODE_MARKER}")
        return False

    try:
        data = json.loads(_SAFE_MODE_MARKER.read_text())
    except (OSError, ValueError):
        data = {}
    r.verdict(
        FAIL,
        "Safe mode IS armed — the EQ is deliberately disabled",
        f"marker: {_SAFE_MODE_MARKER}\n"
        f"armed since: {data.get('timestamp', 'unknown')}\n"
        f"reason: {data.get('reason', 'unknown')}\n"
        f"recorded ASM version: {data.get('asm_version', 'unknown')}, "
        f"PipeWire version: {data.get('pipewire_version', 'unknown')}\n"
        "This alone fully explains \"presets change nothing\": ASM moved the "
        "generated EQ configs aside after the filter-chain crash-looped and is "
        "running a flat passthrough on purpose. Every check below will look "
        "broken (or already-bypassed) as a direct consequence of this — it is "
        "not a second, independent problem.\n"
        "Next step: open the ASM GUI's Sonar page and use \"Re-enable EQ\" once "
        "you believe the crash cause is fixed (an ASM or PipeWire update since "
        "it armed is the usual trigger for it being worth retrying).",
    )
    return True


def resolve_filter_chain_unit(r: Reporter) -> Optional[dict]:
    """Check 2. Returns the describe_filter_chain_unit() summary, or None."""
    r.header("2. filter-chain unit / LADSPA_PATH")

    if not have("systemctl"):
        dinit_unit = Path.home() / ".config" / "dinit.d" / "pipewire-filter-chain"
        if dinit_unit.exists():
            r.verdict(
                SKIP,
                "systemd not found (dinit init) — LADSPA_PATH check does not apply",
                f"dinit service file: {dinit_unit}\n"
                "ASM's dinit template sets no LADSPA_PATH at all (no drop-in "
                "mechanism on dinit) — if a plugin below is referenced by a bare "
                "name or lives outside the built-in search dirs, that is the "
                "likely reason it fails to load. Its own log is at "
                "/tmp/pipewire-filter-chain.log (see the template in "
                "arctis_sound_manager/scripts/setup.py).",
            )
        else:
            r.verdict(SKIP, "systemd not found and no dinit filter-chain service either",
                       "cannot determine which init system runs the filter-chain — skipping.")
        return None

    props = ["FragmentPath", "DropInPaths", "Environment", "ActiveState", "LoadState"]
    chosen_name = None
    chosen_show: Optional[dict[str, str]] = None
    for name in ("filter-chain.service", "pipewire-filter-chain.service"):
        show = systemctl_show(name, props)
        if show and show.get("LoadState") not in (None, "", "not-found"):
            chosen_name = name
            chosen_show = show
            break
    if chosen_show is None:
        r.verdict(FAIL, "No filter-chain unit found under either known name",
                   "checked: filter-chain.service, pipewire-filter-chain.service\n"
                   "Without this unit nothing runs the EQ chain at all — that alone "
                   "explains total silence on Game/Chat/Media. Run `asm-setup` (or "
                   "reinstall) to have ASM write it.")
        return None

    info = describe_filter_chain_unit(chosen_show, str(Path.home()))
    r.note(f"resolved unit: {chosen_name}")
    r.note(f"in force: {info['fragment_path']}")
    if info["drop_in_paths"]:
        r.note(f"drop-ins applied: {', '.join(info['drop_in_paths'])}")
    r.note(f"state: load={info['load_state']} active={info['active_state']}")

    detail_lines = []
    if info["is_home_copy"]:
        detail_lines.append(
            "This is a copy under ~/.config/systemd/user — it outranks any "
            "packaged unit. ASM migrates a stale copy automatically the next "
            "time it runs setup/the daemon starts, but if that hasn't happened "
            "yet since upgrading, this copy can predate the LADSPA_PATH fix "
            "(v1.4.5)."
        )
        try:
            unit_text = Path(info["fragment_path"]).read_text()
            marker = extract_unit_version_marker(unit_text)
            detail_lines.append(
                f"ASM-UNIT-VERSION marker: {marker if marker is not None else 'absent (pre-migration)'}"
            )
        except OSError as exc:
            detail_lines.append(f"could not read the unit file to check its version marker: {exc}")

    if info["sets_ladspa_path"]:
        r.verdict(PASS, f"{chosen_name} sets LADSPA_PATH", "\n".join(detail_lines +
                   [f"LADSPA_PATH={info['ladspa_path']}"]))
    else:
        detail_lines.append(
            "No LADSPA_PATH set. This is not automatically a bug: PipeWire "
            "≥~1.6.8 loads an absolute plugin path directly regardless of "
            "LADSPA_PATH, so a system whose confs only reference absolute "
            "paths to files that exist can still work fine without it. It IS "
            "the #203 defect on older PipeWire (1.6.4 there), where an "
            "absolute path gets resolved as a bare *name* against the "
            "built-in search dirs instead and silently fails. Check 3 below "
            "runs the actual test (the journal + whether the node exists) "
            "rather than guessing from the version alone."
        )
        r.verdict(WARN, f"{chosen_name} does not set LADSPA_PATH", "\n".join(detail_lines))

    return info


def _local_path_exists(path_str: str) -> bool:
    try:
        return Path(path_str).exists()
    except OSError:
        return False


def _is_under_home(path_str: str) -> bool:
    try:
        Path(path_str).relative_to(Path.home())
        return True
    except ValueError:
        return False


def verify_plugin_path(path_str: str, cenv: str) -> str:
    """Check a LADSPA plugin path, aware that under Distrobox only $HOME is
    shared with the host — a system-wide path existing in the container
    proves nothing about whether the host (where filter-chain actually runs)
    has it too."""
    local_exists = _local_path_exists(path_str)
    if _is_under_home(path_str) or not cenv.startswith("distrobox"):
        return "exists" if local_exists else "MISSING"

    host_exec = shutil.which("distrobox-host-exec")
    if not host_exec:
        return (
            f"unknown — running inside Distrobox, this is a system-wide path, and "
            f"distrobox-host-exec is not available to check the host directly. "
            f"(local container check: {'present' if local_exists else 'ABSENT'} — "
            f"not authoritative, the host filesystem is separate for this path)"
        )
    result = run([host_exec, "test", "-e", path_str])
    if result is None:
        return "unknown (distrobox-host-exec call failed)"
    return "exists on host" if result.returncode == 0 else "MISSING on host"


def check_ladspa_plugins(r: Reporter, cenv: str, unit_info: Optional[dict]) -> None:
    """Check 3."""
    r.header("3. LADSPA plugin load")

    pw_version = "unknown"
    dump = pw_dump()
    if dump is not None:
        for obj in dump:
            if obj.get("id") == 0 and obj.get("type") == "PipeWire:Interface:Core":
                pw_version = (obj.get("info") or {}).get("version", "unknown")
                break
    client_version = "unknown"
    pwv = run(["pw-cli", "--version"])
    if pwv is not None:
        for line in pwv.stdout.splitlines():
            if "libpipewire" in line and "Compiled" in line:
                client_version = line.split()[-1]
                break
    r.note(f"running PipeWire (server/daemon, governs plugin loading): {pw_version}")
    if cenv.startswith("distrobox") and client_version != pw_version:
        r.note(f"this tool's own client library: {client_version} — "
               f"differs from the server, exactly the Distrobox skew #203 measured "
               f"(host 1.6.4 / container 1.6.8); the server figure above is the one "
               f"that matters here")

    if not _CONF_DIR.is_dir():
        r.verdict(FAIL, f"conf directory does not exist: {_CONF_DIR}",
                   "No generated confs at all — nothing for filter-chain to load.")
        return

    conf_files = sorted(p for p in _CONF_DIR.glob("*.conf"))
    if not conf_files:
        r.verdict(FAIL, f"no .conf files in {_CONF_DIR}", "")
        return

    any_plugin_refs = False
    any_missing = False
    for conf_path in conf_files:
        try:
            text = conf_path.read_text()
        except OSError as exc:
            r.verdict(WARN, f"{conf_path.name}: could not read ({exc})", "")
            continue
        refs = extract_ladspa_plugin_refs(text)
        if not refs:
            continue
        any_plugin_refs = True
        for ref in refs:
            if ladspa_ref_is_absolute(ref):
                status = verify_plugin_path(ref, cenv)
            else:
                # Bare name: PipeWire resolves it against LADSPA_PATH. Search
                # the same places ASM itself would stage/find a plugin.
                search_dirs = [Path.home() / ".ladspa", Path("/usr/lib64/ladspa"),
                                Path("/usr/lib/ladspa"), Path("/usr/lib")]
                ladspa_path = (unit_info or {}).get("ladspa_path") or ""
                search_dirs += [Path(p) for p in ladspa_path.split(":") if p]
                found = any((d / f"{ref}.so").exists() for d in search_dirs)
                status = "resolved (bare name)" if found else "NOT FOUND in any search dir (bare name)"
            marker = "        " if "MISSING" not in status and "NOT FOUND" not in status else "    !!! "
            r.note(f"{marker}{conf_path.name}: plugin = {ref}  ->  {status}")
            if "MISSING" in status or "NOT FOUND" in status:
                any_missing = True

    journal_hits = ""
    journal_checked = False
    if have("journalctl"):
        journal_checked = True
        jres = run(["journalctl", "--user", "-u", "filter-chain.service", "-b", "--no-pager"], timeout=10.0)
        if jres is not None:
            hits = [ln for ln in jres.stdout.splitlines() if "can't load" in ln.lower()]
            journal_hits = "\n".join(hits[-10:])
            r.detail_only("--- journalctl --user -u filter-chain.service -b (full) ---")
            r.detail_only(jres.stdout)

    if any_missing:
        r.verdict(FAIL, "at least one LADSPA plugin reference cannot be resolved",
                   "See the marked (!!!) lines above. A module with `flags = [ nofail ]` "
                   "drops silently when a plugin fails to dlopen — the rest of the chain "
                   "(and often the whole HeSuVi node) never appears, and there is no "
                   "error the GUI can show you.\n"
                   "Next step: install the missing plugin package on the machine "
                   "filter-chain actually runs on (the HOST if you are on Distrobox), "
                   "or clear ~/.ladspa and let ASM re-stage it.")
    elif any_plugin_refs and journal_checked and journal_hits:
        r.verdict(FAIL, "every referenced plugin file exists, but the journal shows a load failure anyway",
                   journal_hits + "\n"
                   "This is the exact #203 shape: an absolute path that exists locally "
                   "but is still being resolved as a bare *name* by an older PipeWire "
                   "(their case: 1.6.4) because LADSPA_PATH is unset (see check 2). "
                   "Next step: apply the systemd drop-in from check 2, or upgrade "
                   "PipeWire.")
    elif any_plugin_refs:
        detail = "every plugin reference resolved to an existing file"
        if journal_checked:
            detail += "; no \"can't load\" lines in this boot's filter-chain journal"
        else:
            detail += "; journalctl not available, could not cross-check the log"
        r.verdict(PASS, "LADSPA plugins referenced by the generated confs all resolve", detail)
    else:
        r.verdict(SKIP, "no LADSPA plugin is referenced by any generated conf",
                   "Nothing to check here — the HeSuVi convolver on this system uses "
                   "PipeWire's built-in `convolver`/`copy` nodes rather than a LADSPA "
                   ".so (that varies by ASM version and which effects are enabled).")


def _node_names_by_id(dump: list[dict]) -> dict[int, str]:
    names = {}
    for obj in dump:
        if obj.get("type") == "PipeWire:Interface:Node":
            props = (obj.get("info") or {}).get("props") or {}
            name = props.get("node.name")
            if name:
                names[obj["id"]] = name
    return names


def _links(dump: list[dict]) -> list[tuple[int, int]]:
    out = []
    for obj in dump:
        if obj.get("type") == "PipeWire:Interface:Link":
            info = obj.get("info") or {}
            out_id, in_id = info.get("output-node-id"), info.get("input-node-id")
            if out_id is not None and in_id is not None:
                out.append((out_id, in_id))
    return out


def check_graph(r: Reporter) -> None:
    """Check 4."""
    r.header("4. Graph presence: EQ nodes, HeSuVi nodes, loopback links")

    dump = pw_dump()
    if dump is None:
        r.verdict(SKIP, "pw-dump not available or PipeWire not reachable",
                   "Cannot inspect the live graph — is PipeWire running?")
        return

    names = _node_names_by_id(dump)
    name_to_id = {v: k for k, v in names.items()}
    link_pairs = _links(dump)
    link_name_pairs = {(names.get(o), names.get(i)) for o, i in link_pairs}

    r.detail_only("--- ASM-related nodes in the live graph ---")
    for nid, name in sorted(names.items(), key=lambda kv: kv[1]):
        if ("Arctis_" in name or "sonar" in name or "hesuvi" in name
                or name.startswith(("effect_input.", "effect_output."))):
            r.detail_only(f"  {nid}\t{name}")

    for channel, conf_name in _CHANNEL_CONF.items():
        conf_exists = (_CONF_DIR / conf_name).exists()
        eq_node = _CHANNEL_EQ_NODE[channel]
        sink_out = _CHANNEL_SINK_OUT[channel]
        if not conf_exists:
            r.verdict(SKIP, f"{channel}: no {conf_name} on disk — channel not configured, skipping",
                       "")
            continue

        eq_present = eq_node in name_to_id
        sink_present = sink_out in name_to_id
        if not eq_present:
            r.verdict(FAIL, f"{channel}: EQ node '{eq_node}' is absent from the graph",
                       "The conf on disk exists but filter-chain never instantiated the "
                       "node — check 3's plugin-load result for why. Every EQ preset click "
                       "reaches a node that is not there, which is a complete explanation "
                       "for \"nothing changes\" on this channel.")
            continue
        if not sink_present:
            r.verdict(FAIL, f"{channel}: loopback sink '{sink_out}' is absent from the graph",
                       "ASM's own loopback process for this channel is not running — the "
                       "EQ node exists but nothing feeds it. Check whether pw-loopback "
                       "processes are running (`ps -ef | grep pw-loopback`) and the "
                       "arctis-manager journal for the loopback watchdog.")
            continue

        linked = (sink_out, eq_node) in link_name_pairs
        if linked:
            r.verdict(PASS, f"{channel}: {sink_out} -> {eq_node} is linked", "")
        else:
            r.verdict(FAIL, f"{channel}: {sink_out} exists but is NOT linked into {eq_node}",
                       "Both nodes are present, but the link between them is missing — this "
                       "is the #203 permissions case: PipeWire can refuse a cross-process "
                       "link into an Audio/Sink/Internal node on some Distrobox/SteamOS "
                       "sessions. ASM's own watchdog should recreate this within ~16s and "
                       "eventually mark the channel for a pickable-class fallback "
                       "(link_permission_fallback.json) — if it has been longer than that "
                       "and the link is still missing, that fallback has not (yet) "
                       "triggered on this machine.\n"
                       "Audio genuinely cannot reach this channel's EQ (or beyond) while "
                       "this is the case — this fully explains flat/silent audio on it.")

    for hesuvi_channel, conf_name in _HESUVI_CONF.items():
        conf_path = _CONF_DIR / conf_name
        node_name = _HESUVI_NODE[hesuvi_channel]
        if not conf_path.exists():
            r.verdict(SKIP, f"hesuvi ({hesuvi_channel}): no {conf_name} on disk, skipping", "")
            continue
        if node_name in name_to_id:
            r.verdict(PASS, f"hesuvi ({hesuvi_channel}): '{node_name}' is present", "")
        else:
            r.verdict(FAIL, f"hesuvi ({hesuvi_channel}): '{node_name}' is absent from the graph",
                       "The conf exists on disk but the node never instantiated — almost "
                       "always a plugin load failure (see check 3); the module carries "
                       "`nofail` so this fails with no visible error otherwise.")


def check_filter_counts(r: Reporter) -> None:
    """Check 5."""
    r.header("5. Bypass detection: does each conf carry real filters?")

    if not _CONF_DIR.is_dir():
        r.verdict(SKIP, f"{_CONF_DIR} does not exist", "")
        return

    any_bypass = []
    any_active = []
    for channel, conf_name in _CHANNEL_CONF.items():
        conf_path = _CONF_DIR / conf_name
        if not conf_path.exists():
            r.verdict(SKIP, f"{channel}: no {conf_name} on disk, skipping", "")
            continue
        try:
            text = conf_path.read_text()
        except OSError as exc:
            r.verdict(WARN, f"{channel}: could not read {conf_name} ({exc})", "")
            continue

        labels = extract_node_labels(text)
        real, passthrough = classify_filter_nodes(labels)
        if is_bypass_conf(text):
            any_bypass.append(channel)
            r.verdict(
                WARN,
                f"{channel}: {conf_name} is a BYPASS (passthrough) — {passthrough} copy node(s), 0 filters",
                "This is not necessarily wrong — it is also what a channel with every "
                "slider still at its default looks like. But it completely explains "
                "\"I clicked presets and nothing changed\" on this specific channel: no "
                "preset's gains are in this file at all for PipeWire to apply. If you "
                "HAVE selected a non-flat preset on this channel and it still measures "
                "flat, the click is not reaching the conf — check the daemon's D-Bus "
                "log for the Apply call, not the audio graph.",
            )
        else:
            any_active.append(channel)
            r.verdict(PASS, f"{channel}: {conf_name} carries {real} real filter node(s) "
                             f"({len(labels)} total)", "")

    if any_bypass and not any_active:
        r.note("")
        r.note("All configured channels are currently bypass confs — if you have applied "
               "an EQ preset on any of them, this is the single most useful fact in this "
               "report.")


# ===========================================================================
# Main
# ===========================================================================

def main() -> int:
    r = Reporter()
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    report_path = Path.home() / f"asm-sonar-eq-diag-{timestamp}.txt"

    cenv = container_env()

    print(r._c("1", "Arctis Sound Manager — Sonar EQ chain diagnostic"))
    print(f"date: {datetime.now().isoformat(timespec='seconds')}")
    print(f"container environment: {cenv}")
    if cenv.startswith("distrobox"):
        print("  (running inside Distrobox — systemctl/pw-dump/journalctl below reach")
        print("   the HOST transparently via the shared D-Bus session and PipeWire")
        print("   socket; only system-wide filesystem paths differ from the host, and")
        print("   are flagged individually where that matters — see check 3)")

    header_text = (
        "Arctis Sound Manager — Sonar EQ chain diagnostic\n"
        f"date: {datetime.now().isoformat(timespec='seconds')}\n"
        f"container environment: {cenv}\n"
        f"home: {Path.home()}\n\n"
    )

    safe_mode_armed = check_safe_mode(r)
    unit_info = resolve_filter_chain_unit(r)
    check_ladspa_plugins(r, cenv, unit_info)
    check_graph(r)
    check_filter_counts(r)

    # ------------------------------------------------------------------
    # Verdict + paste-into-issue summary
    # ------------------------------------------------------------------
    r.header("Verdict")
    total_fail = r.counts[FAIL]
    total_warn = r.counts[WARN]
    if safe_mode_armed:
        r.note("Safe mode explains the symptom on its own — re-enable EQ and re-run this "
               "script if the problem persists afterwards.")
    elif total_fail == 0:
        warn_note = (f" ({total_warn} informational WARN item(s) above are not failures — "
                     f"see their detail)" if total_warn else "")
        r.note(f"No check failed{warn_note}. This rules out: safe mode, a "
               "stale/misconfigured filter-chain unit, a LADSPA plugin failing to load, a "
               "missing or unlinked EQ node, and an accidental bypass conf. If the EQ still "
               "audibly does nothing with everything above green, the break is downstream "
               "of this chain (the HeSuVi convolver's own routing, the physical output "
               "device, or the headset itself) rather than in the EQ chain this script "
               "checks — worth attaching this report to the issue as evidence of what has "
               "been ruled out.")
    else:
        first_fail = next((label for level, _, label in r.verdicts if level == FAIL), None)
        r.note(f"{total_fail} check(s) failed, {total_warn} warning(s). First failure: {first_fail}")
        r.note("That is very likely where the chain actually breaks — checks after it may "
               "show downstream symptoms of the same root cause rather than independent "
               "problems.")

    r.write_report(report_path, header_text)

    print(f"\n{r._c('1', 'Full report written to:')} {report_path}")
    print("Attach that file to the issue, or paste the summary below directly.\n")

    print(r._c("1", "----- paste-into-issue summary -----"))
    print(f"ASM Sonar EQ diagnostic — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"container: {cenv}")
    for level, section, label in r.verdicts:
        print(f"  [{level}] {section}: {label}")
    if safe_mode_armed:
        print("Verdict: safe mode is armed — that alone explains it.")
    elif total_fail == 0:
        print("Verdict: no check failed — the EQ chain itself looks intact.")
    else:
        print(f"Verdict: first failure — {first_fail}")
    print(r._c("1", "-------------------------------------"))

    return 0


if __name__ == "__main__":
    sys.exit(main())
