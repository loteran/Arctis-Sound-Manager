# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""scripts/restart-user-services.sh must restart the daemon on dinit too.

Wired into debian/postinst, aur/*.install and the RPM %post, this script used
to hardcode `systemctl --user try-restart ...` with no fallback: on a dinit
box (Artix, Arch+dinit) the upgrade scriptlet ran, found no systemd unit to
touch, and did nothing — the daemon kept executing the pre-upgrade code
indefinitely, exactly the inversion runtime_staleness.py's docstring exists to
prevent.

These tests run the real script (not a mock of it) against a fake PATH so no
real systemctl/dinitctl/runuser ever executes and nothing on this developer's
own live session is touched — only the stub binaries below are, and they log
their own invocations instead of doing anything.
"""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "restart-user-services.sh"

_REAL_UID = os.getuid()
_REAL_USER = os.environ.get("USER") or __import__("pwd").getpwuid(_REAL_UID).pw_name


def _write_stub(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture
def fakebin(tmp_path):
    """A PATH containing only stub tools, plus a real /usr/bin/env.

    - `cat`: answers /proc/1/comm with a chosen init name (forcing
      detect_init() down the branch under test) and otherwise defers to the
      real cat, so `[ -S ... ]` etc. still work if the script ever shells out
      to cat for something else.
    - `loginctl`: reports exactly one "logged in user" — this process's own
      uid/user, so the script's `[ -S "/run/user/${uid}/bus" ]` liveness
      check passes against the real, pre-existing session bus socket without
      this test creating or touching anything under /run.
    - `runuser`: ignores the requested privilege switch (this sandbox has no
      permission to become another user anyway) and just execs the command,
      so calls land on the stubbed dinitctl/systemctl/env below.
    """
    d = tmp_path / "bin"
    d.mkdir()

    real_cat = "/bin/cat" if Path("/bin/cat").exists() else "/usr/bin/cat"
    _write_stub(d / "cat", f'''
case "$1" in
    /proc/1/comm) printf '%s\\n' "$FAKE_INIT_COMM"; exit 0 ;;
esac
exec {real_cat} "$@"
''')

    _write_stub(d / "loginctl", f'''
case "$1 $2" in
    "list-users --no-legend") printf '%s %s\\n' {_REAL_UID} {_REAL_USER} ;;
esac
''')

    _write_stub(d / "runuser", '''
shift  # drop -u
shift  # drop the username
shift  # drop --
exec "$@"
''')

    real_env = "/usr/bin/env"
    if not (d / "env").exists() and Path(real_env).exists():
        (d / "env").symlink_to(real_env)

    return d


def _run_script(fakebin: Path, tmp_path: Path, init_comm: str, extra_bins: dict[str, str]):
    for name, body in extra_bins.items():
        _write_stub(fakebin / name, body)

    log = tmp_path / "calls.log"
    env = {
        "PATH": str(fakebin),
        "FAKE_INIT_COMM": init_comm,
        "ASM_TEST_LOG": str(log),
        "HOME": str(tmp_path),
    }
    result = subprocess.run(
        ["/bin/sh", str(_SCRIPT)],
        env=env, capture_output=True, text=True, timeout=10,
    )
    calls = log.read_text().splitlines() if log.exists() else []
    return result, calls


def test_dinit_box_restarts_the_running_daemon(fakebin, tmp_path):
    """No systemctl on PATH, dinitctl present, arctis-manager already running.

    Must reach `dinitctl restart arctis-manager` — the exact call the old
    systemctl-only script could never make.
    """
    dinitctl_body = '''
echo "$@" >> "$ASM_TEST_LOG"
case "$1 $2" in
    "status arctis-manager") echo "State: STARTED"; exit 0 ;;
    "status "*) echo "State: STOPPED"; exit 0 ;;
esac
exit 0
'''
    result, calls = _run_script(
        fakebin, tmp_path, init_comm="dinit",
        extra_bins={"dinitctl": dinitctl_body},
    )

    assert result.returncode == 0, result.stderr
    assert "restart arctis-manager" in calls
    # Stopped-stays-stopped: nothing else was reported STARTED, so nothing
    # else should have been restarted.
    assert not any(c.startswith("restart") and c != "restart arctis-manager" for c in calls)
    # There is no dinit unit for the GUI (XDG autostart handles it instead);
    # the script must never try to restart it as a dinit service.
    assert not any("gui" in c for c in calls)


def test_dinit_box_leaves_a_stopped_service_stopped(fakebin, tmp_path):
    """Someone who stopped ASM on purpose must not get it back from an
    upgrade — dinitctl has no try-restart, so the script must check status
    itself before restarting."""
    dinitctl_body = '''
echo "$@" >> "$ASM_TEST_LOG"
case "$1" in
    status) echo "State: STOPPED"; exit 0 ;;
esac
exit 0
'''
    result, calls = _run_script(
        fakebin, tmp_path, init_comm="dinit",
        extra_bins={"dinitctl": dinitctl_body},
    )

    assert result.returncode == 0, result.stderr
    assert not any(c.startswith("restart") for c in calls)


def test_no_init_manager_is_not_fatal_and_says_why(fakebin, tmp_path):
    """Containers, or a box with neither systemctl nor dinitctl: must not
    fail the package transaction, but — unlike staying silent — must say why
    nothing happened."""
    result, calls = _run_script(fakebin, tmp_path, init_comm="", extra_bins={})

    assert result.returncode == 0
    assert calls == []
    assert "no usable init manager" in result.stderr.lower()


def test_systemd_branch_never_restarts_the_tray():
    """A package scriptlet runs as root with no idea what the user is doing.
    Restarting the tray from it ran the *old* GUI's exit path mid-upgrade,
    which (before 1.4.27) bounced the whole audio server and took
    plasmashell down with it. The headless daemons are restarted; the GUI
    notices the upgrade itself (runtime_staleness.py)."""
    script = (Path(__file__).resolve().parents[1]
              / "scripts" / "restart-user-services.sh").read_text()

    services = next(line for line in script.splitlines()
                    if line.startswith("SYSTEMD_SERVICES="))

    assert "arctis-manager.service" in services
    assert "app-ArctisManager" not in services
    assert "arctis-gui" not in services


def test_upgrade_asks_the_tray_to_restart_as_the_user(fakebin, tmp_path):
    """The daemons are restarted through their units; the tray has none it
    can be counted on to have (XDG autostart, a launcher click), so it is
    asked over its own socket instead: `asm-gui --restart`, run as the user
    with that user's runtime dir — and after the daemons, so the new tray
    comes up talking to the new daemon."""
    systemctl_body = '''
echo "systemctl $@" >> "$ASM_TEST_LOG"
exit 0
'''
    asm_gui_body = '''
echo "asm-gui $@ [XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR]" >> "$ASM_TEST_LOG"
exit 0
'''
    result, calls = _run_script(
        fakebin, tmp_path, init_comm="systemd",
        extra_bins={"systemctl": systemctl_body, "asm-gui": asm_gui_body},
    )

    assert result.returncode == 0, result.stderr
    gui_calls = [c for c in calls if c.startswith("asm-gui")]
    assert gui_calls == [f"asm-gui --restart [XDG_RUNTIME_DIR=/run/user/{_REAL_UID}]"]
    assert calls.index(gui_calls[0]) > max(
        i for i, c in enumerate(calls) if c.startswith("systemctl"))


def test_no_asm_gui_on_path_is_skipped_quietly(fakebin, tmp_path):
    """A partial install (or a distro that names the entry point differently)
    must not turn the scriptlet into a failure."""
    result, calls = _run_script(
        fakebin, tmp_path, init_comm="systemd",
        extra_bins={"systemctl": 'echo "systemctl $@" >> "$ASM_TEST_LOG"\nexit 0'},
    )
    assert result.returncode == 0, result.stderr
    assert not any(c.startswith("asm-gui") for c in calls)
