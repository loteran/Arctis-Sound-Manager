"""Bring a Bluetooth output back when it drops out from under a channel.

A channel routed to earbuds loses them when the Bluetooth transport fails —
which it does on its own, without the earbuds going anywhere: the log shows
``spa.bluez5: Failure in Bluetooth audio transport`` followed by the adapter
resetting itself, after which BlueZ sits there paired and *not connected* until
someone reconnects by hand. Until then the channel falls back to the headset,
which may well be switched off, and the user hears nothing or a broken stream.

This asks BlueZ to reconnect. It is not clever: the device is one the user
saved as a channel output, so they want it; it is paired, so it is allowed;
and it is gone from the graph while BlueZ says it is not connected, so a
``connect`` is the only thing left to try. Earbuds put back in their case fail
that connect (``Host is down``), so attempts back off — 15 s doubling to five
minutes — and stop costing anything much. The backoff resets the moment the
node is seen again.
"""

from __future__ import annotations

import logging
import re
import subprocess
import threading
import time

_log = logging.getLogger(__name__)

# bluez_output.30_96_10_49_54_E2.1 → 30:96:10:49:54:E2
_BLUEZ_NODE = re.compile(r"^bluez_output\.([0-9A-Fa-f]{2}(?:_[0-9A-Fa-f]{2}){5})\.")

BACKOFF_FIRST_S = 15.0
BACKOFF_MAX_S = 300.0
CONNECT_TIMEOUT_S = 20.0


def mac_of(node_name: str) -> str | None:
    """The device address a bluez_output node name is built from, or None."""
    m = _BLUEZ_NODE.match(node_name or "")
    return m.group(1).replace("_", ":").upper() if m else None


def device_state(mac: str) -> tuple[bool, bool]:
    """(paired, connected) as BlueZ reports them; (False, False) when unsure."""
    try:
        out = subprocess.run(
            ["bluetoothctl", "info", mac], capture_output=True, text=True,
            timeout=5, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return False, False
    paired = re.search(r"^\s*Paired:\s*yes", out, re.M) is not None
    connected = re.search(r"^\s*Connected:\s*yes", out, re.M) is not None
    return paired, connected


class BluetoothReconnector:
    """Call :meth:`tick` from the watchdog with the saved outputs and a
    ``node_exists`` predicate; it reconnects what is missing, with backoff."""

    def __init__(self) -> None:
        self._next_try: dict[str, float] = {}
        self._delay: dict[str, float] = {}
        self._busy: set[str] = set()
        self._lock = threading.Lock()

    def tick(self, saved_outputs: dict[str, str], node_exists, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        for node in set(saved_outputs.values()):
            mac = mac_of(node)
            if mac is None:
                continue
            if node_exists(node):
                if mac in self._delay:
                    _log.info("bluetooth: %s is back on the graph", node)
                self._delay.pop(mac, None)
                self._next_try.pop(mac, None)
                continue
            if now < self._next_try.get(mac, 0.0):
                continue
            with self._lock:
                if mac in self._busy:
                    continue
                self._busy.add(mac)
            delay = self._delay.get(mac, BACKOFF_FIRST_S)
            self._delay[mac] = min(delay * 2, BACKOFF_MAX_S)
            self._next_try[mac] = now + delay
            threading.Thread(target=self._connect, args=(mac, node, delay),
                             name=f"bt-reconnect-{mac}", daemon=True).start()

    def _connect(self, mac: str, node: str, delay: float) -> None:
        try:
            paired, connected = device_state(mac)
            if not paired or connected:
                # Not ours to connect, or already connected and the node simply
                # has not appeared yet (profile still switching) — wait.
                return
            _log.info("bluetooth: %s is paired but not connected — asking BlueZ "
                      "to reconnect (channel output %s)", mac, node)
            res = subprocess.run(
                ["bluetoothctl", "connect", mac], capture_output=True, text=True,
                timeout=CONNECT_TIMEOUT_S, check=False)
            if "Connection successful" in res.stdout:
                _log.info("bluetooth: %s reconnected", mac)
            else:
                tail = (res.stdout + res.stderr).strip().splitlines()[-1:]
                _log.info("bluetooth: %s did not reconnect (%s) — next try in %.0fs",
                          mac, tail[0] if tail else "no answer", delay)
        except (OSError, subprocess.SubprocessError) as exc:
            _log.debug("bluetooth: reconnect of %s failed: %s", mac, exc)
        finally:
            with self._lock:
                self._busy.discard(mac)
