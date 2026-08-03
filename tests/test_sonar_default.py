# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for detecting that Sonar is present but not in the audio path.

The failure this guards against is silent by construction: the channels exist,
the equaliser draws, the sliders move, and none of it touches audio from an app
that simply follows the system default. Nothing on screen distinguishes that
from a working setup, so the detection has to be exact — offering the fix when
it is not needed is nagging, and missing it leaves the user believing Sonar
processes audio it never sees.
"""
from __future__ import annotations

import pytest

from arctis_sound_manager.sonar_default import (SonarRouting, classify,
                                                should_offer,
                                                suggested_channel)

HEADSET = "alsa_output.usb-SteelSeries_Arctis_Nova_7-00.analog-stereo"
BUDS = "bluez_output.30_96_10_49_54_E2.1"
CHANNELS = ["Arctis_Game", "Arctis_Chat", "Arctis_Media"]


# ── classify ───────────────────────────────────────────────────────────────────

def test_default_on_hardware_is_bypassed():
    """The reported situation: channels running, default on the raw device."""
    assert classify(HEADSET, CHANNELS + [HEADSET]) is SonarRouting.BYPASSED


def test_default_on_bluetooth_is_also_bypassed():
    assert classify(BUDS, CHANNELS + [BUDS]) is SonarRouting.BYPASSED


@pytest.mark.parametrize("channel", CHANNELS)
def test_default_on_any_sonar_channel_is_active(channel):
    assert classify(channel, CHANNELS + [HEADSET]) is SonarRouting.ACTIVE


def test_no_channels_is_unavailable():
    """Before the daemon builds the loopbacks there is nothing to offer."""
    assert classify(HEADSET, [HEADSET]) is SonarRouting.UNAVAILABLE


def test_no_default_at_all_is_bypassed_when_channels_exist():
    """No default set is still 'audio is not reaching Sonar'."""
    assert classify(None, CHANNELS) is SonarRouting.BYPASSED


def test_channels_absent_outranks_a_missing_default():
    assert classify(None, []) is SonarRouting.UNAVAILABLE


# ── suggestion ─────────────────────────────────────────────────────────────────

def test_media_is_suggested_first():
    """Media is the 'everything else' channel; defaulting to Game would file
    every new app as game audio and skew ChatMix."""
    assert suggested_channel(CHANNELS) == "Arctis_Media"


def test_suggestion_falls_back_to_what_exists():
    assert suggested_channel(["Arctis_Game", "Arctis_Chat"]) == "Arctis_Game"


def test_no_suggestion_without_channels():
    assert suggested_channel([HEADSET]) is None


# ── should_offer ───────────────────────────────────────────────────────────────

def test_offer_when_bypassed_and_not_yet_asked():
    assert should_offer(HEADSET, CHANNELS + [HEADSET], already_asked=False) is True


def test_no_offer_once_asked():
    """Declining is a decision; re-prompting every launch would be nagging."""
    assert should_offer(HEADSET, CHANNELS + [HEADSET], already_asked=True) is False


def test_no_offer_when_already_active():
    assert should_offer("Arctis_Media", CHANNELS, already_asked=False) is False


def test_no_offer_before_channels_exist():
    assert should_offer(HEADSET, [HEADSET], already_asked=False) is False
