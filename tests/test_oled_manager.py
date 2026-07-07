# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the OLED USB circuit breaker (issue #100).

Context: `_send_oled_packet` retries 5x per HID packet, and a single frame
is made of several packets. When the OLED interface stays continuously
EBUSY (e.g. a container/distrobox holding the USB handle, or a stray
process), the periodic refresh loop used to flood the log with one
"OLED USB error after 5 attempts: [Errno 16] Resource busy" warning per
packet, forever.

These tests exercise `_send_current_frame` / `_send_oled_packet` directly
against a fake USB device, bypassing the real renderer/protocol pipeline
(only the *number* of packets in a frame and whether each is accepted
matters for the breaker logic).
"""

from __future__ import annotations

import errno as errno_mod
import logging
import threading
import time as time_mod
from types import SimpleNamespace

import usb.core

from arctis_sound_manager.oled_manager import (
    OledManager,
    _OLED_BUSY_FAIL_THRESHOLD,
)

_LOGGER_NAME = "arctis_sound_manager.oled_manager"


class _FakeUsbDevice:
    """Stand-in for the pyusb Device: ctrl_transfer either succeeds or
    always raises USBError with a configurable errno."""

    def __init__(self, fail_errno: int | None = None) -> None:
        self.fail_errno = fail_errno
        self.call_count = 0

    def ctrl_transfer(self, *args, **kwargs):
        self.call_count += 1
        if self.fail_errno is not None:
            raise usb.core.USBError("busy", errno=self.fail_errno)
        return None


class _FakeCore:
    """Minimal CoreEngine stand-in — only what OledManager.__init__ and
    _send_current_frame/_send_oled_packet touch."""

    def __init__(self, usb_device: _FakeUsbDevice | None) -> None:
        self.usb_device = usb_device
        self._usb_write_lock = threading.Lock()
        self.device_config = None
        self.general_settings = SimpleNamespace(oled_brightness=50)


def _make_manager(usb_device, monkeypatch, packet_count: int = 2) -> OledManager:
    core = _FakeCore(usb_device)
    manager = OledManager(core)
    # The breaker only cares about "how many packets does this frame have"
    # and "did each ctrl_transfer succeed" — stub out rendering entirely.
    monkeypatch.setattr(
        manager._protocol, "build_frame_packets",
        lambda *a, **k: [[0, 0, 0, 0] for _ in range(packet_count)],
    )
    monkeypatch.setattr(manager._renderer, "crop_frame", lambda *a, **k: b"\x00")
    manager._current_image = object()  # just needs to be non-None
    monkeypatch.setattr(time_mod, "sleep", lambda *_: None)  # skip retry backoff
    return manager


# ---------------------------------------------------------------------------
# _send_oled_packet: bool return contract
# ---------------------------------------------------------------------------

def test_send_oled_packet_returns_true_on_success(monkeypatch):
    dev = _FakeUsbDevice(fail_errno=None)
    manager = _make_manager(dev, monkeypatch, packet_count=1)
    assert manager._send_oled_packet([1, 2, 3]) is True


def test_send_oled_packet_returns_false_after_retries_exhausted(monkeypatch):
    dev = _FakeUsbDevice(fail_errno=errno_mod.EBUSY)
    manager = _make_manager(dev, monkeypatch, packet_count=1)
    assert manager._send_oled_packet([1, 2, 3]) is False
    assert manager._last_send_errno == errno_mod.EBUSY
    assert dev.call_count == 5  # _MAX_ATTEMPTS


def test_send_oled_packet_returns_false_when_device_gone(monkeypatch):
    manager = _make_manager(None, monkeypatch, packet_count=1)
    assert manager._send_oled_packet([1, 2, 3]) is False
    assert manager._last_send_errno is None


# ---------------------------------------------------------------------------
# Circuit breaker: trips after N consecutive EBUSY frames, single warning
# ---------------------------------------------------------------------------

def test_circuit_breaker_trips_after_threshold_with_single_warning(monkeypatch, caplog):
    dev = _FakeUsbDevice(fail_errno=errno_mod.EBUSY)
    manager = _make_manager(dev, monkeypatch)

    with caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME):
        for _ in range(_OLED_BUSY_FAIL_THRESHOLD):
            manager._send_current_frame()

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "suspended for 60s" in warnings[0].getMessage()
    assert manager._suspend_until > 0
    assert manager._frame_fail_streak == 0  # counter reset once tripped


def test_circuit_breaker_does_not_trip_before_threshold(monkeypatch, caplog):
    dev = _FakeUsbDevice(fail_errno=errno_mod.EBUSY)
    manager = _make_manager(dev, monkeypatch)

    with caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME):
        for _ in range(_OLED_BUSY_FAIL_THRESHOLD - 1):
            manager._send_current_frame()

    assert not any(r.levelno == logging.WARNING for r in caplog.records)
    assert manager._suspend_until == 0.0
    assert manager._frame_fail_streak == _OLED_BUSY_FAIL_THRESHOLD - 1


def test_circuit_breaker_ignores_non_ebusy_errors(monkeypatch, caplog):
    """A different USB error (e.g. EPIPE) is not the distrobox-EBUSY-spam
    scenario this breaker targets — never counted, never suspended."""
    dev = _FakeUsbDevice(fail_errno=errno_mod.EPIPE)
    manager = _make_manager(dev, monkeypatch)

    with caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME):
        for _ in range(5):
            manager._send_current_frame()

    assert not any(r.levelno == logging.WARNING for r in caplog.records)
    assert manager._frame_fail_streak == 0
    assert manager._suspend_until == 0.0


# ---------------------------------------------------------------------------
# Circuit breaker: suspension actually silences the device / logs
# ---------------------------------------------------------------------------

def test_suspended_frame_send_does_not_touch_device(monkeypatch):
    dev = _FakeUsbDevice(fail_errno=errno_mod.EBUSY)
    manager = _make_manager(dev, monkeypatch)
    for _ in range(_OLED_BUSY_FAIL_THRESHOLD):
        manager._send_current_frame()
    assert manager._suspend_until > 0

    calls_before = dev.call_count
    manager._send_current_frame()
    assert dev.call_count == calls_before  # early-returned, no ctrl_transfer


def test_suspended_frame_send_logs_only_debug(monkeypatch, caplog):
    dev = _FakeUsbDevice(fail_errno=errno_mod.EBUSY)
    manager = _make_manager(dev, monkeypatch)
    for _ in range(_OLED_BUSY_FAIL_THRESHOLD):
        manager._send_current_frame()

    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME):
        manager._send_current_frame()

    assert not any(r.levelno >= logging.WARNING for r in caplog.records)


# ---------------------------------------------------------------------------
# Reset semantics: success, and device re-attach
# ---------------------------------------------------------------------------

def test_frame_fail_streak_resets_on_success(monkeypatch):
    dev = _FakeUsbDevice(fail_errno=errno_mod.EBUSY)
    manager = _make_manager(dev, monkeypatch)
    manager._send_current_frame()
    manager._send_current_frame()
    assert manager._frame_fail_streak == 2

    dev.fail_errno = None  # device recovered
    manager._send_current_frame()
    assert manager._frame_fail_streak == 0
    assert manager._suspend_until == 0.0


def test_resumes_after_suspend_window_elapses(monkeypatch):
    dev = _FakeUsbDevice(fail_errno=errno_mod.EBUSY)
    manager = _make_manager(dev, monkeypatch)
    for _ in range(_OLED_BUSY_FAIL_THRESHOLD):
        manager._send_current_frame()
    assert manager._suspend_until > 0

    # Simulate the 60s suspend window having elapsed.
    manager._suspend_until = 0.0
    dev.fail_errno = None
    calls_before = dev.call_count
    manager._send_current_frame()
    assert dev.call_count > calls_before  # attempted again
    assert manager._frame_fail_streak == 0


def test_breaker_resets_on_device_reattach(monkeypatch, caplog):
    dev = _FakeUsbDevice(fail_errno=errno_mod.EBUSY)
    manager = _make_manager(dev, monkeypatch)
    manager._send_current_frame()
    manager._send_current_frame()
    assert manager._frame_fail_streak == 2

    # A re-attach swaps in a new USB device object (still busy).
    new_dev = _FakeUsbDevice(fail_errno=errno_mod.EBUSY)
    manager._core.usb_device = new_dev

    with caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME):
        manager._send_current_frame()

    # The streak must restart at 1 (reset-then-increment), not 3 — so no
    # warning should fire yet even though this is nominally the 3rd call.
    assert manager._frame_fail_streak == 1
    assert not any(r.levelno == logging.WARNING for r in caplog.records)
