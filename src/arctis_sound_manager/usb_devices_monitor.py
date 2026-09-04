# Copyright (C) 2022 Giacomo Furlan (elegos) — original work
# Copyright (C) 2026 loteran — modifications
# SPDX-License-Identifier: GPL-3.0-or-later

import asyncio
import logging
import threading
import time
from typing import Callable

try:
    import pyudev
    _PYUDEV_AVAILABLE = True
    _PYUDEV_IMPORT_ERROR: Exception | None = None
except Exception as e:  # ImportError, OSError on libudev missing
    pyudev = None  # type: ignore[assignment]
    _PYUDEV_AVAILABLE = False
    _PYUDEV_IMPORT_ERROR = e


_POLL_INTERVAL_SECONDS = 2.0
_STEELSERIES_VENDOR_ID = 0x1038
_WATCHDOG_INTERVAL_SECONDS = 5.0


class USBDevicesMonitor:
    """USB hotplug monitor with pyudev event backend and a polling fallback.

    pyudev is the preferred backend (event-driven, zero CPU when idle), but it
    requires libudev + a working netlink socket. In containers, restricted
    sandboxes, or distros without libudev, the import or netlink setup can
    fail. In that case we fall back to a 2s polling loop over usb.core.find()
    so the app still functions (degraded but usable).
    """

    _instance: 'USBDevicesMonitor|None' = None

    @staticmethod
    def get_instance() -> 'USBDevicesMonitor':
        if USBDevicesMonitor._instance is None:
            USBDevicesMonitor._instance = USBDevicesMonitor()
        return USBDevicesMonitor._instance

    def __init__(self):
        self.logger = logging.getLogger('USBDevicesMonitor')

        self._stopping = False
        self._on_connect_callbacks: list[Callable[[int, int, str], None]] = []
        self._on_disconnect_callbacks: list[Callable[[int, int, str], None]] = []

        self._backend: str = 'none'
        self.context = None
        self.monitor = None
        self._poll_thread: threading.Thread | None = None
        self._observer: 'pyudev.MonitorObserver | None' = None
        self._watchdog_thread: threading.Thread | None = None
        self._known_devices: dict[tuple[int, int], str] = {}

        if _PYUDEV_AVAILABLE:
            try:
                self.context = pyudev.Context()
                self.monitor = pyudev.Monitor.from_netlink(self.context)
                self.monitor.filter_by(subsystem='usb')
                self._backend = 'pyudev'
            except Exception as e:
                self.logger.warning(
                    f"pyudev netlink setup failed ({e!r}) — falling back to polling."
                )
                self.context = None
                self.monitor = None
                self._backend = 'polling'
        else:
            self.logger.warning(
                f"pyudev not available ({_PYUDEV_IMPORT_ERROR!r}) — using polling fallback."
            )
            self._backend = 'polling'

    def register_on_connect(self, callback: Callable[[int, int, str], None]):
        if callback not in self._on_connect_callbacks:
            self._on_connect_callbacks.append(callback)

    def register_on_disconnect(self, callback: Callable[[int, int, str], None]):
        if callback not in self._on_disconnect_callbacks:
            self._on_disconnect_callbacks.append(callback)

    def start(self):
        self.logger.info(f"Starting USB devices monitor (backend={self._backend})...")
        if self._backend == 'pyudev' and self.monitor is not None:
            self._observer = pyudev.MonitorObserver(
                self.monitor,
                callback=self._on_event,
                name='usb-monitor',
            )
            self._observer.start()
            # SD-3: the pyudev observer is a thread; if it dies (udevd restart
            # in a sandboxed session, a container namespace change, netlink
            # never working in a Distrobox setup) nothing noticed and hotplug
            # silently stopped. Watch the thread and fall back to the polling
            # loop, which already exists in this file for exactly this case.
            self._watchdog_thread = threading.Thread(
                target=self._watchdog_loop, name='usb-monitor-watchdog', daemon=True,
            )
            self._watchdog_thread.start()
        elif self._backend == 'polling':
            self._start_polling()
        else:
            self.logger.error("USB devices monitor has no working backend — hotplug disabled.")

    def _start_polling(self):
        self._poll_thread = threading.Thread(
            target=self._poll_loop, name='usb-poll-monitor', daemon=True,
        )
        self._poll_thread.start()

    def _watchdog_loop(self):
        """Watch the pyudev MonitorObserver thread while it is our active
        backend, and switch to the polling fallback if it dies.

        What this catches: the observer thread crashing or exiting
        (Thread.is_alive() going False) — e.g. an unhandled exception in the
        netlink read loop.

        What this does NOT catch: a netlink socket that silently stops
        delivering events while the observer thread is still alive and
        blocked waiting on it (e.g. a udevd restart the socket doesn't
        surface as an error). That failure mode has no cheap, reliable signal
        from the thread object alone and is not detected here.

        Switching backend here is exclusive: once the observer thread has
        died it cannot deliver any more events, so handing off to polling
        cannot double-fire a callback for the same device transition.
        """
        while not self._stopping:
            time.sleep(_WATCHDOG_INTERVAL_SECONDS)
            if self._stopping:
                return
            observer = self._observer
            if observer is None:
                return
            if not observer.is_alive():
                self.logger.error(
                    "pyudev USB monitor observer thread has died — hotplug "
                    "events were no longer being delivered. Switching to the "
                    "polling fallback."
                )
                self._backend = 'polling'
                self._observer = None
                self._start_polling()
                return

    async def wait_for_stop(self):
        while not self._stopping:
            await asyncio.sleep(1)

    def stop(self):
        self.logger.info("Stopping USB devices monitor...")
        self._stopping = True
        if self._observer is not None:
            # The MonitorObserver thread was previously never stopped — a fresh
            # thread leaked every time the monitor was (re)started (e.g. daemon
            # reload), since register_on_disconnect()'s stop() only flipped
            # _stopping and never touched the observer thread itself.
            try:
                self._observer.stop()
            except Exception as e:
                self.logger.warning(f"Failed to stop pyudev MonitorObserver: {e!r}")
            self._observer = None

    def _on_event(self, device):
        if device.device_type != 'usb_device':
            return

        try:
            vid: int = int(device.get('ID_VENDOR_ID', '0'), 16)
            pid: int = int(device.get('ID_MODEL_ID', '0'), 16)
        except ValueError:
            return

        # udev's ID_MODEL is the USB iProduct string with spaces replaced by
        # underscores (kernel convention).  Expose it raw so callers can log
        # it for diagnostics when a PID matches no YAML.
        product_name: str = device.get('ID_MODEL', '')

        if device.action == 'add':
            self._on_connect(vid, pid, product_name)
        elif device.action == 'remove':
            self._on_disconnect(vid, pid, product_name)

    def _poll_loop(self):
        """Polling fallback: enumerate USB devices every _POLL_INTERVAL_SECONDS
        and emit add/remove events by diffing against the previous snapshot."""
        try:
            import usb.core
        except Exception as e:
            self.logger.error(f"Polling backend cannot import usb.core: {e!r}")
            return

        # Seed snapshot so we don't fire spurious 'add' for devices already
        # present at daemon startup — those are handled by configure_virtual_sinks.
        self._known_devices = self._snapshot(usb.core)

        while not self._stopping:
            time.sleep(_POLL_INTERVAL_SECONDS)
            current = self._snapshot(usb.core)
            for vid, pid in current.keys() - self._known_devices.keys():
                self._on_connect(vid, pid, current[(vid, pid)])
            for vid, pid in self._known_devices.keys() - current.keys():
                self._on_disconnect(vid, pid, self._known_devices[(vid, pid)])
            self._known_devices = current

    def _snapshot(self, usb_core) -> dict[tuple[int, int], str]:
        """Return {(vid, pid): product_name} for the SteelSeries vendor.
        We only watch our vendor to keep polling cheap."""
        try:
            devices = usb_core.find(find_all=True, idVendor=_STEELSERIES_VENDOR_ID)
            return {
                (int(d.idVendor), int(d.idProduct)): (d.product or '')
                for d in devices
            }
        except Exception as e:
            self.logger.debug(f"USB snapshot failed: {e!r}")
            return self._known_devices  # keep last good snapshot

    def _on_connect(self, vendor_id: int, product_id: int, product_name: str = ''):
        for callback in self._on_connect_callbacks:
            try:
                callback(vendor_id, product_id, product_name)
            except Exception as e:
                self.logger.exception(f"on_connect callback raised: {e!r}")

    def _on_disconnect(self, vendor_id: int, product_id: int, product_name: str = ''):
        for callback in self._on_disconnect_callbacks:
            try:
                callback(vendor_id, product_id, product_name)
            except Exception as e:
                self.logger.exception(f"on_disconnect callback raised: {e!r}")
