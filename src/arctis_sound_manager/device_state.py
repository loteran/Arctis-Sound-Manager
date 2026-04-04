"""device_state.py — Runtime state for the currently connected Arctis device.

Set by CoreEngine.configure_virtual_sinks() when a device is detected.
Read by sonar_to_pipewire to generate correct, device-aware PipeWire configs.
"""
from __future__ import annotations

import logging

_log = logging.getLogger(__name__)

# Fallback defaults (Nova Pro Wireless — keeps backward compat if no device set yet)
_DEFAULT_OUT = "alsa_output.usb-SteelSeries_Arctis_Nova_Pro_Wireless-00.analog-stereo"
_DEFAULT_IN  = "alsa_input.usb-SteelSeries_Arctis_Nova_Pro_Wireless-00.mono-fallback"

_physical_out:   str = _DEFAULT_OUT
_physical_in:    str = _DEFAULT_IN
_spatial_engine: str = "hesuvi"
_device_name:    str = "Arctis Nova Pro Wireless"


def set_current_device(
    physical_out:   str,
    physical_in:    str,
    spatial_engine: str,
    device_name:    str,
) -> None:
    """Called by CoreEngine after a device is detected."""
    global _physical_out, _physical_in, _spatial_engine, _device_name
    _physical_out   = physical_out
    _physical_in    = physical_in
    _spatial_engine = spatial_engine
    # Strip "SteelSeries " prefix for use in PipeWire node descriptions
    _device_name = device_name.replace("SteelSeries ", "")
    _log.info("Device state: %s | out=%s | engine=%s", _device_name, physical_out, spatial_engine)


def clear() -> None:
    """Called by CoreEngine.teardown() when the device disconnects."""
    global _physical_out, _physical_in, _spatial_engine, _device_name
    _physical_out   = _DEFAULT_OUT
    _physical_in    = _DEFAULT_IN
    _spatial_engine = "hesuvi"
    _device_name    = "Arctis Nova Pro Wireless"


def get_physical_out() -> str:
    return _physical_out


def get_physical_in() -> str:
    return _physical_in


def get_spatial_engine() -> str:
    return _spatial_engine


def get_device_name() -> str:
    return _device_name
