# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""Short per-part battery text for headsets with more than one battery
(Arctis GameBuds: two earbuds and a charging case), Qt-free.

The combined `headset_battery_charge` stays the number the tray icon draws;
this is the breakdown shown next to it, e.g. "L 95% · R 100%".
"""
from __future__ import annotations

from typing import Any

from arctis_sound_manager.i18n import I18n

_BUDS = (('L', 'left_bud_battery'), ('R', 'right_bud_battery'))


def _reading(entry: Any) -> str | None:
    if not isinstance(entry, dict):
        return None
    value = entry.get('value')
    if entry.get('type') == 'percentage' and isinstance(value, int):
        return f'{value}%'
    if isinstance(value, str):
        return I18n.translate('status_values', value)
    return None


def buds_text(status: dict) -> str | None:
    """"L 95% · R 100%", or None for a headset with no per-bud readings."""
    headset = status.get('headset') if isinstance(status, dict) else None
    if not isinstance(headset, dict):
        return None
    parts = []
    for short, key in _BUDS:
        reading = _reading(headset.get(key))
        if reading is not None:
            parts.append(f'{short} {reading}')
    return ' · '.join(parts) or None


def buds_levels(status: dict) -> list[tuple[str, str]]:
    """[("left", "95%"), ("right", "100%")] — each bud's reading, for icons."""
    headset = status.get('headset') if isinstance(status, dict) else None
    if not isinstance(headset, dict):
        return []
    levels = []
    for side, key in (('left', 'left_bud_battery'), ('right', 'right_bud_battery')):
        reading = _reading(headset.get(key))
        if reading is not None:
            levels.append((side, reading))
    return levels


def case_percent(status: dict) -> int | None:
    """The charging case's level, while it is plugged in over USB."""
    case = status.get('charging_case') if isinstance(status, dict) else None
    entry = case.get('case_battery_charge') if isinstance(case, dict) else None
    if isinstance(entry, dict) and isinstance(entry.get('value'), int):
        return entry['value']
    return None
