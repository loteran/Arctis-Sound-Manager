# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Issue #203: ASM must not write a container-only ExecStart onto the host.

ensure_systemd_unit() writes ~/.config/systemd/user/arctis-manager.service
with `ExecStart=$(which asm-daemon)`. Inside a Distrobox container that
resolves to /usr/bin/asm-daemon — which exists only in the container — while
$HOME is shared with the host and ~/.config/systemd/user takes precedence over
the packaged unit. So the host's systemd ends up running a unit pointing at a
binary it does not have:

    Unable to locate executable '/usr/bin/asm-daemon': No such file or directory
    Main process exited, code=exited, status=203/EXEC
    Start request repeated too quickly.

It is quiet in practice: the running daemon predates the file, so nothing
breaks until the next reboot. The Distrobox installers already write correct
host units that go through `distrobox enter`; overwriting those is destructive.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from arctis_sound_manager import systemd as asm_systemd


@pytest.fixture
def home(tmp_path, monkeypatch):
    unit_dir = tmp_path / ".config" / "systemd" / "user"
    monkeypatch.setattr(asm_systemd, "HOME_SYSTEMD_SERVICE_FOLDER", unit_dir)
    monkeypatch.setattr(asm_systemd.shutil, "which", lambda name: "/usr/bin/asm-daemon")
    monkeypatch.setattr("arctis_sound_manager.init_system.detect_init", lambda: "systemd")
    return unit_dir / asm_systemd.SYSTEMD_SERVICE_NAME


def test_inside_a_container_no_unit_is_written(home, monkeypatch):
    monkeypatch.setenv("container", "distrobox")

    asm_systemd.ensure_systemd_unit()

    assert not home.exists(), (
        "the file written here is the one the HOST runs, and it would point at "
        "a binary only the container has"
    )


def test_a_correct_host_unit_is_left_alone(home, monkeypatch):
    """The Distrobox installers wrote this; ASM must not clobber it."""
    home.parent.mkdir(parents=True)
    correct = ("[Service]\n"
               "ExecStart=/usr/bin/distrobox enter arctis-sound-manager -- /usr/bin/asm-daemon\n")
    home.write_text(correct)
    monkeypatch.setenv("DISTROBOX_ENTER_PATH", "/usr/bin/distrobox-enter")

    asm_systemd.ensure_systemd_unit()

    assert home.read_text() == correct


def test_a_native_install_still_gets_its_unit(home, monkeypatch):
    for var in ("container", "DISTROBOX_ENTER_PATH", "CONTAINER_ID",
                "FLATPAK_ID", "SNAP"):
        monkeypatch.delenv(var, raising=False)

    asm_systemd.ensure_systemd_unit()

    assert home.exists()
    assert "ExecStart=/usr/bin/asm-daemon" in home.read_text()
