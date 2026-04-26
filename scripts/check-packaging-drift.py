#!/usr/bin/env python3
"""Fail loudly if packaging files have drifted from the source of truth.

What this guards:

1. Version sync — pyproject.toml, aur/PKGBUILD, aur/.SRCINFO and
   arctis-sound-manager.spec must all carry the same version string.
2. Generated files — re-run every generator (udev rules, AppStream
   metainfo releases, debian/changelog) and compare its output to the
   committed file. Diff = drift = CI red.
3. Dependencies — every Python distribution listed in pyproject.toml
   must show up (in any reasonable distro form) in PKGBUILD `depends`,
   the spec's `Requires:` lines, and debian/control's `Depends:`. A dep
   may be marked `bundled` in scripts/packaging_deps.yaml when the
   packager ships it via a wheel or pip install instead.

Exit codes: 0 = clean, 1 = drift detected.

Run locally:  python3 scripts/check-packaging-drift.py
Run in CI:    same — output is grep-friendly.
"""
from __future__ import annotations

import difflib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
PKGBUILD = ROOT / "aur" / "PKGBUILD"
SRCINFO = ROOT / "aur" / ".SRCINFO"
SPEC = ROOT / "arctis-sound-manager.spec"
DEB_CONTROL = ROOT / "debian" / "control"
METAINFO = ROOT / "src" / "arctis_sound_manager" / "desktop" / "com.github.loteran.arctis-sound-manager.metainfo.xml"
DEB_CHANGELOG = ROOT / "debian" / "changelog"

# Map pyproject.toml package name → list of acceptable distro/packaging names.
# A dep is considered satisfied if ANY of the listed names appears in the
# packager's metadata. `bundled` skips the check (the wheel ships the dep).
DEPS_MAP: dict[str, dict[str, list[str] | str]] = {
    "dbus-next":   {"arch": "bundled",         "fedora": "bundled",         "debian": "bundled"},
    "pillow":      {"arch": ["python-pillow"], "fedora": ["python3-pillow"], "debian": ["python3-pil", "python3-pillow"]},
    "pulsectl":    {"arch": "bundled",         "fedora": ["python3-pulsectl"], "debian": ["python3-pulsectl"]},
    "pyside6":     {"arch": ["pyside6"],       "fedora": ["python3-pyside6"], "debian": ["python3-pyside6.qtwidgets", "python3-pyside6", "pyside6"]},
    "pyudev":      {"arch": ["python-pyudev"], "fedora": ["python3-pyudev"], "debian": ["python3-pyudev"]},
    "pyusb":       {"arch": ["python-pyusb"],  "fedora": ["python3-pyusb"],  "debian": ["python3-usb", "python3-pyusb"]},
    "ruamel-yaml": {"arch": ["python-ruamel-yaml"], "fedora": ["python3-ruamel-yaml"], "debian": ["python3-ruamel.yaml", "python3-ruamel-yaml"]},
}

ERRORS: list[str] = []


def fail(msg: str) -> None:
    ERRORS.append(msg)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ── 1. Version sync ─────────────────────────────────────────────────────────

def check_versions() -> None:
    pp = re.search(r'^version\s*=\s*"([^"]+)"', read(PYPROJECT), re.MULTILINE)
    if not pp:
        fail(f"could not parse version in {PYPROJECT}")
        return
    canonical = pp.group(1)

    pb = re.search(r"^pkgver=([^\s]+)", read(PKGBUILD), re.MULTILINE)
    if pb and pb.group(1) != canonical:
        fail(f"version drift: aur/PKGBUILD pkgver={pb.group(1)} ≠ pyproject {canonical}")

    si = re.search(r"^\s*pkgver\s*=\s*(\S+)", read(SRCINFO), re.MULTILINE)
    if si and si.group(1) != canonical:
        fail(f"version drift: aur/.SRCINFO pkgver={si.group(1)} ≠ pyproject {canonical}")

    sp = re.search(r"^Version:\s+(\S+)", read(SPEC), re.MULTILINE)
    if sp and sp.group(1) != canonical:
        fail(f"version drift: arctis-sound-manager.spec Version={sp.group(1)} ≠ pyproject {canonical}")


