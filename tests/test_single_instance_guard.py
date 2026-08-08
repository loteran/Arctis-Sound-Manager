# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the single-instance guard in scripts/gui.py.

The guard decides whether a launch becomes the GUI or hands its command to the
one already running. Getting that wrong is expensive: a launch that wrongly
decides it is first deletes the running instance's socket and opens a second
window, a second tray icon and a second set of pollers on top of the same
daemon — which is what a login used to produce, with the systemd user unit and
the desktop entry firing a second apart.

The scenario that broke it is the one that has nothing to answer yet: an
instance that is listening but is still several seconds away from its event
loop, so the connection is accepted by the kernel and the reply comes later.
Every test here pins the server name so it can never reach the developer's own
running asm-gui.
"""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtNetwork import QLocalServer, QLocalSocket

from arctis_sound_manager.scripts import gui as gui_mod


@pytest.fixture
def server_name(monkeypatch, tmp_path):
    """A socket name of our own, never the one a real asm-gui is listening on."""
    name = f"ArctisManagerGuiTest-{os.getpid()}-{tmp_path.name}"
    monkeypatch.setattr(gui_mod, "_SERVER_NAME", name)
    yield name
    QLocalServer.removeServer(name)


@pytest.fixture
def busy_instance(server_name):
    """A listening server that never runs an event loop — an instance mid-startup.

    Nothing calls accept() or answers, but the kernel completes the connection,
    which is exactly what a launch racing a starting instance sees.
    """
    server = QLocalServer()
    assert server.listen(server_name), server.errorString()
    yield server
    server.close()


def test_nothing_listening_means_we_are_the_instance(server_name):
    assert gui_mod.hand_off_to_running_instance(QLocalSocket, b"show") is False


def test_starting_instance_is_found_even_though_it_never_replies(busy_instance):
    assert gui_mod.hand_off_to_running_instance(
        QLocalSocket, b"show", reply_timeout_ms=200) is True


def test_starting_instance_keeps_its_socket(busy_instance, server_name):
    """The regression: silence must not be read as death.

    Handing off to an instance that has not answered yet has to leave its
    socket alone. Unlinking it here is what left two GUIs running side by side,
    and left the second one owning the name for every launch afterwards.
    """
    socket_path = busy_instance.fullServerName()

    gui_mod.hand_off_to_running_instance(QLocalSocket, b"show", reply_timeout_ms=200)

    assert busy_instance.isListening()
    assert Path(socket_path).exists()


def test_command_is_readable_after_the_sender_has_gone(busy_instance):
    """The sender does not wait for "ok", so the command must survive it.

    The running instance reads it when it reaches its event loop, seconds after
    the launch that sent it has exited.
    """
    gui_mod.hand_off_to_running_instance(QLocalSocket, b"show", reply_timeout_ms=200)

    assert busy_instance.waitForNewConnection(1000)
    conn = busy_instance.nextPendingConnection()
    assert conn is not None
    conn.waitForReadyRead(1000)
    assert bytes(conn.readAll()) == b"show"


def test_socket_left_by_a_killed_instance_is_taken_over(server_name):
    """A file with nobody behind it is the one case worth removing."""
    probe = QLocalServer()
    assert probe.listen(server_name)
    socket_path = probe.fullServerName()
    probe.close()
    # close() unlinks it; a killed process is what leaves it behind.
    Path(socket_path).touch()

    server = gui_mod.claim_instance_server(QLocalServer, QLocalSocket, b"alive")

    assert server is not None
    assert server.isListening()
    server.close()


def test_losing_the_race_hands_over_instead_of_evicting(busy_instance, server_name):
    """listen() refused while somebody is listening: they own the session.

    This is the launch whose connect was refused a moment before the other
    instance called listen(). It must ask again rather than assume the name is
    stale — assuming is how both of them end up running.
    """
    socket_path = busy_instance.fullServerName()

    server = gui_mod.claim_instance_server(QLocalServer, QLocalSocket, b"show")

    assert server is None
    assert busy_instance.isListening()
    assert Path(socket_path).exists()
