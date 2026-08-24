# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""device_state.py — Runtime state for the currently connected Arctis device.

Set by CoreEngine.configure_virtual_sinks() when a device is detected.
Read by sonar_to_pipewire to generate correct, device-aware PipeWire configs.

When no device is connected, `get_physical_out()` / `get_physical_in()` /
`get_device_name()` return empty strings. Callers must treat that as the
"no device" state and skip config generation (or write PipeWire targets as
the empty string, which PipeWire interprets as "default sink/source").

Devices with two ALSA PCMs (e.g. Arctis 7 Pro Audio: pro-output-0 mono,
pro-output-1 stereo) store separate game and chat outputs so that routing
and the chatmix wheel work correctly.  Single-output devices have
_physical_out_game == _physical_out_chat.
"""
from __future__ import annotations

import logging
import threading

_log = logging.getLogger(__name__)

_lock:               threading.Lock = threading.Lock()
_physical_out_game:  str = ""   # stereo PCM (pro-output-1) — game, media, HeSuVi
_physical_out_chat:  str = ""   # mono PCM   (pro-output-0) — chat, sidetone
_physical_in:        str = ""
_spatial_engine:     str = "hesuvi"
_device_name:        str = ""
# What the virtual channels are called, which is not always the device name.
# On a real headset the two are the same and the name is what you want —
# "Arctis Nova Pro Wireless Game" tells you which headset the channel belongs
# to, and that matters when more than one is plugged in. The generic profile
# has no headset to name, so the same rule produced "Generic audio device
# Game" in every application's output picker (#208).
_channel_label:      str = ""


def set_current_device(
    physical_out_game: str,
    physical_out_chat: str,
    physical_in:       str,
    spatial_engine:    str,
    device_name:       str,
    channel_label:     str = "",
) -> None:
    """Called by CoreEngine after a device is detected."""
    global _physical_out_game, _physical_out_chat, _physical_in, _spatial_engine, _device_name
    global _channel_label
    with _lock:
        _physical_out_game = physical_out_game
        _physical_out_chat = physical_out_chat
        _physical_in       = physical_in
        _spatial_engine    = spatial_engine
        # Strip "SteelSeries " prefix for use in PipeWire node descriptions
        _device_name = device_name.replace("SteelSeries ", "")
        # Falls back to the device name, so every profile that does not ask for
        # anything keeps the behaviour it had.
        _channel_label = (channel_label or _device_name)
    _log.info(
        "Device state: %s | out_game=%s | out_chat=%s | engine=%s",
        _device_name, physical_out_game, physical_out_chat, spatial_engine,
    )


def clear() -> None:
    """Called by CoreEngine.teardown() when the device disconnects."""
    global _physical_out_game, _physical_out_chat, _physical_in, _spatial_engine, _device_name
    global _channel_label
    with _lock:
        _channel_label = ""
        _physical_out_game = ""
        _physical_out_chat = ""
        _physical_in       = ""
        _spatial_engine    = "hesuvi"
        _device_name       = ""


def is_device_set() -> bool:
    """True if a device has been registered via `set_current_device`."""
    with _lock:
        return bool(_physical_out_game or _physical_out_chat)


def get_physical_out_game() -> str:
    """Stereo PCM used by game, media and HeSuVi (pro-output-1 on dual-PCM devices)."""
    with _lock:
        return _physical_out_game


def get_physical_out_chat() -> str:
    """Mono PCM used by chat and sidetone (pro-output-0 on dual-PCM devices)."""
    with _lock:
        return _physical_out_chat


def get_physical_out() -> str:
    """Back-compat accessor: returns the game output, falling back to chat."""
    with _lock:
        return _physical_out_game or _physical_out_chat


def get_physical_in() -> str:
    with _lock:
        return _physical_in


def get_spatial_engine() -> str:
    with _lock:
        return _spatial_engine


def get_device_name() -> str:
    with _lock:
        return _device_name


def get_channel_label() -> str:
    """What to call the virtual channels. See ``_channel_label``."""
    with _lock:
        return _channel_label or _device_name
