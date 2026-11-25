# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""Status of a charging case plugged in over USB (Arctis GameBuds), Qt-free.

The case is its own USB product, not a sub-frame the dongle relays, and it
has no audio. Making it a device profile would let CoreEngine pick it as *the*
device and drop the dongle's sinks; Linux Arctis Manager's v3 daemon models it
as one more device and shares a single audio setup between devices, so
unplugging the case there tears down the dongle's sinks. Here it is read on
the side instead: CoreEngine never sees it, and unplugging it only clears
this monitor's own state.

Protocol from base_arctis_gamebuds_case.device. Offsets follow ASM's
convention (spec field position minus two: no report id, command echo at 0):

    0xB7  left %, right %, case %, left/right/case charging (0/1/2)
          (per-bud values are only kept while that bud is seated)
    0xB8  left bud seated, right bud seated (0/1)
    0xBC  lid (0 open, 1 closed)
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable

from arctis_sound_manager.config import ChargingCaseConfig, DeviceConfiguration

CMD_BATTERY = 0xB7
CMD_BUDS_IN_CASE = 0xB8
CMD_LID = 0xBC
COMMANDS = (CMD_BATTERY, CMD_BUDS_IN_CASE, CMD_LID)

POLL_INTERVAL_S = 5.0
REPORT_LENGTH = 64
READ_TIMEOUT_MS = 500

_CHARGING = {0: 'discharging', 1: 'charging', 2: 'plugged_in'}


def parse_frame(frame: list[int] | bytes) -> dict[str, Any]:
    """The fields one reply carries, or {} for a frame that is not a reply.

    Values outside the spec's ranges are dropped rather than shown: an
    out-of-range byte is a frame this parser misunderstands, not a reading.
    """
    if len(frame) < 2:
        return {}
    cmd = frame[0]
    if cmd == CMD_BATTERY and len(frame) >= 7:
        out: dict[str, Any] = {}
        for prefix, pct, chg in (('case_left', 1, 4), ('case_right', 2, 5), ('case', 3, 6)):
            if 0 <= frame[pct] <= 100:
                out[f'{prefix}_battery_charge'] = frame[pct]
            if frame[chg] in _CHARGING:
                out[f'{prefix}_charging_status'] = _CHARGING[frame[chg]]
        return out
    if cmd == CMD_BUDS_IN_CASE and len(frame) >= 3:
        left, right = frame[1], frame[2]
        if left not in (0, 1) or right not in (0, 1):
            return {}
        seated = {(1, 1): 'both', (1, 0): 'left', (0, 1): 'right', (0, 0): 'none'}
        return {'buds_in_case': seated[(left, right)]}
    if cmd == CMD_LID:
        if frame[1] not in (0, 1):
            return {}
        return {'case_lid_status': 'closed' if frame[1] else 'open'}
    return {}


def choose_interface(pages: dict[int, int | None], case: ChargingCaseConfig) -> int:
    """The command interface: the only one on the declared page, else the profile's.

    Exact page only. The case's push interface is vendor-defined too (0xffc1),
    so "any vendor page" — what CoreEngine.resolve_command_interface falls
    back to — could land on it.
    """
    if case.hid_usage_page is not None:
        exact = [n for n, page in pages.items() if page == case.hid_usage_page]
        if len(exact) == 1:
            return exact[0]
    return case.command_interface_index


def without_unseated_buds(state: dict[str, Any]) -> dict[str, Any]:
    """Drop a bud's case readings while it is not seated: the case has
    nothing to measure then, and the bud's real level comes from the dongle."""
    seated = state.get('buds_in_case')
    if seated is None:
        return state
    out = dict(state)
    for side in ('left', 'right'):
        if seated not in (side, 'both'):
            out.pop(f'case_{side}_battery_charge', None)
            out.pop(f'case_{side}_charging_status', None)
    return out


