# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
test_pipeable_scripts.py: scripts we tell users to run straight from curl.

Discussion #190: the uninstaller was handed to a user twice as

    curl -fsSL .../scripts/uninstall.sh | bash -s -- --all --purge

and it died on its second line of code, before printing anything, with

    bash: line 24: BASH_SOURCE[0]: unbound variable
    bash: line 24: cd: null directory

Two independent faults, both invisible to anyone testing from a checkout:

  1. `BASH_SOURCE` is unset when bash reads a script from stdin. Under
     `set -u` that alone aborts the script, and since bash 5.3 the `cd ""`
     that follows fails as well, so on Fedora 42+/Nobara/Arch the one-liner
     did nothing at all, while on bash 5.2 and older it merely computed a
     wrong directory and carried on.

  2. With the script itself arriving on stdin, `read` sees EOF immediately:
     every confirmation answered "no" on its own, the uninstaller removed
     nothing, and it still printed "==> Done". That is the reading of "no
     matter what i do, the program still lingers after reboot".

`clean-reinstall.sh` already had this right (`read -r "$@" </dev/tty`), which
is why these checks are written against every pipeable script rather than the
one that happened to break.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"

#: A script advertising `curl … | script.sh | bash` (a pipe, not `bash <(…)`,
#: which keeps both BASH_SOURCE and stdin intact) must survive having no file
#: of its own and no readable stdin.
_PIPE_USAGE_RE = re.compile(r"curl[^\n|]*\|\s*bash", re.IGNORECASE)

#: `${BASH_SOURCE[0]}` with no `:-` default: the form that aborts under `set -u`.
_BARE_BASH_SOURCE_RE = re.compile(r"\$\{BASH_SOURCE\[0\]\}")

#: A bare `read` that is not redirected away from stdin on the same line.
_BARE_READ_RE = re.compile(r"^\s*read\s+(?![^\n]*<\s*[\"']?[/$])", re.MULTILINE)


def _shell_scripts() -> list[Path]:
    return sorted(SCRIPTS_DIR.rglob("*.sh"))


def _pipeable_scripts() -> list[Path]:
    """Scripts whose own header documents a `curl … | bash` invocation."""
    out = []
    for p in _shell_scripts():
        header = "\n".join(p.read_text(encoding="utf-8").splitlines()[:40])
        if _PIPE_USAGE_RE.search(header):
            out.append(p)
    return out


def test_at_least_one_pipeable_script_is_detected():
    """Guard against the detection above silently matching nothing, which would
    turn every check in this file into a green no-op."""
    found = _pipeable_scripts()
    assert found, "no pipeable script detected, the header regex stopped matching"
    assert any(p.name == "uninstall.sh" for p in found), \
        f"uninstall.sh no longer advertises a curl|bash usage: {[p.name for p in found]}"


@pytest.mark.parametrize("script", _pipeable_scripts(), ids=lambda p: p.name)
def test_pipeable_script_guards_bash_source(script: Path):
    """`${BASH_SOURCE[0]}` unguarded is fatal under `set -u` when piped."""
    text = script.read_text(encoding="utf-8")
    if "set -u" not in text and "set -euo" not in text:
        pytest.skip(f"{script.name} does not run under set -u")
    offenders = [
        f"line {i}: {line.strip()}"
        for i, line in enumerate(text.splitlines(), 1)
        if _BARE_BASH_SOURCE_RE.search(line)
    ]
    assert not offenders, (
        f"{script.name} dereferences BASH_SOURCE[0] with no default, which "
        f"aborts the script when it is piped into bash. Use "
        f'"${{BASH_SOURCE[0]:-}}" and check the file exists.\n  '
        + "\n  ".join(offenders)
    )


@pytest.mark.parametrize("script", _pipeable_scripts(), ids=lambda p: p.name)
def test_pipeable_script_does_not_prompt_on_stdin(script: Path):
    """Piped scripts must ask on /dev/tty: stdin carries the script itself, so
    a bare `read` answers every prompt with EOF and the run does nothing."""
    text = script.read_text(encoding="utf-8")
    offenders = []
    for i, line in enumerate(text.splitlines(), 1):
        if not _BARE_READ_RE.match(line):
            continue
        # `while IFS= read -r x; do … done <<<"$data"` consumes a here-string,
        # not the user's stdin: the redirection sits on the loop, not the read.
        if "IFS=" in line and "while" in line:
            continue
        offenders.append(f"line {i}: {line.strip()}")
    assert not offenders, (
        f"{script.name} reads an answer from stdin. When the script arrives "
        f"through a pipe, stdin is the script itself: `read` hits EOF, the "
        f"prompt is answered 'no', and nothing happens. Read from /dev/tty.\n  "
        + "\n  ".join(offenders)
    )


def test_update_dialog_does_not_pipe_the_repo_installer_into_bash():
    """scripts/install.sh copies files out of the checkout around it, so it
    cannot work from a pipe: yet it was what the update dialog offered every
    Arch and CachyOS user, the largest install base ASM has."""
    from arctis_sound_manager.update_checker import (
        InstallMethod, repo_setup_command,
    )
    for method in InstallMethod:
        cmd = repo_setup_command(method)
        if not cmd:
            continue
        assert "install.sh | bash" not in cmd, (
            f"{method}: the update dialog offers a command that pipes the "
            f"repository installer into bash, which cannot succeed:\n  {cmd}"
        )


def test_repo_installer_refuses_to_run_from_a_pipe():
    """It must say so, rather than abort on an unbound variable."""
    text = (SCRIPTS_DIR / "install.sh").read_text(encoding="utf-8")
    assert 'BASH_SOURCE[0]:-' in text, \
        "install.sh still dereferences BASH_SOURCE[0] unguarded"
    assert "must run from a clone" in text, \
        "install.sh no longer explains why a piped run cannot work"
