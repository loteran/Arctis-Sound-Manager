# Copyright (C) 2022 Giacomo Furlan (elegos) — original work
# Copyright (C) 2026 loteran — modifications
# SPDX-License-Identifier: GPL-3.0-or-later

from typing import Callable, Literal, ParamSpec, TypeVar

P = ParamSpec("P")
R = TypeVar("R")

def status_type(name: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        setattr(func, "_status_type", name)
        return func
    return decorator

@status_type("percentage")
def percentage(perc_min: int, perc_max: int, value: int, round_to: int = 0) -> int:
    """Scale *value* onto 0-100, optionally snapped to a multiple of *round_to*.

    Several headsets report their battery in a handful of steps rather than as
    a real percentage — the Nova Pro Wireless has nine levels, so the raw
    conversion yields 12 / 37 / 62 / 87, which reads like a precise measurement
    the hardware never made. `round_to` lets those profiles state their true
    resolution: the number shown then carries no more precision than the device
    actually has. Profiles whose steps already land on round values (0/25/50/
    75/100) don't need it, and anything else using this parser — station volume,
    mic level — is unaffected as long as it doesn't ask for it.
    """
    if perc_max == perc_min:
        return 0

    if perc_max < perc_min:
        value = perc_min - value
        perc_min, perc_max = perc_max, perc_min
        result = 100 - (value - perc_min) * 100 // (perc_max - perc_min)
    else:
        result = (value - perc_min) * 100 // (perc_max - perc_min)

    if round_to > 1:
        # Half-up, and never round a real 0 or 100 away from the rails.
        result = ((result + round_to // 2) // round_to) * round_to

    # Unconditional: a value outside [perc_min, perc_max] — a misfiled PID
    # reading a battery scale that isn't its own, for instance — must not
    # produce an absurd percentage just because the caller didn't ask for
    # round_to. See RAPPORT-CHAOS-ASM.md HW-2: percentage(0, 4, 76) returned
    # 1900 before this clamp existed unconditionally.
    result = max(0, min(100, result))

    return result

@status_type("signed_percentage")
def signed_percentage(perc_min: int, perc_max: int, value: int) -> int:
    """Like percentage(), but *value* is a signed byte (two's complement).

    The Arctis 7 2019's game_chat_status answers 0x00 for full volume and
    counts *down* through 0xFF, 0xFE, ... to 0xC0 near mute — the same
    signed-byte-of-attenuation convention hardware_eq.py already decodes for
    EQ gain, not a plain unsigned level. A naive unsigned percentage(0, 100)
    read the loudest position (0x00) as 0% and clamped every attenuated one
    (0xC0-0xFF) to 100% — backwards (#220). perc_min/perc_max are given in
    the same signed terms (e.g. -64..0).
    """
    if value >= 128:
        value -= 256
    return percentage(perc_min, perc_max, value)

@status_type("on_off")
def on_off(value: int, on: int, off: int) -> Literal['on', 'off']:
    return 'on' if value == on else 'off'

@status_type("int_str_mapping")
def int_str_mapping(values: dict[int, str], value: int) -> str|None:
    return values.get(value, None)

@status_type("int_int_mapping")
def int_int_mapping(values: dict[int, int], value: int) -> int|None:
    return values.get(value, None)
