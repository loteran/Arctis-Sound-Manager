# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
eq_types.py — Shared EQ data types used by both the daemon and the GUI.

Kept free of any Qt / PySide6 imports so the daemon-side modules
(sonar_to_pipewire, dbus_service) can import EqBand and PW_LABEL without
pulling in the entire Qt stack.
"""
from __future__ import annotations

from dataclasses import dataclass

# ── PipeWire biquad filter label map ─────────────────────────────────────────

PW_LABEL: dict[str, str] = {
    "peakingEQ":    "bq_peaking",
    "lowPass":      "bq_lowpass",
    "highPass":     "bq_highpass",
    "lowShelving":  "bq_lowshelf",
    "highShelving": "bq_highshelf",
}

# ── EQ band dataclass ─────────────────────────────────────────────────────────


@dataclass
class EqBand:
    freq: float = 1000.0
    gain: float = 0.0
    q: float = 0.707
    type: str = "peakingEQ"
    enabled: bool = True
