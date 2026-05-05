#!/usr/bin/env python3
"""Fail loudly if the codebase grew a new external dep without registering
it in `system_deps_checker._build_checks()`.

The runtime self-healing dialog (Phase 4 of ASM_PLAN_DEPS_CHECK) only knows
how to detect and install deps that are listed in the registry. A new
`subprocess.run(["foo", ...])` or `import bar` slipping in unnoticed means
the dialog will silently miss it — exactly the bug class issue #23 was all
about.

What this guards:

1. **Module imports.** Every third-party `import X` / `from X import …`
   in `src/arctis_sound_manager/` must either (a) be listed in
   `pyproject.toml` AND `_build_checks()`, or (b) be in the explicit
   allowlist below (stdlib / first-party).

2. **External binaries.** Every literal-argv `subprocess.run([\"foo\", …])`
   /  `subprocess.Popen([\"foo\"])` / `shutil.which(\"foo\")` call must
   reference a binary that's either checked by `_build_checks()` or
   in the allowlist (always-present coreutils).

Exit codes: 0 = clean, 1 = drift detected.

Run locally:  python3 scripts/check-deps-drift.py
Run in CI:    same — output is grep-friendly.
"""
from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "arctis_sound_manager"
CHECKER = SRC / "system_deps_checker.py"
PYPROJECT = ROOT / "pyproject.toml"

# Modules that don't need a checker entry — Python stdlib + ASM's own pkg.
_STDLIB_OR_FIRST_PARTY = {
    # stdlib (non-exhaustive — extend if a new stdlib import trips the check)
    "os", "sys", "io", "re", "json", "logging", "subprocess", "shutil",
    "pathlib", "typing", "dataclasses", "enum", "functools", "itertools",
    "collections", "asyncio", "threading", "queue", "weakref", "ctypes",
    "importlib", "argparse", "datetime", "time", "tempfile", "fnmatch",
    "contextlib", "uuid", "hashlib", "shlex", "signal", "math", "random",
    "string", "textwrap", "traceback", "abc", "warnings", "atexit",
    "platform", "stat", "errno", "copy", "configparser", "dbm", "select",
    "socket", "struct", "urllib", "http", "ssl", "ipaddress", "base64",
    "zipfile", "tarfile", "gzip", "bz2", "lzma", "csv", "sqlite3",
    "xml", "html", "email", "smtplib", "ftplib", "telnetlib", "wave",
    "audioop", "chunk", "colorsys", "bisect", "heapq", "array", "cmath",
    "decimal", "fractions", "statistics", "operator", "reprlib",
    "pprint", "linecache", "encodings", "codecs", "locale", "gettext",
    "secrets", "hmac", "_thread", "concurrent", "multiprocessing",
    "pickle", "copyreg", "shelve", "marshal", "site", "sysconfig",
    "tkinter", "unittest", "test", "venv", "ensurepip", "zipimport",
    "runpy", "trace", "tracemalloc", "inspect", "ast",
    "__future__",
    # First-party
    "arctis_sound_manager",
}

# Binaries that ARE deps but live in always-present groups (coreutils,
# systemd-udev). These are ASSUMED present on any Linux distro that ASM
# claims to support — declaring them in the checker would be noise.
_ALWAYS_PRESENT_BINARIES = {
    # coreutils
    "true", "false", "cat", "echo", "rm", "ls", "mv", "cp", "ln",
    "mkdir", "rmdir", "id", "whoami", "test", "tee", "head", "tail",
    "grep", "awk", "sed", "sort", "uniq", "find", "xargs", "tr", "which",
    # systemd-bundled (assumed present — required by all systemd distros)
    "udevadm", "systemctl", "loginctl", "journalctl", "machinectl",
    # dinit init-system tool (equivalent of systemctl on Artix Linux / dinit)
    "dinitctl",
    # always-present POSIX shell utilities
    "sh", "bash",
    # Diagnostic-only callsites: try-wrapped, never on a hot path.
    # Listed here rather than in the checker because they are introspection
    # helpers used by `update_checker.py` and `bug_reporter.py` to figure
    # out what install method ASM is using and what's on the system —
    # a missing one degrades the bug report, but doesn't break a feature.
    "lsb_release", "notify-send",
    "dpkg", "rpm", "pip", "pip3", "pipx", "python3",
    # The pipewire daemon binary itself — covered indirectly by the
    # pactl + pw-cli registry checks (those fail if `pipewire` isn't
    # running, which subsumes "is the binary installed").
    "pipewire",
}


# ── Reading the registry ─────────────────────────────────────────────────────


def _registry_imports() -> set[str]:
    """Return the set of importable module names referenced by `_can_import`
    calls inside system_deps_checker._build_checks()."""
    src = CHECKER.read_text()
    return set(re.findall(r"_can_import\(['\"]([^'\"]+)['\"]\)", src))


def _registry_binaries() -> set[str]:
    """Return binaries the checker either calls explicitly via `_which`,
    `subprocess.run([\"<name>\", …])`, or executes through an install_command."""
    src = CHECKER.read_text()
    bins: set[str] = set()
    bins.update(re.findall(r"_which\(['\"]([^'\"]+)['\"]\)", src))
    bins.update(re.findall(
        r"subprocess\.run\(\[['\"]([^'\"]+)['\"]", src,
    ))
    # install_commands argv: first element of every list literal in dicts
    bins.update(re.findall(
        r"install_commands\s*=\s*\{[^}]*\[\"([^\"]+)\"", src, re.DOTALL,
    ))
    return bins


