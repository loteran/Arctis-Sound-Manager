# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""What the virtual channels are called, which is not always the device name.

On a real headset the two are the same, and the name is what you want: "Arctis
Nova Pro Wireless Game" says which headset the channel belongs to, and that
matters as soon as a second one is plugged in. The generic profile has no
headset to name, so the same rule produced "Generic audio device Game" in every
application's output picker (#208).

A profile can now name its channels. Only the channels: the device keeps its own
name in the GUI, on D-Bus and in bug reports, because that is where "which
device is this?" is the actual question being answered.
"""
from __future__ import annotations

import pytest

from arctis_sound_manager import device_state
from arctis_sound_manager.loopback_manager import make_specs


@pytest.fixture(autouse=True)
def _clean_state():
    device_state.clear()
    yield
    device_state.clear()


def _set(name: str, label: str = ""):
    device_state.set_current_device(
        physical_out_game="alsa_output.test", physical_out_chat="alsa_output.test",
        physical_in="alsa_input.test", spatial_engine="hesuvi",
        device_name=name, channel_label=label,
    )


def test_a_profile_without_a_label_keeps_naming_channels_after_itself():
    """Every existing headset profile asks for nothing, and must be unaffected —
    the headset's name on the channel is useful, not noise."""
    _set("Arctis Nova Pro Wireless")

    assert device_state.get_channel_label() == "Arctis Nova Pro Wireless"


def test_a_label_replaces_the_device_name_on_the_channels():
    _set("Generic audio device", "Sonar")

    assert device_state.get_channel_label() == "Sonar"


def test_the_device_keeps_its_own_name():
    """The label must not leak into the device's identity: the GUI header, the
    D-Bus settings and the bug report all answer "which device is this?" and
    "Sonar" is not an answer to that."""
    _set("Generic audio device", "Sonar")

    assert device_state.get_device_name() == "Generic audio device"


def test_the_label_reaches_the_channel_descriptions():
    """What the user actually sees: the name in their application's output
    picker."""
    specs = make_specs(sonar=True, physical_game="alsa_output.test",
                        physical_chat="alsa_output.test", device_name="Sonar")

    assert sorted(s.description for s in specs) == [
        "Sonar Chat", "Sonar Game", "Sonar Media"]


def test_the_generic_profile_asks_for_it():
    """The one profile this exists for. Read from the YAML rather than asserted
    in code, since the profile is the authority on what a device declares."""
    from pathlib import Path
    from ruamel.yaml import YAML
    import arctis_sound_manager
    from arctis_sound_manager.config import DeviceConfiguration

    path = Path(arctis_sound_manager.__file__).parent / "devices" / "generic.yaml"
    cfg = DeviceConfiguration(YAML(typ="safe").load(path))

    assert cfg.channel_label == "Sonar"
    assert cfg.name == "Generic audio device", "the device name must not change"


def test_a_disconnect_clears_the_label_too():
    """A stale label outliving its device would rename the next one's channels."""
    _set("Generic audio device", "Sonar")
    device_state.clear()

    assert device_state.get_channel_label() == ""
