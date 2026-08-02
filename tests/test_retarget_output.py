# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Switching the Output device must move a link, not rebuild the graph.

A ``pw-loopback`` process *is* the virtual sink it publishes. Recreating it to
change where a channel points destroys that sink, so every stream playing to it
is orphaned and scattered by PipeWire onto whatever else it can find; a new sink
then appears with a new id and the streams have to be chased back. That round
trip is what made switching between a Bluetooth headset and the Arctis lose
audio and land channels on the wrong device.

``retarget_output`` re-links instead: the sink stays in the graph and only the
link below the equaliser moves.
"""
from __future__ import annotations

from unittest.mock import patch

from arctis_sound_manager.pw_utils import SONAR_OUTPUT_NODE, retarget_output

BUDS = "bluez_output.30_96_10_49_54_E2.1"
HEADSET = "alsa_output.usb-SteelSeries_Arctis_Nova_7-00.analog-stereo"


def test_relinks_from_the_sonar_output_node():
    with patch("arctis_sound_manager.pw_utils.ensure_loopback_link",
               return_value=True) as link:
        assert retarget_output(BUDS) is True

    link.assert_called_once_with(SONAR_OUTPUT_NODE, BUDS, data=None)


def test_switching_devices_never_recreates_a_loopback():
    """The whole point: no process is stopped, so no sink disappears."""
    with patch("arctis_sound_manager.pw_utils.ensure_loopback_link",
               return_value=True), \
         patch("arctis_sound_manager.loopback_manager.LoopbackManager.recreate") as recreate, \
         patch("arctis_sound_manager.loopback_manager.LoopbackManager.recreate_all") as recreate_all:
        retarget_output(BUDS)
        retarget_output(HEADSET)

    recreate.assert_not_called()
    recreate_all.assert_not_called()


def test_failure_is_reported_so_the_caller_can_fall_back():
    """A chain that is not up yet must not be mistaken for a completed switch —
    the caller rebuilds in that case rather than leaving the channel silent."""
    with patch("arctis_sound_manager.pw_utils.ensure_loopback_link",
               return_value=False):
        assert retarget_output(BUDS) is False


def test_empty_target_is_rejected_without_touching_the_graph():
    with patch("arctis_sound_manager.pw_utils.ensure_loopback_link") as link:
        assert retarget_output("") is False

    link.assert_not_called()


def test_prefetched_dump_is_passed_through():
    """Callers doing a sweep should not force a second pw-dump."""
    dump: list = [{"id": 1}]
    with patch("arctis_sound_manager.pw_utils.ensure_loopback_link",
               return_value=True) as link:
        retarget_output(BUDS, data=dump)

    link.assert_called_once_with(SONAR_OUTPUT_NODE, BUDS, data=dump)
