# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Running ASM on hardware it cannot talk to (#189).

The audio half of ASM — four channels, the Sonar EQ, HeSuVi, the router, Clips
— only ever manipulates PipeWire sink names. It is the HID conversation
(battery, ANC, sidetone, ChatMix, OLED) that needs an Arctis. Generic mode does
without the second and keeps the first, on a sink the user names.

The first requirement of this feature is that it changes nothing for the people
who *do* have a SteelSeries headset, so that is what the first tests assert.
"""
from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest


# ── nothing changes for a SteelSeries headset ─────────────────────────────────

def test_the_generic_profile_can_never_be_picked_by_the_usb_scan():
    """It declares no product_ids, and the matcher iterates over them.

    An empty list means the loop body never runs and the lookup returns None,
    so the profile is unreachable without the setting — no headset owner can
    land on it because their device was asleep during a scan.
    """
    from arctis_sound_manager.config import load_device_configurations

    generic = [c for c in load_device_configurations() if getattr(c, "generic", False)]
    assert len(generic) == 1, "expected exactly one generic profile"
    assert generic[0].product_ids == [], "a generic profile must declare no product ids"


def test_real_profiles_still_fail_validation_when_incomplete():
    """The checks a headset profile must pass are untouched.

    They exist to catch a profile someone left half written; exempting the
    generic one must not have exempted everybody.
    """
    from arctis_sound_manager.config import DeviceConfiguration

    with pytest.raises(ValueError, match="product_ids"):
        DeviceConfiguration({"device": {"name": "Half written", "vendor_id": 0x1038}})

    with pytest.raises(ValueError, match="vendor_id"):
        DeviceConfiguration({"device": {"name": "No vendor", "product_ids": [0x1234]}})


def test_a_generic_profile_declaring_product_ids_is_refused():
    """The one new rule: it must not be reachable from the USB scan."""
    from arctis_sound_manager.config import DeviceConfiguration

    with pytest.raises(ValueError, match="must not declare"):
        DeviceConfiguration({"device": {"name": "Contradiction", "generic": True,
                                        "product_ids": [0x1234]}})


def test_every_shipped_headset_profile_is_still_valid():
    """All of them load, and none of them accidentally became generic."""
    from arctis_sound_manager.config import load_device_configurations

    configs = load_device_configurations()
    headsets = [c for c in configs if not getattr(c, "generic", False)]
    assert len(headsets) >= 18, f"only {len(headsets)} headset profiles loaded"
    for c in headsets:
        assert c.product_ids, f"{c.name} lost its product ids"
        assert c.vendor_id, f"{c.name} lost its vendor id"


def test_generic_setup_is_skipped_when_the_mode_is_off():
    """The branch is entered only after no Arctis was found, and leaves
    immediately unless the user asked for it."""
    engine = _engine(generic_mode=False)
    assert engine._setup_generic_device() is False
    engine.setup_loopbacks.assert_not_called()


def test_is_device_online_is_unchanged_with_a_headset_attached():
    """The generic branch keys off usb_device being None, so a real device —
    even with the setting left on — takes the normal path."""
    engine = _engine(generic_mode=True)
    engine.usb_device = object()          # a device is attached
    engine.device_status = None           # …but no status polled yet

    assert engine.is_device_online() is False, "should fall through to the normal rule"


# ── the mode itself ───────────────────────────────────────────────────────────

def _engine(*, generic_mode: bool, output: str | None = "alsa_output.usb-Generic-00.analog-stereo",
            source: str | None = None):
    from arctis_sound_manager.core import CoreEngine

    engine = CoreEngine.__new__(CoreEngine)
    engine.logger = MagicMock()
    engine._detect_lock = threading.Lock()
    engine._logged_no_device = False
    engine._device_ready = False
    engine.device_config = None
    engine.usb_device = None
    engine.device_status = None
    engine.oled_manager = None
    engine.general_settings = MagicMock(
        generic_device_mode=generic_mode,
        generic_output_device=output,
        generic_input_device=source,
        redirect_audio_on_connect=True,
    )
    from arctis_sound_manager.config import load_device_configurations
    engine.device_configurations = load_device_configurations()
    engine.setup_loopbacks = MagicMock()
    engine._claim_default_source = MagicMock()
    engine.redirect_to_media_sink = MagicMock()
    engine.init_device = MagicMock()
    engine.teardown = MagicMock()
    engine._resolve_sink_name = lambda w: w or None
    return engine


def test_generic_mode_builds_the_channels_without_touching_usb():
    from arctis_sound_manager import device_state

    engine = _engine(generic_mode=True)
    with patch("arctis_sound_manager.sonar_to_pipewire.check_and_fix_stale_configs",
               return_value=(False, False)):
        handled = engine._setup_generic_device()
    try:
        assert handled is True
        engine.setup_loopbacks.assert_called_once()
        engine.redirect_to_media_sink.assert_called_once()
        # The two things that need a headset must not be attempted.
        engine.init_device.assert_not_called()
        assert engine.oled_manager is None
        assert engine.usb_device is None
        assert device_state.get_physical_out_game() == "alsa_output.usb-Generic-00.analog-stereo"
    finally:
        device_state.clear()


def test_game_and_chat_share_the_one_output_a_generic_headset_has():
    """A real Arctis exposes two PCMs and ASM separates them. One jack means
    both channels land on it — still independent in volume, EQ and the mixer."""
    from arctis_sound_manager import device_state

    engine = _engine(generic_mode=True)
    with patch("arctis_sound_manager.sonar_to_pipewire.check_and_fix_stale_configs",
               return_value=(False, False)):
        engine._setup_generic_device()
    try:
        assert device_state.get_physical_out_game() == device_state.get_physical_out_chat()
    finally:
        device_state.clear()


def test_no_microphone_configured_means_no_capture_chain():
    """Someone routing playback only should not have to name a microphone to
    get their channels."""
    from arctis_sound_manager import device_state

    engine = _engine(generic_mode=True, source=None)
    with patch("arctis_sound_manager.sonar_to_pipewire.check_and_fix_stale_configs",
               return_value=(False, False)):
        engine._setup_generic_device()
    try:
        engine._claim_default_source.assert_not_called()
        assert device_state.get_physical_in() == ""
    finally:
        device_state.clear()


def test_an_absent_output_device_waits_instead_of_failing():
    """Bluetooth earbuds in their case are the ordinary situation, not an
    error: the mode reports it once and comes back when they do."""
    engine = _engine(generic_mode=True, output="bluez_output.gone")
    engine._resolve_sink_name = lambda w: None

    handled = engine._setup_generic_device()

    assert handled is True, "still handled — the caller must not log 'no device'"
    engine.setup_loopbacks.assert_not_called()


def test_generic_mode_counts_as_online_so_routing_behaves():
    """redirect_to_media_sink and the disconnect repatriation both key off
    is_device_online. With no device to poll, the honest answer is that the
    channels are reachable — the setup refuses to build them otherwise."""
    from arctis_sound_manager import device_state

    engine = _engine(generic_mode=True)
    device_state.set_current_device(
        physical_out_game="alsa_output.generic", physical_out_chat="alsa_output.generic",
        physical_in="", spatial_engine="hesuvi", device_name="Generic audio device")
    try:
        assert engine.is_device_online() is True
    finally:
        device_state.clear()
