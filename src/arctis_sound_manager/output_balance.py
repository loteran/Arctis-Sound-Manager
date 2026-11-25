# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""Separate left/right output level on the headset sink, Qt-free.

For hearing that differs between the ears. PipeWire keeps a volume per channel
on every sink, so the balance lives on the headset's own output: everything
ASM plays (each channel, either EQ mode) ends up there, and it costs nothing
in latency or sound.

The *main* volume is the louder side, and each side is the main volume times
its own level (``output_volume_left`` / ``output_volume_right``, percent).
Every place that sets the headset's volume goes through :func:`set_main_volume`
so moving the main slider keeps the balance instead of flattening it, as
``volume_set_all_chans`` does.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Below this many percent difference the sink already has the balance.
_TOLERANCE = 0.01


def levels(general_settings: Any) -> tuple[float, float]:
    """Left and right factors (0.0-1.0); (1.0, 1.0) while the option is off."""
    if not getattr(general_settings, 'output_balance_enabled', False):
        return 1.0, 1.0

    def _pct(name: str) -> float:
        try:
            return max(0, min(100, int(getattr(general_settings, name, 100)))) / 100.0
        except (TypeError, ValueError):
            return 1.0

    return _pct('output_volume_left'), _pct('output_volume_right')


def _side(channel_name: str) -> str | None:
    name = channel_name.lower()
    if 'left' in name:
        return 'left'
    if 'right' in name:
        return 'right'
    return None


def channel_values(channel_names: list[str], main: float,
                   factors: tuple[float, float]) -> list[float]:
    """Per-channel volumes for *main* with the balance applied.

    Left channels (front-left, rear-left, side-left…) get the left factor,
    right ones the right factor, anything else (mono, centre, LFE) the main
    volume as is.
    """
    left, right = factors
    out = []
    for name in channel_names:
        side = _side(name)
        out.append(main * (left if side == 'left' else right if side == 'right' else 1.0))
    return out


def main_volume(sink: Any, factors: tuple[float, float] = (1.0, 1.0)) -> float:
    """The main volume *sink* is set to, given the balance *factors*.

    Read from the side with the larger factor: its channels are main × that
    factor. Taking the loudest channel as the main volume is only right when
    one side is at 100 % — with both below it, that read back a lower main
    volume, which the next pass scaled down again, and the headset faded
    towards silence one watchdog tick at a time. Reading every channel as
    channel ÷ factor is no better on a sink not balanced yet (flat after a
    reconnect): the quieter side's factor inflates it and the louder side
    jumps up. Using the louder side's channels keeps both cases right.
    """
    values = list(getattr(sink.volume, 'values', []) or [])
    if not values:
        return float(sink.volume.value_flat)
    left, right = factors
    lead_factor = max(left, right)
    if lead_factor <= 0:
        return max(values)
    lead = {side for side, f in (('left', left), ('right', right)) if f == lead_factor}
    names = _channel_names(sink)
    on_lead = [v for n, v in zip(names, values) if _side(n) in lead]
    if not on_lead:                      # mono / no left-right channels
        return max(values)
    return min(1.0, max(on_lead) / lead_factor)


def _channel_names(sink: Any) -> list[str]:
    names = list(getattr(sink, 'channel_list', []) or [])
    return names or ['mono'] * len(sink.volume.values)


def set_main_volume(pulse: Any, sink: Any, main: float, factors: tuple[float, float]) -> None:
    """Set *sink* to *main* (0.0-1.0) with the balance applied."""
    import pulsectl

    main = max(0.0, min(1.0, main))
    values = channel_values(_channel_names(sink), main, factors)
    pulse.volume_set(sink, pulsectl.PulseVolumeInfo(values))


def is_balanced(sink: Any, factors: tuple[float, float]) -> bool:
    """Does *sink* already carry this balance (at whatever main volume)?"""
    want = channel_values(_channel_names(sink), main_volume(sink, factors), factors)
    have = list(sink.volume.values)
    return len(want) == len(have) and all(abs(a - b) <= _TOLERANCE for a, b in zip(want, have))


def apply(pulse: Any, sink_names: set[str], factors: tuple[float, float],
          current: tuple[float, float] | None = None) -> bool:
    """Bring the named sinks to *factors*, keeping their main volume.

    *current* is the balance the sink was last set to, when it differs (a
    slider just moved); the main volume is read back with it.

    Returns True when anything was changed. Sinks already balanced are left
    alone, so this is cheap to call on every watchdog tick.
    """
    changed = False
    for sink in pulse.sink_list():
        if sink.name not in sink_names and sink.proplist.get('node.name') not in sink_names:
            continue
        if is_balanced(sink, factors):
            continue
        # Read back under the *current* balance: that is what the sink is set
        # to now. With the new factors a changed balance would be misread.
        set_main_volume(pulse, sink, main_volume(sink, current or factors), factors)
        logger.info("Output balance: %s → left %d%%, right %d%%",
                    sink.name, round(factors[0] * 100), round(factors[1] * 100))
        changed = True
    return changed
