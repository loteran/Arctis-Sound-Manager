# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""#374: a USB reset and the release of the same handle must not overlap,
and a device held by another program is named instead of reset."""
from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from arctis_sound_manager.core import CoreEngine
from arctis_sound_manager.usb_reenumerate import usbfs_holders


def _fake_proc(root: Path, pid: int, comm: str, targets: list[str]) -> None:
    fd = root / str(pid) / "fd"
    fd.mkdir(parents=True)
    (root / str(pid) / "comm").write_text(comm + "\n")
    for i, t in enumerate(targets):
        os.symlink(t, fd / str(i))


def test_usbfs_holders_names_the_other_process(tmp_path):
    _fake_proc(tmp_path, 100, "nova-chatmix", ["/dev/null", "/dev/bus/usb/003/007"])
    _fake_proc(tmp_path, 200, "firefox", ["/dev/bus/usb/003/008"])
    _fake_proc(tmp_path, 300, "asm-daemon", ["/dev/bus/usb/003/007"])
    assert usbfs_holders(3, 7, proc_root=tmp_path, exclude_pid=300) == [(100, "nova-chatmix")]


def test_usbfs_holders_needs_a_real_address(tmp_path):
    assert usbfs_holders(None, 7, proc_root=tmp_path) == []
    assert usbfs_holders(MagicMock(), MagicMock(), proc_root=tmp_path) == []


def _engine() -> CoreEngine:
    eng = CoreEngine.__new__(CoreEngine)
    eng.logger = logging.getLogger("test374")
    eng.oled_manager = None
    eng.device_config = None
    return eng


def test_release_waits_for_a_reset_in_flight():
    eng = _engine()
    device = MagicMock()
    eng.usb_device = device
    order: list[str] = []

    def slow_reset(dev, logger):
        order.append("reset start")
        time.sleep(0.2)
        order.append("reset end")
        return True

    released = threading.Event()

    def release_from_udev_thread():
        time.sleep(0.05)   # the reset is in flight by now
        eng._release_usb_handle()
        order.append("release done")
        released.set()

    with patch("arctis_sound_manager.core.reset_via_usbfs", side_effect=slow_reset), \
         patch("arctis_sound_manager.core.usb.util.dispose_resources",
               side_effect=lambda d: order.append("dispose")), \
         patch.object(CoreEngine, "_record_usb_reset_attempt"):
        t = threading.Thread(target=release_from_udev_thread)
        t.start()
        with CoreEngine._usb_reset_lock:
            pass
        # Run the locked part of the escalation synchronously.
        import asyncio

        async def run():
            with patch("arctis_sound_manager.core.asyncio.sleep"), \
                 patch.object(CoreEngine, "_configure_virtual_sinks_off_loop"):
                eng.device_config = MagicMock(vendor_id=0x1038)
                await eng._escalate_to_usb_reset("test")
        asyncio.run(run())
        assert released.wait(2)
        t.join()
    # The release never ran inside the reset, and found nothing left to free.
    assert order == ["reset start", "reset end", "dispose", "release done"]
    assert eng.usb_device is None


def test_wedged_reset_skipped_when_another_program_holds_the_device(caplog):
    eng = _engine()
    eng.usb_device = MagicMock(bus=3, address=7)
    eng._wedged_reset_spent = False
    eng._last_usb_reset_monotonic = -1e9
    loop = MagicMock()
    loop.is_running.return_value = True
    eng._main_event_loop = loop
    with patch("arctis_sound_manager.core.usbfs_holders", return_value=[(100, "nova-chatmix")]), \
         patch("arctis_sound_manager.core.asyncio.run_coroutine_threadsafe") as rct, \
         caplog.at_level(logging.WARNING):
        eng._schedule_wedged_device_reset("init_device transport failures")
    rct.assert_not_called()
    assert "nova-chatmix (pid 100)" in caplog.text
    assert eng._wedged_reset_spent is False
