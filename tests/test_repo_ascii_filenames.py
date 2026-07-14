# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Guard against non-ASCII filenames re-entering the tree (issue #132).

`bsdtar` fails to set the locale in some minimal/containerized environments
(``bsdtar: Failed to set default locale``), and when that happens it
silently skips extracting any file whose name isn't representable in the
fallback (usually ASCII-only) locale. That bit users installing via the AUR
package, whose release tarball is built with `git archive` / `tar` and
extracted with `bsdtar`. The 4 offending files (HeSuVi "Spatial Sound Card"
Shanghai presets, e.g. ``ssc_hù.wav``) were renamed to pure ASCII
(``ssc_hu.wav``); this test keeps it that way for the whole tree.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _git_tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=_REPO_ROOT,
        capture_output=True,
        check=True,
    )
    # -z: NUL-separated, raw bytes — git's default quoting would otherwise
    # octal-escape non-ASCII bytes (e.g. "ssc_h\303\271.wav"), which hides
    # them from a naive text-based `isascii()` check on the escaped form.
    return [p for p in result.stdout.decode("utf-8").split("\0") if p]


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
@pytest.mark.skipif(not (_REPO_ROOT / ".git").exists(), reason="not a git checkout")
def test_no_tracked_file_has_a_non_ascii_name():
    non_ascii = [p for p in _git_tracked_files() if not p.isascii()]
    assert non_ascii == [], (
        "Non-ASCII filenames break extraction with `bsdtar` when it fails "
        f"to set the locale (issue #132): {non_ascii!r}"
    )
