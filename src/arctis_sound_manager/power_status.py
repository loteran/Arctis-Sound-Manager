# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Shared helper to normalize the headset's power status.

Device YAMLs use two vocabularies for `headset_power_status`: some report
'off'/'on' (Arctis 1 Wireless, Arctis 9, Nova 5, Nova 7*), others report
'offline'/'online'/'cable_charging' (Nova Pro Wireless, Nova Elite, Nova Pro
Omni, Arctis Pro Wireless). Anything reading a GetStatus payload to decide
whether the headset is on must go through normalize_power_value() /
extract_power_status() rather than testing a single string in isolation, or
it silently only works for half the supported devices (the original #124
fix only handled 'off', missed 'offline').

No Qt/PySide dependency: importable from the daemon, the tray (PySide6) and
the standalone video_router process alike.
"""

from __future__ import annotations

import json
import logging
from enum import Enum

log = logging.getLogger(__name__)

# 'off' / 'offline' both mean the headset is powered down.
_OFF_VALUES = {'off', 'offline'}
# 'cable_charging' (Nova Pro Wireless on its charging stand) is not a power-off
# state — the headset is on and reachable, just plugged in. Treat it as ON.
_ON_VALUES = {'on', 'online', 'cable_charging'}


class HeadsetPower(Enum):
    ON = 'on'
    OFF = 'off'
    UNKNOWN = 'unknown'


def normalize_power_value(value: object) -> HeadsetPower:
    """Normalize a single headset_power_status string value into a HeadsetPower.

    Anything not recognized (missing, wrong type, or a vocabulary value we
    don't have a rule for, e.g. Nova Elite's 'standby') resolves to UNKNOWN —
    callers must treat that as "we don't know", not as "off".
    """
    if not isinstance(value, str):
        return HeadsetPower.UNKNOWN
    v = value.strip().lower()
    if v in _OFF_VALUES:
        return HeadsetPower.OFF
    if v in _ON_VALUES:
        return HeadsetPower.ON
    return HeadsetPower.UNKNOWN


def extract_power_status(status: dict) -> HeadsetPower:
    """Find headset_power_status in a parsed GetStatus payload and normalize it.

    `status` is the dict produced by json.loads() on the GetStatus D-Bus
    reply body: {category: {variable: {'value': ..., ...}}}.
    """
    if not isinstance(status, dict):
        return HeadsetPower.UNKNOWN
    for category in status.values():
        if not isinstance(category, dict):
            continue
        power = category.get('headset_power_status')
        if isinstance(power, dict):
            return normalize_power_value(power.get('value'))
    return HeadsetPower.UNKNOWN


def power_status_from_json(status_json: str | bytes) -> HeadsetPower:
    """Same as extract_power_status(), starting from the raw GetStatus JSON string."""
    try:
        return extract_power_status(json.loads(status_json))
    except (TypeError, ValueError) as e:
        log.debug('Could not parse GetStatus payload: %s', e)
        return HeadsetPower.UNKNOWN