def _pyproject_deps() -> set[str]:
    """Return the import names declared in pyproject.toml's `dependencies =`
    list. The pyproject names are pkg names (e.g. `pyside6`) — we lower-case
    them so we can compare against import names case-insensitively."""
    text = PYPROJECT.read_text()
    block_match = re.search(r'^dependencies\s*=\s*\[([^\]]*)\]', text, re.MULTILINE)
    if not block_match:
        return set()
    out: set[str] = set()
    for line in block_match.group(1).splitlines():
        m = re.search(r'"\s*([a-zA-Z0-9_.\-]+)', line)
        if m:
            out.add(m.group(1).lower())
    return out


# ── Walking src/ ─────────────────────────────────────────────────────────────


def _walk_imports() -> set[tuple[Path, str]]:
    """Return (file, top-level module name) for every import in src/."""
    out: set[tuple[Path, str]] = set()
    for py in SRC.rglob("*.py"):
        try:
            tree = ast.parse(py.read_text())
        except SyntaxError as exc:
            print(f"[deps-drift] SKIP {py.relative_to(ROOT)}: {exc}", file=sys.stderr)
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    out.add((py, alias.name.split(".")[0]))
            elif isinstance(node, ast.ImportFrom):
                if node.level > 0:  # relative import
                    continue
                if node.module:
                    out.add((py, node.module.split(".")[0]))
    return out


# Match `subprocess.run(["foo", …])` and `subprocess.Popen(["foo", …])` and
# `shutil.which("foo")`. We only consider literal first arguments — dynamic
# strings can't be enumerated statically and are out of scope.
_BINARY_PATTERNS = (
    re.compile(r'subprocess\.run\(\s*\[\s*[\'"]([a-zA-Z0-9_.\-/]+)[\'"]'),
    re.compile(r'subprocess\.Popen\(\s*\[\s*[\'"]([a-zA-Z0-9_.\-/]+)[\'"]'),
    re.compile(r'shutil\.which\(\s*[\'"]([a-zA-Z0-9_.\-/]+)[\'"]'),
)


def _walk_binaries() -> set[tuple[Path, str]]:
    out: set[tuple[Path, str]] = set()
    for py in SRC.rglob("*.py"):
        try:
            text = py.read_text()
        except OSError:
            continue
        for pattern in _BINARY_PATTERNS:
            for binary in pattern.findall(text):
                # Strip leading paths — "/usr/bin/foo" → "foo" — so the
                # check matches both absolute and PATH-resolved invocations.
                base = binary.rsplit("/", 1)[-1]
                out.add((py, base))
    return out


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> int:
    registry_imports = _registry_imports()
    registry_binaries = _registry_binaries()
    pyproject_deps = _pyproject_deps()

    drift = []

    # 1. Imports — third-party only (skip stdlib / first-party).
    seen_modules: dict[str, set[Path]] = {}
    for py, mod in _walk_imports():
        if mod in _STDLIB_OR_FIRST_PARTY:
            continue
        seen_modules.setdefault(mod, set()).add(py)

    for mod, files in sorted(seen_modules.items()):
        if mod in registry_imports:
            continue
        # Some Python pkgs have a different import name than their pkg name —
        # PIL = pillow, dbus_next = dbus-next, ruamel.yaml = ruamel-yaml.
        # Hand-roll the equivalents we know about.
        equivs = {
            "PIL": "pillow",
            "dbus_next": "dbus-next",
            "ruamel": "ruamel.yaml",
            "usb": "pyusb",
        }
        pyproj_name = equivs.get(mod, mod).lower()
        if pyproj_name in pyproject_deps:
            files_short = sorted(p.relative_to(ROOT).as_posix() for p in files)
            drift.append(
                f"  IMPORT {mod!r} (pyproject: {pyproj_name!r}) is NOT in "
                f"system_deps_checker._build_checks(). Files: {', '.join(files_short)}"
            )

    # 2. Binaries — flag anything that's not in the registry, not always-present,
    # and looks like a real CLI tool (not a python script invocation we own).
    seen_bins: dict[str, set[Path]] = {}
    for py, binary in _walk_binaries():
        if binary in _ALWAYS_PRESENT_BINARIES:
            continue
        if binary.startswith("asm-"):  # our own entry points
            continue
        # The system_deps_checker file references its own binaries in
        # detect funcs — skip self-references.
        if py == CHECKER:
            continue
        seen_bins.setdefault(binary, set()).add(py)

    for binary, files in sorted(seen_bins.items()):
        if binary in registry_binaries:
            continue
        files_short = sorted(p.relative_to(ROOT).as_posix() for p in files)
        drift.append(
            f"  BINARY {binary!r} called by ASM is NOT in "
            f"system_deps_checker._build_checks(). Files: {', '.join(files_short)}"
        )

    if drift:
        print("[deps-drift] FAIL — the runtime checker is missing entries:", file=sys.stderr)
        for line in drift:
            print(line, file=sys.stderr)
        print(
            "\n[deps-drift] Add a DepCheck for each item to "
            "src/arctis_sound_manager/system_deps_checker.py:_build_checks(), "
            "with the per-distro install_commands. The runtime self-healing "
            "dialog (Phase 4 of ASM_PLAN_DEPS_CHECK) only fixes deps it knows about.",
            file=sys.stderr,
        )
        return 1

    print(
        f"[deps-drift] OK — {len(seen_modules)} third-party imports + "
        f"{len(seen_bins)} external binaries all covered by the checker."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
