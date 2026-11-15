# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""#383: something else re-binds the HID driver (fwupd every 20 s), the
kernel replaces the hidraw node, and the fd ASM holds only returns EIO /
ENODEV. The transport reopens the new node onto the same fd number instead
of the daemon resetting a healthy device."""
import errno
import os

import pytest
import usb.core

from arctis_sound_manager import hidraw_transport
from arctis_sound_manager.hidraw_transport import HidrawTransport


@pytest.fixture
def node(tmp_path):
    p = tmp_path / "hidraw8"
    p.write_bytes(b"")
    return p


def _replace(p):
    """What the kernel does: the old node is deleted, a new one appears."""
    p.unlink()
    p.write_bytes(b"")


def test_replaced_node_is_stale_and_reopened_on_the_same_fd(node):
    t = HidrawTransport({3: node}, locate=lambda i: node)
    fd = t._fds[3]
    assert not t.is_stale(3)
    _replace(node)
    assert t.is_stale(3)
    assert t.reopen(3)
    assert t._fds[3] == fd and not t.is_stale(3)
    t.close()


def test_a_genuine_eio_is_not_swallowed(node, monkeypatch):
    t = HidrawTransport({3: node}, locate=lambda i: node)

    def eio(fd, data):
        raise OSError(errno.EIO, "Input/output error")
    monkeypatch.setattr(hidraw_transport.os, "write", eio)
    with pytest.raises(usb.core.USBError) as exc:
        t.write_output(3, [0x06, 0xb0])
    assert exc.value.errno == errno.EIO
    t.close()


def test_write_after_node_replaced_is_retried_once(node, monkeypatch):
    t = HidrawTransport({3: node}, locate=lambda i: node)
    real_write = os.write
    calls = []

    def write(fd, data):
        calls.append(fd)
        if len(calls) == 1:
            _replace(node)
            raise OSError(errno.ENODEV, "No such device")
        return real_write(fd, data)
    monkeypatch.setattr(hidraw_transport.os, "write", write)
    assert t.write_output(3, [0x06, 0xb0]) == 2
    assert len(calls) == 2
    t.close()
    assert node.read_bytes() == b"\x06\xb0"


def test_without_locate_nothing_is_reopened(node):
    t = HidrawTransport({3: node})
    _replace(node)
    assert not t.reopen(3)
    t.close()
