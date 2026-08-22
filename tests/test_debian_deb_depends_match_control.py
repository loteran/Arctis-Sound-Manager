# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""PKG-1 / PKG-5 regression.

Debian is built two ways. `debian/rules` (via `debian/control`'s `Depends:`)
builds the PPA package; `debian/build-deb.sh` builds the .deb CI attaches to
every GitHub Release (`.github/workflows/release.yaml`). The latter used to
hand-write its own `Depends:` heredoc, and it drifted from `debian/control`:
it carried neither `python3-pil`, `python3-babel`, `pulseaudio-utils` nor
`curl`.

The result: `asm-daemon` died with a bare `ModuleNotFoundError: No module
named 'PIL'` on first launch for every Debian/Ubuntu user who installed the
.deb from the releases page (PKG-1), and every non-English locale silently
got wrong plural forms because `python3-babel` was missing too (PKG-5,
`i18n.py` degrades gracefully there instead of crashing).

`debian/build-deb.sh` now derives its `Depends:` from `debian/control` at
build time instead of hand-duplicating it (see its `_control_field`
function) and exposes that derivation through `--print-depends` so it can be
introspected without needing `uv`/`dpkg-deb` installed. This test exercises
that real derivation — not a static grep of the script's source, since the
script no longer contains package names literally — so a regression back to
a hand-written list, or `debian/control` losing a package `build-deb.sh`
still needs, is caught the same way `scripts/check-packaging-drift.py`
catches it (see its `debian_standalone` DEPS_MAP source).
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTROL = ROOT / "debian" / "control"
BUILD_DEB = ROOT / "debian" / "build-deb.sh"

# Packages debian/control's Depends: lists that build-deb.sh legitimately
# omits, because it pip-installs them straight into dist-packages itself
# (see "Bundle dbus-next and pulsectl" in build-deb.sh) rather than
# depending on the system package debian/control expects for the PPA build.
_BUNDLED_BY_BUILD_DEB = {"python3-pulsectl"}


def _control_field(name: str) -> list[str]:
    text = CONTROL.read_text(encoding="utf-8")
    m = re.search(rf"^{name}:[ \t]*(.*?)(?=^\S|\Z)", text, re.MULTILINE | re.DOTALL)
    assert m, f"no {name}: field found in debian/control"
    items = [i.strip() for i in m.group(1).replace("\n", " ").split(",")]
    return [i for i in items if i]


def _primary(dep: str) -> str:
    """First alternative of a dependency: `a | b` -> `a`.

    build-deb.sh keeps `| python3-pip` on the PySide6 packages so the .deb
    stays installable on series whose archive has no python3-pyside6.*
    (Ubuntu 22.04, Debian 12), which the PPA build never has to care about.
    Comparing primaries lets that survive without weakening the check.
    """
    return dep.split("|")[0].strip()


def _print_depends() -> str:
    result = subprocess.run(
        ["bash", str(BUILD_DEB), "--print-depends"],
        capture_output=True, text=True, cwd=ROOT, check=True,
    )
    return result.stdout.strip()


def test_build_deb_depends_matches_debian_control():
    control_depends = {
        d for d in _control_field("Depends")
        if not d.startswith("${")
    } - _BUNDLED_BY_BUILD_DEB

    build_deb_depends = {_primary(d) for d in _print_depends().split(",") if d.strip()}

    missing = sorted(control_depends - build_deb_depends)
    assert not missing, (
        "debian/build-deb.sh's derived Depends: is missing packages "
        f"debian/control carries: {missing} — this is the PKG-1/PKG-5 class "
        "of bug: the .deb on GitHub Releases either can't start or gets "
        "wrong i18n plural forms."
    )

    extra = sorted(build_deb_depends - control_depends)
    assert not extra, (
        "debian/build-deb.sh's derived Depends: carries packages "
        f"debian/control does not: {extra} — either add them to "
        "debian/control (the source of truth) or exclude them explicitly "
        "via _BUNDLED_BY_BUILD_DEB / the script's own `bundled` set."
    )


def test_pkg1_pkg5_specific_packages_are_present():
    """The exact four packages the original hand-written Depends: omitted."""
    depends = _print_depends()
    for pkg in ("python3-pil", "python3-babel", "pulseaudio-utils", "curl"):
        assert pkg in depends, (
            f"{pkg!r} missing from debian/build-deb.sh's Depends: "
            f"(PKG-1/PKG-5 regression) — got: {depends}"
        )




def test_pyside6_keeps_its_pip_fallback():
    """The .deb on the releases page is installed on series the PPA never
    targets. Deriving Depends: from debian/control must not flatten
    `python3-pyside6.* | python3-pip` into a hard dependency the archive
    cannot satisfy — that would trade a daemon that fails to start (PKG-1)
    for a package that fails to install."""
    depends = _print_depends()
    for pkg in ("python3-pyside6.qtcore", "python3-pyside6.qtgui",
                "python3-pyside6.qtwidgets", "python3-pyside6.qtsvg",
                "python3-pyside6.qtnetwork"):
        assert f"{pkg} | python3-pip" in depends, (
            f"{pkg} lost its python3-pip alternative — got: {depends}"
        )
