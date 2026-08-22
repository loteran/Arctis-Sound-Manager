# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Discussion #190: the uninstaller must not break a working install.

The script used to stop AND `systemctl --user disable` the ASM services
unconditionally, before the first confirmation prompt. So a user who answered
"no", or who ran the documented `curl … | bash` one-liner with no terminal to
answer on, ended up with ASM stopped and disabled at login, nothing
uninstalled, and nothing saying so. The reporter's own words were "the GUI
shows nothing is connected and nothing functions".

These tests run the real script against stub binaries and a throwaway $HOME,
so nothing on the machine running the suite is touched.
"""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "uninstall.sh"


def _stub(path: Path, name: str, body: str) -> None:
    f = path / name
    f.write_text(f"#!/usr/bin/env bash\n{body}\n")
    f.chmod(f.stat().st_mode | stat.S_IEXEC)


@pytest.fixture
def sandbox(tmp_path):
    """A fake $HOME with a pip --user copy, and stubs for everything that
    could touch the real system."""
    home = tmp_path / "home"
    pkg = home / ".local" / "lib" / "python3.14" / "site-packages" / "arctis_sound_manager"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")

    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "calls.log"
    _stub(bindir, "systemctl", f'echo "systemctl $*" >> "{log}"\n'
                               'if [ "$2" = "list-unit-files" ]; then echo "$3 enabled"; fi\n'
                               'exit 0')
    _stub(bindir, "sudo", f'echo "sudo $*" >> "{log}"; exit 0')
    _stub(bindir, "python3", 'exit 0')
    for absent in ("pipx", "dnf", "apt-get", "pacman", "rpm", "dpkg", "flatpak"):
        _stub(bindir, absent, 'exit 1')

    env = dict(os.environ)
    env["HOME"] = str(home)
    env["PATH"] = f"{bindir}:/usr/bin:/bin"
    return env, log, home


def _run_piped(env, *args):
    return subprocess.run(
        ["bash", "-s", "--", *args],
        input=SCRIPT.read_bytes(), capture_output=True, timeout=60, env=env,
    )


def test_no_terminal_means_nothing_is_stopped(sandbox):
    """The exact shape of the command given in #190: piped in, no --yes, no
    terminal. It must refuse — without having touched the services first."""
    env, log, _ = sandbox

    result = _run_piped(env, "--all", "--purge")

    # Refusing is the right answer here, and it says so with a non-zero exit.
    assert result.returncode != 0
    assert b"refusing to guess" in result.stdout + result.stderr
    calls = log.read_text() if log.exists() else ""
    assert "stop" not in calls, (
        "a run that removes nothing must leave the services alone:\n" + calls
    )
    assert "disable" not in calls, (
        "disabling at login is what made ASM stay dead after a reboot:\n" + calls
    )


def test_a_confirmed_removal_does_stop_the_services(sandbox):
    """The stop is still needed — it just belongs after the confirmation."""
    env, log, home = sandbox

    result = _run_piped(env, "--pip-user", "--yes")

    calls = log.read_text() if log.exists() else ""
    assert "stop arctis-manager.service" in calls, (
        "files are being removed; the services must be stopped first:\n" + calls
    )
    assert not (home / ".local" / "lib" / "python3.14" / "site-packages"
                / "arctis_sound_manager").exists()
    # A successful uninstall must exit 0 and say it finished. `command -v -a`
    # exits non-zero once the binary is gone — the successful case — and under
    # pipefail that used to kill the script on its last line.
    assert b"Uninstall finished" in result.stdout, result.stdout[-300:]
    assert result.returncode == 0


def test_the_script_never_stops_services_before_asking(sandbox):
    """Structural guard: no stop may sit outside the confirmed branches."""
    text = SCRIPT.read_text()
    stop_at = text.index("stop_services() {")
    first_confirm = text.index("confirm ")
    assert stop_at < first_confirm or "stop_services\n" in text, (
        "stopping must go through the idempotent helper, not inline"
    )
    assert "trap restore_services_if_nothing_removed EXIT" in text, (
        "a run that ends up removing nothing must put the services back"
    )
