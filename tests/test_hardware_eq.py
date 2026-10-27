# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
test_hardware_eq.py — the custom EQ must not be sent to headsets without one.

send_eq_command() used to hardcode [0x06, 0x33] — the Nova Pro Wireless' report
id and EQ opcode — and send it to whatever was connected. Every other family
uses a different report id, a different opcode, or both, so the command was
discarded by the headset without any error and the custom EQ sliders did
nothing at all (#146, Arctis Nova 7P Gen 2).

Wire format for the families that do have it: one byte per band on a 0-40
scale, 20 being 0 dB — GG computes it as 2 * (10 + gain_in_dB).
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from ruamel.yaml import YAML

from arctis_sound_manager.config import DeviceConfiguration
from arctis_sound_manager.core import CoreEngine

DEVICES = Path(__file__).parent.parent / "src" / "arctis_sound_manager" / "devices"

# Profiles whose protocol we know drives an on-device EQ, and the exact bytes
# that must lead the command.
# profile → (leading command bytes, firmware value meaning 0 dB)
EXPECTED_EQ_COMMANDS = {
    "nova_pro_wireless.yaml": ([0x06, 0x33], 20),
    "arctis_nova_pro_wired.yaml": ([0x06, 0x33], 20),
    # Plain gain bytes, no report id prefix, on ASM's own 0-40 scale.
    "nova_7_discrete_battery.yaml": ([0x33], 20),
    "nova_7p_discrete_battery.yaml": ([0x33], 20),
    # Same opcode, but a ±12 dB scale: 0 dB is 24 here.
    "nova_4.yaml": ([0x33], 24),
}

# Families where writing the curve does not activate it: the gains land in the
# Custom slot and the headset keeps applying whichever preset is selected.
# profile → the frame that must follow the gains.
# Across all 127 SteelSeries specs, only these two families declare
# `selected_eq_preset` (0x2E) — the parametric ones carry `preset_type` in the
# curve frame itself, and every other flat-gain family has no such command.
EXPECTED_PRESET_SELECT = {
    "nova_pro_wireless.yaml": [0x06, 0x2E, 0x04],   # [4] = Custom
    "arctis_nova_pro_wired.yaml": [0x06, 0x2E, 0x04],
}

# Profiles whose EQ takes a parametric payload instead (see hardware_eq.py).
PARAMETRIC_PROFILES = {
    # profile → the opcodes its frames must carry, in order
    "nova_7_perc_battery.yaml": [0xA7, 0x33, 0x27],
    "nova_7p_perc_battery.yaml": [0xA7, 0x33, 0x27],
    # The Nova 5 declares no commit opcode: it applies on the band frame.
    "nova_5.yaml": [0xA5, 0x33],
    "gamebuds.yaml": [0x19, 0x33],
    "nova_3_wireless.yaml": [0xA5, 0x33],
}


# Profiles whose curve goes out as one HIDFEATURE chunk instead of command
# frames. profile → (opcode at frame[1], chunk size)
FEATURE_REPORT_PROFILES = {
    "nova_elite.yaml": (0x1B, 1036),
    "nova_pro_omni.yaml": (0x1B, 1036),
}


def _config(name: str) -> DeviceConfiguration:
    return DeviceConfiguration(YAML(typ="safe").load(DEVICES / name))


@pytest.fixture(autouse=True)
def _custom_eq_mode(tmp_path, monkeypatch):
    """Run these tests in Custom EQ mode, whatever the developer's own mode is.

    `_select_custom_eq_preset` reads `~/.config/arctis_manager/.eq_mode` to keep
    the two equalisers exclusive (see test_eq_mode_exclusivity.py), so without
    this every expectation here would depend on the machine running them.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = tmp_path / ".config" / "arctis_manager"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / ".eq_mode").write_text("custom")
    return tmp_path


def _engine(config: DeviceConfiguration | None) -> MagicMock:
    engine = MagicMock()
    engine._device_lock = threading.RLock()
    engine.device_config = config
    engine.logger = MagicMock()
    engine.get_command_endpoint_address.return_value = 0
    engine.send_eq_command = lambda bands: CoreEngine.send_eq_command(engine, bands)
    engine.has_hardware_eq = lambda: CoreEngine.has_hardware_eq(engine)
    engine._read_eq_mode_is_sonar = CoreEngine._read_eq_mode_is_sonar
    engine._select_custom_eq_preset = (
        lambda endpoint: CoreEngine._select_custom_eq_preset(engine, endpoint))
    return engine


@pytest.mark.parametrize("profile,expected", sorted(EXPECTED_EQ_COMMANDS.items()))
def test_declared_profiles_send_their_own_command(profile, expected):
    command, zero_at = expected
    engine = _engine(_config(profile))

    assert engine.send_eq_command([20] * 10) is True
    engine.send_command.assert_any_call(command + [zero_at] * 10, 0)
    assert engine.send_command.call_args_list[0].args[0] == command + [zero_at] * 10
    assert engine.has_hardware_eq() is True


@pytest.mark.parametrize("profile,expected", sorted(EXPECTED_PRESET_SELECT.items()))
def test_the_written_curve_is_made_the_active_preset(profile, expected):
    """Writing the gains fills the Custom slot; it does not select it.

    On these two families the base station drew the curve on its screen as the
    sliders moved while the sound never changed, because `device_init` selects
    Flat and nothing ever switched away from it — a custom EQ you could see and
    not hear.
    """
    engine = _engine(_config(profile))

    assert engine.send_eq_command([20] * 10) is True

    frames = [call.args[0] for call in engine.send_command.call_args_list]
    assert frames[-1] == expected, "the preset switch must follow the gains"
    assert len(frames) == 2


@pytest.mark.parametrize("profile", sorted(
    set(EXPECTED_EQ_COMMANDS) - set(EXPECTED_PRESET_SELECT)))
def test_families_without_a_preset_command_send_only_the_gains(profile):
    """No opcode may be invented for a family whose spec declares none."""
    engine = _engine(_config(profile))

    engine.send_eq_command([20] * 10)

    assert len(engine.send_command.call_args_list) == 1
    assert _config(profile).hardware_eq_preset_select is None


@pytest.mark.parametrize("profile", sorted(PARAMETRIC_PROFILES))
def test_parametric_families_do_not_grow_a_preset_frame(profile):
    """They carry `preset_type` inside the curve frame; 0x2E is not theirs."""
    assert _config(profile).hardware_eq_preset_select is None


@pytest.mark.parametrize("profile", [
    p.name for p in sorted(DEVICES.glob("*.yaml"))
    if p.name not in EXPECTED_EQ_COMMANDS and p.name not in PARAMETRIC_PROFILES
    and p.name not in FEATURE_REPORT_PROFILES
])
def test_other_profiles_send_nothing(profile):
    """No command at all beats a command the headset cannot understand."""
    engine = _engine(_config(profile))

    assert engine.send_eq_command([20] * 10) is False
    engine.send_command.assert_not_called()
    assert engine.has_hardware_eq() is False


@pytest.mark.parametrize("profile,expected_opcodes",
                         sorted(PARAMETRIC_PROFILES.items()))
def test_parametric_profiles_send_their_own_frames(profile, expected_opcodes):
    """Each family's frame sequence comes from its own spec, not a default."""
    engine = _engine(_config(profile))

    assert engine.send_eq_command([20] * 10) is True
    assert engine.has_hardware_eq() is True
    opcodes = [call.args[0][0] for call in engine.send_command.call_args_list]
    assert opcodes == expected_opcodes


def test_nova_4_shifts_onto_its_own_zero():
    """This family calls 0 dB 24, not 20 — a flat curve must not sit 2 dB low."""
    engine = _engine(_config("nova_4.yaml"))

    engine.send_eq_command([20] * 10)

    frame = engine.send_command.call_args_list[0].args[0]
    assert frame == [0x33] + [24] * 10


def test_families_on_asm_scale_are_not_shifted():
    engine = _engine(_config("nova_7_discrete_battery.yaml"))

    engine.send_eq_command([20] * 10)

    assert engine.send_command.call_args_list[0].args[0] == [0x33] + [20] * 10


def test_slider_scale_maps_onto_decibels():
    """ASM's 0-40 sliders are -10..+10 dB in half-decibel steps."""
    engine = _engine(_config("nova_7_perc_battery.yaml"))

    engine.send_eq_command([0, 20, 40] + [20] * 7)

    band_frame = engine.send_command.call_args_list[1].args[0]
    # band_frame is [0x33, connection, band_1..band_10]; gain is byte 3 of a band.
    gains = [band_frame[2 + i * 6 + 3] for i in range(3)]
    # -10 dB → -100 decidecibels → 0x9C, 0 dB → 0x00, +10 dB → 100 → 0x64
    assert gains == [0x9C, 0x00, 0x64]


def test_unknown_format_is_refused_not_guessed(monkeypatch):
    config = _config("nova_7_perc_battery.yaml")
    object.__setattr__(config, "hardware_eq_format", "does_not_exist")
    engine = _engine(config)

    assert engine.send_eq_command([20] * 10) is False
    engine.send_command.assert_not_called()
    assert engine.logger.error.called


def test_no_device_connected_is_not_an_error():
    engine = _engine(None)

    assert engine.send_eq_command([20] * 10) is False
    engine.send_command.assert_not_called()


def test_bands_are_appended_verbatim():
    """The 0-40 scale is ASM's own; nothing may rescale it on the way out."""
    engine = _engine(_config("nova_pro_wireless.yaml"))
    bands = [0, 5, 10, 15, 20, 25, 30, 35, 40, 20]

    engine.send_eq_command(bands)

    assert engine.send_command.call_args_list[0].args[0] == [0x06, 0x33] + bands


def test_families_needing_a_pause_get_one_between_frames(monkeypatch):
    """GG raises its inter-command delay to 600 ms for these before the bands."""
    import arctis_sound_manager.core as core_mod

    slept: list[float] = []
    monkeypatch.setattr(core_mod.time, "sleep", slept.append)

    engine = _engine(_config("nova_5.yaml"))
    engine.send_eq_command([20] * 10)

    assert slept == [0.6], "the band frame must not follow the name immediately"


def test_families_without_a_declared_pause_do_not_wait(monkeypatch):
    import arctis_sound_manager.core as core_mod

    slept: list[float] = []
    monkeypatch.setattr(core_mod.time, "sleep", slept.append)

    engine = _engine(_config("nova_7_perc_battery.yaml"))
    engine.send_eq_command([20] * 10)

    assert slept == []


def _stored_curve(monkeypatch, tmp_path, bands):
    """Put an eq_bands.json where _apply_stored_eq() will look for it."""
    monkeypatch.setenv("HOME", str(tmp_path))
    eq_file = tmp_path / ".config" / "arctis_manager" / "eq_bands.json"
    eq_file.parent.mkdir(parents=True, exist_ok=True)
    eq_file.write_text(json.dumps(bands))


@pytest.mark.parametrize("profile,expected_opcodes",
                         sorted(PARAMETRIC_PROFILES.items()))
def test_stored_curve_is_restored_in_the_family_s_own_format(
        profile, expected_opcodes, monkeypatch, tmp_path):
    """Init replays the saved curve through the same encoder as the sliders.

    _apply_stored_eq() kept its own hardcoded [0x06, 0x33] long after
    send_eq_command() stopped guessing (#146): on a parametric family the
    restored curve went out as a frame the headset discards, so the custom EQ
    was silent until the user moved a slider by hand.
    """
    _stored_curve(monkeypatch, tmp_path, [20] * 10)
    engine = _engine(_config(profile))

    CoreEngine._apply_stored_eq(engine)

    opcodes = [call.args[0][0] for call in engine.send_command.call_args_list]
    assert opcodes == expected_opcodes


def test_stored_curve_is_not_restored_to_a_headset_without_an_eq(
        monkeypatch, tmp_path):
    _stored_curve(monkeypatch, tmp_path, [20] * 10)
    engine = _engine(_config("arctis_9.yaml"))

    CoreEngine._apply_stored_eq(engine)

    engine.send_command.assert_not_called()


def test_dbus_reports_parametric_families_as_having_an_eq():
    """The GUI hides the custom EQ on `has_hardware_eq` being false.

    That flag used to test hardware_eq_command alone, which parametric
    families don't declare — so the control vanished from exactly the headsets
    parametric support had just been written for (#146).
    """
    from arctis_sound_manager.dbus_service import ArctisManagerDbusSettingsService

    for profile in sorted(PARAMETRIC_PROFILES) + sorted(EXPECTED_EQ_COMMANDS):
        engine = _engine(_config(profile))
        service = MagicMock()
        service.core_engine = engine
        assert ArctisManagerDbusSettingsService.get_list_options is not None
        assert engine.has_hardware_eq() is True, f"{profile} would hide its EQ"


@pytest.mark.parametrize("profile,expected", sorted(FEATURE_REPORT_PROFILES.items()))
def test_feature_report_profiles_send_one_chunk(profile, expected):
    """The Nova Elite: one 1036-byte feature report, no command frame, and no
    0x2E-style select — the 0x1B write is what selects the slot."""
    opcode, size = expected
    config = _config(profile)
    engine = _engine(config)

    assert engine.send_eq_command([20] * 10) is True
    assert engine.has_hardware_eq() is True
    engine.send_command.assert_not_called()
    (frame, sent_size), _ = engine.send_feature_report.call_args
    assert frame[:3] == [0x01, opcode, 0x04]   # report id, opcode, Custom slot
    assert sent_size == size
    assert config.hardware_eq_preset_select is None


def test_the_nova_5_encodes_gain_as_its_spec_does(monkeypatch):
    """The Nova 5's filter payload takes gain_float_to_int: 2 x (10 + dB),
    0-40 with 20 = 0 dB. It used to get the signed tenths of a dB the other
    parametric families take, so flattening the on-device EQ for Sonar sent
    0x00 on every band, which this headset reads as -10 dB (#347)."""
    import arctis_sound_manager.core as core_mod

    monkeypatch.setattr(core_mod.time, "sleep", lambda _s: None)
    engine = _engine(_config("nova_5.yaml"))
    # ASM's sliders: 20 = 0 dB, half a dB per step.
    engine.send_eq_command([20, 20, 30, 10, 40, 0, 20, 20, 20, 20])

    band_frame = engine.send_command.call_args_list[-1].args[0]
    assert band_frame[0] == 0x33
    # No connection byte on this family: band 1 starts at index 1, gain is
    # byte 3 of each 6-byte band.
    gains = [band_frame[1 + i * 6 + 3] for i in range(10)]
    assert gains == [20, 20, 30, 10, 40, 0, 20, 20, 20, 20]


def test_other_parametric_families_keep_signed_tenths(monkeypatch):
    import arctis_sound_manager.core as core_mod

    monkeypatch.setattr(core_mod.time, "sleep", lambda _s: None)
    engine = _engine(_config("nova_3_wireless.yaml"))
    engine.send_eq_command([20, 30, 10] + [20] * 7)

    band_frame = engine.send_command.call_args_list[-1].args[0]
    start = len(band_frame) - 60
    gains = [band_frame[start + i * 6 + 3] for i in range(3)]
    assert gains == [0x00, 0x32, 0xCE]  # 0, +5, -5 dB
