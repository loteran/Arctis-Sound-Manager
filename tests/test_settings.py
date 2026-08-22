# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for settings — DeviceSettings, GeneralSettings."""

from pathlib import Path
from unittest.mock import patch

from arctis_sound_manager.settings import DeviceSettings, GeneralSettings


def test_device_settings_get_default():
    ds = DeviceSettings(0x1038, 0x12e0)
    assert ds.get("nonexistent", 42) == 42


def test_device_settings_setattr():
    ds = DeviceSettings(0x1038, 0x12e0)
    ds.gain = 2
    assert ds.settings["gain"] == 2
    assert ds.get("gain") == 2


def test_device_settings_write_read(tmp_path):
    with patch("arctis_sound_manager.settings.SETTINGS_FOLDER", tmp_path):
        ds = DeviceSettings(0x1038, 0x12e0)
        ds.gain = 2
        ds.mic_volume = 5
        ds.write_to_file()

        ds2 = DeviceSettings(0x1038, 0x12e0)
        ds2.gain = 0  # pre-populate keys
        ds2.mic_volume = 0
        ds2.read_from_file()
        assert ds2.get("gain") == 2
        assert ds2.get("mic_volume") == 5


def test_device_settings_read_nonexistent(tmp_path):
    with patch("arctis_sound_manager.settings.SETTINGS_FOLDER", tmp_path):
        ds = DeviceSettings(0x1038, 0x12e0)
        ds.read_from_file()  # should not raise


# ── CHA-5: a corrupt per-device settings file must never abort configuration ──
#
# read_from_file() used to call yaml.load() and int(raw[key]) with no try,
# no shape check, no clamping — called unguarded from core.py on every
# device event. Any of the four shapes below used to raise, which the USB
# monitor's callback guard swallowed, leaving the daemon "active" while
# configure_virtual_sinks() silently aborted forever: no virtual sinks, no
# audio, no explanation.

def test_device_settings_read_truncated_yaml_does_not_raise_and_backs_up(tmp_path):
    with patch("arctis_sound_manager.settings.SETTINGS_FOLDER", tmp_path):
        settings_file = tmp_path / "1038_12e0.yaml"
        # Same shape as the report's repro: an unterminated flow mapping,
        # exactly what a process killed mid-yaml.dump() can leave behind.
        settings_file.write_text("gain: 2\nmic_volume: {oops")

        ds = DeviceSettings(0x1038, 0x12e0)
        ds.gain = 0
        ds.mic_volume = 0
        ds.read_from_file()  # must not raise

        assert ds.get("gain") == 0
        assert ds.get("mic_volume") == 0
        assert not settings_file.exists()
        assert (tmp_path / "1038_12e0.yaml.broken").exists()


def test_device_settings_read_scalar_top_level_does_not_raise_and_backs_up(tmp_path):
    with patch("arctis_sound_manager.settings.SETTINGS_FOLDER", tmp_path):
        settings_file = tmp_path / "1038_12e0.yaml"
        settings_file.write_text("42")

        ds = DeviceSettings(0x1038, 0x12e0)
        ds.read_from_file()  # must not raise

        assert not settings_file.exists()
        assert (tmp_path / "1038_12e0.yaml.broken").exists()


def test_device_settings_read_string_value_skips_only_that_key(tmp_path):
    with patch("arctis_sound_manager.settings.SETTINGS_FOLDER", tmp_path):
        settings_file = tmp_path / "1038_12e0.yaml"
        settings_file.write_text("gain: hello\nmic_volume: 5\n")

        ds = DeviceSettings(0x1038, 0x12e0)
        ds.gain = 0
        ds.mic_volume = 0
        ds.read_from_file()  # must not raise

        # The bad key is skipped, not the whole load: the well-formed
        # sibling key still gets applied.
        assert ds.get("gain") == 0
        assert not ds.was_chosen_by_user("gain")
        assert ds.get("mic_volume") == 5
        assert ds.was_chosen_by_user("mic_volume")


def test_device_settings_read_list_value_skips_only_that_key(tmp_path):
    with patch("arctis_sound_manager.settings.SETTINGS_FOLDER", tmp_path):
        settings_file = tmp_path / "1038_12e0.yaml"
        settings_file.write_text("gain: [1, 2]\nmic_volume: 5\n")

        ds = DeviceSettings(0x1038, 0x12e0)
        ds.gain = 0
        ds.mic_volume = 0
        ds.read_from_file()  # must not raise

        assert ds.get("gain") == 0
        assert not ds.was_chosen_by_user("gain")
        assert ds.get("mic_volume") == 5
        assert ds.was_chosen_by_user("mic_volume")


def test_device_settings_write_is_atomic_no_tmp_file_left_behind(tmp_path):
    with patch("arctis_sound_manager.settings.SETTINGS_FOLDER", tmp_path):
        ds = DeviceSettings(0x1038, 0x12e0)
        ds.gain = 3
        ds.write_to_file()

        assert (tmp_path / "1038_12e0.yaml").exists()
        assert not (tmp_path / "1038_12e0.yaml.tmp").exists()


def test_general_settings_defaults():
    gs = GeneralSettings()
    # Defaults on so the headset becomes the default output when it comes
    # online (issue #135); disconnect redirection stays opt-in.
    assert gs.redirect_audio_on_connect is True
    assert gs.redirect_audio_on_disconnect is False
    assert gs.redirect_audio_on_disconnect_device is None


