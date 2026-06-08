# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

#!/usr/bin/env python3
"""Generate a correct .SRCINFO from a PKGBUILD.

Parses the PKGBUILD and emits a .SRCINFO that matches what
`makepkg --printsrcinfo` would produce, without requiring makepkg
to be installed (useful in Ubuntu CI).

Usage:
    python3 scripts/generate_srcinfo.py \\
        --pkgbuild aur/PKGBUILD \\
        --version 1.2.3 \\
        --sha256 abc123...
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


def _scalar(text: str, key: str) -> str:
    m = re.search(rf"^{key}=(.+)$", text, re.MULTILINE)
    if not m:
        return ""
    return m.group(1).strip().strip('"').strip("'")


def _array(text: str, key: str) -> list[str]:
    # Parse line-by-line to avoid stopping at ')' inside comments.
    lines = text.splitlines()
    in_block = False
    items: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not in_block:
            if re.match(rf"^{key}=\(", stripped):
                in_block = True
                stripped = re.sub(rf"^{key}=\(", "", stripped)
            else:
                continue
        # Strip inline comments
        stripped = re.sub(r"\s*#.*$", "", stripped)
        items += re.findall(r"'([^']+)'", stripped)
        if ")" in re.sub(r"'[^']*'", "", stripped):  # closing ) outside quoted string
            break
    return items


def generate(pkgbuild_path: Path, version: str, sha256: str) -> str:
    text = pkgbuild_path.read_text()

    pkgver   = version or _scalar(text, "pkgver")
    pkgrel   = _scalar(text, "pkgrel")
    pkgdesc  = _scalar(text, "pkgdesc")
    url      = _scalar(text, "url")
    install  = _scalar(text, "install")
    arch     = _array(text, "arch")
    licenses = _array(text, "license")
    makedeps = _array(text, "makedepends")
    depends  = _array(text, "depends")

    pkg = f"arctis-sound-manager"
    src_file = f"{pkg}-{pkgver}.tar.gz"
    src_url  = (
        f"https://github.com/loteran/Arctis-Sound-Manager"
        f"/releases/download/v{pkgver}/{src_file}"
    )
    source   = f"{src_file}::{src_url}"
    checksum = sha256 if sha256 else "SKIP"

    lines = [f"pkgbase = {pkg}"]
    lines += [f"\tpkgdesc = {pkgdesc}"]
    lines += [f"\tpkgver = {pkgver}"]
    lines += [f"\tpkgrel = {pkgrel}"]
    lines += [f"\turl = {url}"]
    if install:
        lines += [f"\tinstall = {install}"]
    for a in arch:
        lines += [f"\tarch = {a}"]
    for lic in licenses:
        lines += [f"\tlicense = {lic}"]
    for m in makedeps:
        lines += [f"\tmakedepends = {m}"]
    for d in depends:
        lines += [f"\tdepends = {d}"]
    lines += [f"\tsource = {source}"]
    lines += [f"\tsha256sums = {checksum}"]
    lines += [""]
    lines += [f"pkgname = {pkg}"]

    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pkgbuild", required=True, type=Path)
    parser.add_argument("--version", default="")
    parser.add_argument("--sha256", default="SKIP")
    args = parser.parse_args()

    print(generate(args.pkgbuild, args.version, args.sha256), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
