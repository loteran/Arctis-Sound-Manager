# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""CHA-13: values from SendEqCommand are written to headset firmware.

The D-Bus surface checked `isinstance(bands, list) and len(bands) == 10` and
nothing else. core.send_eq_command then sent `list(command) + [b + shift for b
in bands]`, so an out-of-domain value was shifted by the family's
hardware_eq_zero and written straight to the device. Worse, the array was
persisted to eq_bands.json *before* the send, so _apply_stored_eq() replayed it
at every daemon start.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from arctis_sound_manager.core import CoreEngine, sanitise_eq_bands


class _Sanitiser:
    def _sanitised_eq_bands(self, bands):
        return sanitise_eq_bands(bands, MagicMock())


def _engine():
    return _Sanitiser()


def test_ten_in_domain_integers_pass_through_untouched():
    assert _engine()._sanitised_eq_bands([20] * 10) == [20] * 10
    assert _engine()._sanitised_eq_bands([0, 40] + [20] * 8) == [0, 40] + [20] * 8


def test_out_of_domain_values_are_clamped_not_sent_raw():
    """The 0-40 scale is ASM's own; the family shift is applied afterwards, so
    a 4000 here becomes a wildly out-of-range byte on the wire."""
    assert _engine()._sanitised_eq_bands([4000] + [20] * 9) == [40] + [20] * 9
    assert _engine()._sanitised_eq_bands([-100] + [20] * 9) == [0] + [20] * 9


def test_non_numeric_and_bool_are_refused_outright():
    """A bool is an int in Python: True would have meant 1 dB silently."""
    assert _engine()._sanitised_eq_bands(["20"] * 10) is None
    assert _engine()._sanitised_eq_bands([True] + [20] * 9) is None
    assert _engine()._sanitised_eq_bands([None] + [20] * 9) is None


def test_wrong_length_is_refused():
    assert _engine()._sanitised_eq_bands([20] * 9) is None
    assert _engine()._sanitised_eq_bands([20] * 11) is None
    assert _engine()._sanitised_eq_bands("not a list") is None


def test_send_eq_command_refuses_before_touching_the_device():
    """No frame may reach send_command when the curve is rejected."""
    engine = MagicMock()
    engine.logger = MagicMock()
    engine.send_eq_command = lambda bands: CoreEngine.send_eq_command(engine, bands)
    engine.device_config = MagicMock()
    engine.send_command = MagicMock()
    engine.get_command_endpoint_address = MagicMock(return_value=0)

    assert engine.send_eq_command([None] * 10) is False
    engine.send_command.assert_not_called()