def test_general_settings_write_read(tmp_path):
    with patch("arctis_sound_manager.settings.SETTINGS_FOLDER", tmp_path):
        gs = GeneralSettings(redirect_audio_on_connect=True)
        gs.write_to_file()

        gs2 = GeneralSettings.read_from_file()
        assert gs2.redirect_audio_on_connect is True


def test_general_settings_opt_out_persists(tmp_path):
    # A user who turns the redirect off must keep it off across restarts,
    # even though the class default is now True (issue #135 sovereignty).
    with patch("arctis_sound_manager.settings.SETTINGS_FOLDER", tmp_path):
        gs = GeneralSettings(redirect_audio_on_connect=False)
        gs.write_to_file()

        gs2 = GeneralSettings.read_from_file()
        assert gs2.redirect_audio_on_connect is False


def test_general_settings_read_nonexistent(tmp_path):
    with patch("arctis_sound_manager.settings.SETTINGS_FOLDER", tmp_path / "nope"):
        gs = GeneralSettings.read_from_file()
        assert gs.redirect_audio_on_connect is True


def test_general_settings_ignores_unknown_keys():
    gs = GeneralSettings(redirect_audio_on_connect=True, unknown_key="ignored")
    assert gs.redirect_audio_on_connect is True
    assert not hasattr(gs, "unknown_key")


def test_general_settings_migrates_non_ascii_hrir_id(tmp_path):
    # issue #132: ssc_hù / ssc_hù+ were renamed to ssc_hu / ssc_hu+ (non-ASCII
    # filenames broke bsdtar extraction under some locales). A settings file
    # written before the rename must still resolve to the new id on load.
    with patch("arctis_sound_manager.settings.SETTINGS_FOLDER", tmp_path):
        gs = GeneralSettings(hrir_id="ssc_hù+")
        gs.write_to_file()

        gs2 = GeneralSettings.read_from_file()
        assert gs2.hrir_id == "ssc_hu+"


def test_general_settings_hrir_id_migration_is_noop_for_current_ids(tmp_path):
    with patch("arctis_sound_manager.settings.SETTINGS_FOLDER", tmp_path):
        gs = GeneralSettings(hrir_id="ssc_hu")
        gs.write_to_file()

        gs2 = GeneralSettings.read_from_file()
        assert gs2.hrir_id == "ssc_hu"


# ── CHA-2 / CHA-8: general_settings.yaml is validated on read, not just at
# the D-Bus boundary — a hand-edited or restored file must not poison the
# daemon either, since CoreEngine.start() re-applies every value it finds.

def test_general_settings_read_rejects_out_of_range_pipewire_quantum(tmp_path):
    with patch("arctis_sound_manager.settings.SETTINGS_FOLDER", tmp_path):
        (tmp_path / "general_settings.yaml").write_text("pipewire_quantum: 8192\n")

        gs = GeneralSettings.read_from_file()
        assert gs.pipewire_quantum == 0  # class default; the file's value was rejected


def test_general_settings_read_rejects_bool_for_pipewire_quantum(tmp_path):
    # isinstance(True, int) is True, which is exactly what let CHA-2 through
    # the old `not isinstance(value, type(config.default_value))` check.
    with patch("arctis_sound_manager.settings.SETTINGS_FOLDER", tmp_path):
        (tmp_path / "general_settings.yaml").write_text("pipewire_quantum: true\n")

        gs = GeneralSettings.read_from_file()
        assert gs.pipewire_quantum == 0


def test_general_settings_read_accepts_declared_pipewire_quantum_values(tmp_path):
    with patch("arctis_sound_manager.settings.SETTINGS_FOLDER", tmp_path):
        (tmp_path / "general_settings.yaml").write_text("pipewire_quantum: 2048\n")

        gs = GeneralSettings.read_from_file()
        assert gs.pipewire_quantum == 2048


def test_general_settings_read_rejects_non_string_select_value(tmp_path):
    # CHA-8: every SELECT setting defaults to None, which used to skip type
    # validation entirely and let a list or dict reach general_settings.yaml.
    with patch("arctis_sound_manager.settings.SETTINGS_FOLDER", tmp_path):
        (tmp_path / "general_settings.yaml").write_text(
            "external_output_device:\n  - 1\n  - 2\n"
        )

        gs = GeneralSettings.read_from_file()
        assert gs.external_output_device is None


def test_general_settings_read_accepts_string_select_value(tmp_path):
    with patch("arctis_sound_manager.settings.SETTINGS_FOLDER", tmp_path):
        (tmp_path / "general_settings.yaml").write_text(
            'external_output_device: "alsa_output.pci-0000_00_1f.3"\n'
        )

        gs = GeneralSettings.read_from_file()
        assert gs.external_output_device == "alsa_output.pci-0000_00_1f.3"


def test_general_settings_read_rejects_unknown_hrir_id(tmp_path):
    # CHA-12: a traversing (or otherwise unknown) hrir_id must fall back to
    # the class default rather than reach package_hrir_path() unfiltered.
    with patch("arctis_sound_manager.settings.SETTINGS_FOLDER", tmp_path):
        (tmp_path / "general_settings.yaml").write_text(
            'hrir_id: "../../../../../../tmp/x/sine"\n'
        )

        gs = GeneralSettings.read_from_file()
        assert gs.hrir_id == "atmos"
