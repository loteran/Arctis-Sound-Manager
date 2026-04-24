"""device_state.py — Runtime state for the currently connected Arctis device.

Set by CoreEngine.configure_virtual_sinks() when a device is detected.
Read by sonar_to_pipewire to generate correct, device-aware PipeWire configs.

When no device is connected, `get_physical_out()` / `get_physical_in()` /
`get_device_name()` return empty strings. Callers must treat that as the
"no device" state and skip config generation (or write PipeWire targets as
the empty string, which PipeWire interprets as "default sink/source").
"""
from __future__ import annotations

import logging
import threading

_log = logging.getLogger(__name__)

_lock:           threading.Lock = threading.Lock()
_physical_out:   str = ""
_physical_in:    str = ""
_spatial_engine: str = "hesuvi"
_device_name:    str = ""


def set_current_device(
    physical_out:   str,
    physical_in:    str,
    spatial_engine: str,
    device_name:    str,
) -> None:
    """Called by CoreEngine after a device is detected."""
    global _physical_out, _physical_in, _spatial_engine, _device_name
    with _lock:
        _physical_out   = physical_out
        _physical_in    = physical_in
        _spatial_engine = spatial_engine
        # Strip "SteelSeries " prefix for use in PipeWire node descriptions
        _device_name = device_name.replace("SteelSeries ", "")
    _log.info("Device state: %s | out=%s | engine=%s", _device_name, physical_out, spatial_engine)


def clear() -> None:
    """Called by CoreEngine.teardown() when the device disconnects."""
    global _physical_out, _physical_in, _spatial_engine, _device_name
    with _lock:
        _physical_out   = ""
        _physical_in    = ""
        _spatial_engine = "hesuvi"
        _device_name    = ""


def is_device_set() -> bool:
    """True if a device has been registered via `set_current_device`."""
    with _lock:
        return bool(_physical_out)


def get_physical_out() -> str:
    with _lock:
        return _physical_out


def get_physical_in() -> str:
    with _lock:
        return _physical_in


def get_spatial_engine() -> str:
    with _lock:
        return _spatial_engine


def get_device_name() -> str:
    with _lock:
        return _device_name
