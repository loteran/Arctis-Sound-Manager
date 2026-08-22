# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Discussion #48: a toggle that is on but cannot act must say so.

"Redirect Audio on Disconnect" is a TOGGLE; the device it redirects to is a
separate SELECT that defaults to None. Turning the toggle on without picking a
device makes CoreEngine.redirect_audio_on_disconnect() return immediately —
no redirect, no log, no message — which is indistinguishable from the feature
being broken. That is what the reporter in #48 described, and the thread went
quiet after "It's fix ;)".

The daemon-side fallback is separate; this is the half that stops the UI
claiming a feature is armed when it is not.
"""
from __future__ import annotations

import pytest

from arctis_sound_manager.settings import GeneralSettings


def _config(name):
    return {c.name: c for c in [*GeneralSettings.settings_config,
                                *GeneralSettings.dac_settings_config]}[name]


def test_the_disconnect_toggle_declares_its_companion():
    config = _config('redirect_audio_on_disconnect')
    assert getattr(config, 'inert_without', None) == 'redirect_audio_on_disconnect_device'


def test_the_companion_setting_really_defaults_to_unset():
    """If this ever gains a default, the warning becomes dead weight and the
    #48 failure mode disappears on its own — the test should then be removed
    rather than quietly kept green."""
    assert _config('redirect_audio_on_disconnect_device').default_value is None


def test_the_connect_toggle_needs_no_companion():
    """redirect_audio_on_connect targets the headset itself, so it has
    everything it needs — it must not carry a spurious warning."""
    assert getattr(_config('redirect_audio_on_connect'), 'inert_without', None) is None


def test_the_warning_string_exists():
    from arctis_sound_manager.i18n import I18n

    text = I18n.get_instance().translate('settings_values', 'setting_inert_without')
    assert text and text != 'setting_inert_without', (
        "a missing key renders as the raw identifier in the UI"
    )


# ── Daemon side: the redirect must not fail silently ────────────────────────


class _Sink:
    def __init__(self, name, priority=0, vendor=None):
        self.name = name
        self.proplist = {'device.priority': str(priority)}
        if vendor is not None:
            self.proplist['device.vendor.id'] = vendor


def _engine(sinks, configured_device=None):
    from unittest.mock import MagicMock

    from arctis_sound_manager.core import CoreEngine

    engine = CoreEngine.__new__(CoreEngine)
    engine.logger = MagicMock()
    engine.pa_audio_manager = MagicMock()
    engine.pa_audio_manager.sink_list_wrapper.return_value = sinks
    engine.pa_audio_manager.get_default_device.return_value = _Sink("Arctis_Game")
    engine.general_settings = MagicMock()
    engine.general_settings.redirect_audio_on_disconnect = True
    engine.general_settings.redirect_audio_on_disconnect_device = configured_device
    return engine


def test_no_configured_device_falls_back_instead_of_doing_nothing():
    """#48: the toggle on with no device picked used to return silently."""
    engine = _engine([
        _Sink("alsa_output.hdmi-tv", priority=100),
        _Sink("alsa_output.usb-SteelSeries_Arctis-00", priority=900, vendor="1038"),
        _Sink("Arctis_Game", priority=999),
    ])

    engine.redirect_audio_on_disconnect()

    engine.pa_audio_manager.redirect_audio.assert_called_once_with("alsa_output.hdmi-tv")


def test_the_configured_device_still_wins():
    engine = _engine([_Sink("alsa_output.hdmi-tv", priority=100)],
                     configured_device="alsa_output.speakers")

    engine.redirect_audio_on_disconnect()

    engine.pa_audio_manager.redirect_audio.assert_called_once_with("alsa_output.speakers")


def test_the_headset_is_never_the_fallback():
    """Redirecting to the headset that just went away is the one answer that
    cannot help."""
    engine = _engine([
        _Sink("alsa_output.usb-SteelSeries_Arctis-00", priority=900, vendor="1038"),
        _Sink("Arctis_Media", priority=800),
    ])

    engine.redirect_audio_on_disconnect()

    engine.pa_audio_manager.redirect_audio.assert_not_called()
    assert engine.logger.warning.called, "no output left must be said out loud"