# ── 2. Generated files — re-run and diff ────────────────────────────────────

def _run(cmd: list[str]) -> str:
    res = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT, check=True)
    return res.stdout


def _diff_or_fail(label: str, expected: str, current: str, regen_cmd: str) -> None:
    if expected == current:
        return
    diff = difflib.unified_diff(
        current.splitlines(keepends=True),
        expected.splitlines(keepends=True),
        fromfile=f"{label} (committed)",
        tofile=f"{label} (regenerated)",
        n=3,
    )
    fail(f"{label} drift — re-run `{regen_cmd}` and commit:\n" + "".join(diff)[:2000])


def check_udev_rules() -> None:
    # The udev rules file isn't checked into the repo (it's generated at
    # package build time), but we can at least confirm the generator runs
    # cleanly and produces non-empty output covering the SteelSeries vendor.
    try:
        out = _run(["python3", "scripts/generate_udev_rules.py"])
    except Exception as e:
        fail(f"udev generator crashed: {e!r}")
        return
    if "1038" not in out or "uaccess" not in out:
        fail("udev generator output missing vendor 0x1038 or uaccess tag")


def check_metainfo() -> None:
    try:
        regen = _run(["python3", "scripts/generate_metainfo_releases.py"])
    except Exception as e:
        fail(f"metainfo generator crashed: {e!r}")
        return
    _diff_or_fail("AppStream metainfo", regen, read(METAINFO),
                  "python3 scripts/generate_metainfo_releases.py --in-place")


def check_debian_changelog() -> None:
    try:
        regen = _run(["python3", "scripts/generate_debian_changelog.py"])
    except Exception as e:
        fail(f"debian changelog generator crashed: {e!r}")
        return
    _diff_or_fail("debian/changelog", regen, read(DEB_CHANGELOG),
                  "python3 scripts/generate_debian_changelog.py --in-place")


# ── 3. Dependency coverage ───────────────────────────────────────────────────

def _pyproject_deps() -> list[str]:
    text = read(PYPROJECT)
    m = re.search(r"^dependencies\s*=\s*\[(.*?)\]", text, re.DOTALL | re.MULTILINE)
    if not m:
        return []
    deps: list[str] = []
    for line in m.group(1).splitlines():
        line = line.strip().rstrip(",").strip('"').strip("'")
        if not line or line.startswith("#"):
            continue
        # strip version specifier
        name = re.split(r"[<>=!~]", line, 1)[0].strip().lower()
        if name:
            deps.append(name)
    return deps


def _haystack(path: Path) -> str:
    return read(path).lower()


def check_dependencies() -> None:
    pkgbuild = _haystack(PKGBUILD)
    spec = _haystack(SPEC)
    control = _haystack(DEB_CONTROL)
    pyproject_deps = _pyproject_deps()

    for dep in pyproject_deps:
        rule = DEPS_MAP.get(dep)
        if rule is None:
            fail(f"pyproject dep {dep!r} has no entry in DEPS_MAP — add one to scripts/check-packaging-drift.py")
            continue
        for distro, hay in (("arch", pkgbuild), ("fedora", spec), ("debian", control)):
            entry = rule[distro]
            if entry == "bundled":
                continue
            assert isinstance(entry, list)
            if not any(name.lower() in hay for name in entry):
                fail(
                    f"dependency drift: pyproject {dep!r} missing from {distro} packaging "
                    f"(expected one of: {entry})"
                )


# ── main ────────────────────────────────────────────────────────────────────

def main() -> int:
    check_versions()
    check_udev_rules()
    check_metainfo()
    check_debian_changelog()
    check_dependencies()

    if not ERRORS:
        print("packaging drift check: OK")
        return 0
    print(f"packaging drift check: {len(ERRORS)} issue(s)\n")
    for e in ERRORS:
        print(f"  - {e}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
