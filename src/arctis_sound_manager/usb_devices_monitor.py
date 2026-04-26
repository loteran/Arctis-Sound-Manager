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
        self._on_connect_callbacks: list[Callable[[int, int], None]] = []
        self._on_disconnect_callbacks: list[Callable[[int, int], None]] = []

        self._backend: str = 'none'
        self.context = None
        self.monitor = None
        self._poll_thread: threading.Thread | None = None
        self._known_devices: set[tuple[int, int]] = set()

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

    def register_on_connect(self, callback: Callable[[int, int], None]):
        if callback not in self._on_connect_callbacks:
            self._on_connect_callbacks.append(callback)

    def register_on_disconnect(self, callback: Callable[[int, int], None]):
        if callback not in self._on_disconnect_callbacks:
            self._on_disconnect_callbacks.append(callback)

    def start(self):
        self.logger.info(f"Starting USB devices monitor (backend={self._backend})...")
        if self._backend == 'pyudev' and self.monitor is not None:
            observer = pyudev.MonitorObserver(
                self.monitor,
                callback=self._on_event,
                name='usb-monitor',
            )
            observer.start()
        elif self._backend == 'polling':
            self._poll_thread = threading.Thread(
                target=self._poll_loop, name='usb-poll-monitor', daemon=True,
            )
            self._poll_thread.start()
        else:
            self.logger.error("USB devices monitor has no working backend — hotplug disabled.")

    async def wait_for_stop(self):
        while not self._stopping:
            await asyncio.sleep(1)

    def stop(self):
        self.logger.info("Stopping USB devices monitor...")
        self._stopping = True

    def _on_event(self, device):
        if device.device_type != 'usb_device':
            return

        try:
            vid: int = int(device.get('ID_VENDOR_ID', '0'), 16)
            pid: int = int(device.get('ID_MODEL_ID', '0'), 16)
        except ValueError:
            return

        if device.action == 'add':
            self._on_connect(vid, pid)
        elif device.action == 'remove':
            self._on_disconnect(vid, pid)

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
            for vid, pid in current - self._known_devices:
                self._on_connect(vid, pid)
            for vid, pid in self._known_devices - current:
                self._on_disconnect(vid, pid)
            self._known_devices = current

    def _snapshot(self, usb_core) -> set[tuple[int, int]]:
        """Return the set of (vid, pid) currently present for the SteelSeries vendor.
        We only watch our vendor to keep polling cheap."""
        try:
            devices = usb_core.find(find_all=True, idVendor=_STEELSERIES_VENDOR_ID)
            return {(int(d.idVendor), int(d.idProduct)) for d in devices}
        except Exception as e:
            self.logger.debug(f"USB snapshot failed: {e!r}")
            return self._known_devices  # keep last good snapshot

    def _on_connect(self, vendor_id: int, product_id: int):
        for callback in self._on_connect_callbacks:
            try:
                callback(vendor_id, product_id)
            except Exception as e:
                self.logger.exception(f"on_connect callback raised: {e!r}")

    def _on_disconnect(self, vendor_id: int, product_id: int):
        for callback in self._on_disconnect_callbacks:
            try:
                callback(vendor_id, product_id)
            except Exception as e:
                self.logger.exception(f"on_disconnect callback raised: {e!r}")