def status_category(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """`state` in GetStatus's ``{key: {'value', 'type'}}`` shape."""
    return {
        key: {'value': value,
              'type': 'percentage' if key.endswith('_battery_charge') else 'label'}
        for key, value in state.items()
    }


class ChargingCaseMonitor:
    """Polls every charging case a loaded profile declares, while plugged in."""

    def __init__(self, configurations: Callable[[], list[DeviceConfiguration]]):
        self._configurations = configurations
        self.logger = logging.getLogger('ChargingCase')
        self._lock = threading.Lock()
        self._state: dict[int, dict[str, Any]] = {}
        self._workers: dict[int, threading.Event] = {}
        self._stopping = False

    def _case_for(self, vendor_id: int, product_id: int) -> ChargingCaseConfig | None:
        for config in self._configurations():
            case = config.charging_case
            if case and config.vendor_id == vendor_id and product_id in case.product_ids:
                return case
        return None

    def is_case(self, vendor_id: int, product_id: int) -> bool:
        return self._case_for(vendor_id, product_id) is not None

    def state_for(self, config: DeviceConfiguration | None) -> dict[str, Any]:
        """Merged readings of the plugged-in case(s) belonging to `config`."""
        if config is None or config.charging_case is None:
            return {}
        with self._lock:
            out: dict[str, Any] = {}
            for pid in config.charging_case.product_ids:
                out.update(self._state.get(pid, {}))
        return without_unseated_buds(out)

    def scan(self) -> None:
        """Pick up a case already plugged in when the daemon starts — the
        USB monitor only reports devices that appear afterwards."""
        try:
            import usb.core
            present = [(int(d.idVendor), int(d.idProduct))
                       for d in usb.core.find(find_all=True, idVendor=0x1038)]
        except Exception as exc:  # noqa: BLE001
            self.logger.debug("Charging case scan failed: %r", exc)
            return
        for vendor_id, product_id in present:
            self.on_device_connected(vendor_id, product_id)

    def on_device_connected(self, vendor_id: int, product_id: int, product_name: str = '') -> None:
        case = self._case_for(vendor_id, product_id)
        if case is None or self._stopping:
            return
        with self._lock:
            if product_id in self._workers:
                return
            stop = threading.Event()
            self._workers[product_id] = stop
        threading.Thread(target=self._run, args=(vendor_id, product_id, case, stop),
                         name=f'charging-case-{product_id:04x}', daemon=True).start()

    def on_device_disconnected(self, vendor_id: int, product_id: int, product_name: str = '') -> None:
        # The polling backend reports the PID; udev's remove event may not.
        # Either way the worker notices the device is gone on its next
        # transfer, so this only shortens the wait when the PID is known.
        with self._lock:
            stop = self._workers.get(product_id)
        if stop is not None:
            stop.set()

    def stop(self) -> None:
        self._stopping = True
        with self._lock:
            for stop in self._workers.values():
                stop.set()

    # ── Worker ────────────────────────────────────────────────────────────

    def _run(self, vendor_id: int, product_id: int, case: ChargingCaseConfig,
             stop: threading.Event) -> None:
        try:
            self._poll(vendor_id, product_id, case, stop)
        except Exception as exc:  # noqa: BLE001
            self.logger.info("Charging case %04x:%04x: stopped (%r)", vendor_id, product_id, exc)
        finally:
            with self._lock:
                self._state.pop(product_id, None)
                self._workers.pop(product_id, None)

    def _interface_pages(self, device: Any) -> dict[int, int | None]:
        from pathlib import Path

        from arctis_sound_manager.bug_reporter import (find_interface_sysfs_dir,
                                                       usage_page_for_interface)
        from arctis_sound_manager.usb_reenumerate import sysfs_device_dir

        sys_root = Path('/sys/bus/usb/devices')
        device_dir = sysfs_device_dir(device.bus, getattr(device, 'port_numbers', None), sys_root)
        pages: dict[int, int | None] = {}
        if device_dir is None:
            return pages
        for intf in device.get_active_configuration():
            if intf.bInterfaceClass != 3:
                continue
            iface_dir = find_interface_sysfs_dir(sys_root, device_dir.name, intf.bInterfaceNumber)
            pages[intf.bInterfaceNumber] = (
                usage_page_for_interface(iface_dir) if iface_dir else None)
        return pages

    def _poll(self, vendor_id: int, product_id: int, case: ChargingCaseConfig,
              stop: threading.Event) -> None:
        import usb.core
        import usb.util

        device = usb.core.find(idVendor=vendor_id, idProduct=product_id)
        if device is None:
            return
        iface = choose_interface(self._interface_pages(device), case)
        intf = device.get_active_configuration()[(iface, 0)]
        endpoint_in = usb.util.find_descriptor(
            intf, custom_match=lambda e: usb.util.endpoint_direction(
                e.bEndpointAddress) == usb.util.ENDPOINT_IN)
        if endpoint_in is None:
            self.logger.warning("Charging case %04x:%04x: interface %d has no IN endpoint",
                                vendor_id, product_id, iface)
            return

        # Same as the dongle: usbhid must let go of the interface, or the
        # replies go to /dev/hidraw* instead of this read. The case is not an
        # input device, so nothing on the desktop loses anything.
        reattach = False
        if device.is_kernel_driver_active(iface):
            device.detach_kernel_driver(iface)
            reattach = True
        usb.util.claim_interface(device, iface)
        self.logger.info("Charging case %04x:%04x: reading on interface %d",
                         vendor_id, product_id, iface)
        request_type = usb.util.build_request_type(
            direction=usb.util.CTRL_OUT, type=usb.util.CTRL_TYPE_CLASS,
            recipient=usb.util.CTRL_RECIPIENT_INTERFACE)
        try:
            while not stop.is_set() and not self._stopping:
                readings: dict[str, Any] = {}
                for cmd in COMMANDS:
                    report = [cmd] + [0] * (REPORT_LENGTH - 1)
                    # SET_REPORT, output report, no report id.
                    device.ctrl_transfer(request_type, 0x09, 0x0200, iface, report)
                    readings.update(self._read_reply(device, endpoint_in, cmd))
                with self._lock:
                    self._state[product_id] = {**self._state.get(product_id, {}), **readings}
                stop.wait(POLL_INTERVAL_S)
        finally:
            try:
                usb.util.release_interface(device, iface)
                if reattach:
                    device.attach_kernel_driver(iface)
            except Exception:  # noqa: BLE001 — usually: the case is gone
                pass
            usb.util.dispose_resources(device)

    def _read_reply(self, device: Any, endpoint_in: Any, cmd: int) -> dict[str, Any]:
        """The reply to `cmd`, skipping the case's own unsolicited frames.

        A timeout is not an error: a firmware that ignores one of the three
        commands should still report the other two.
        """
        import usb.core

        readings: dict[str, Any] = {}
        for _ in range(4):
            try:
                frame = device.read(endpoint_in.bEndpointAddress,
                                    endpoint_in.wMaxPacketSize, READ_TIMEOUT_MS)
            except usb.core.USBTimeoutError:
                break
            parsed = parse_frame(frame)
            readings.update(parsed)
            if frame and frame[0] == cmd:
                break
        return readings
