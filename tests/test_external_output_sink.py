# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for issue #134 (2nd part) — Bluetooth outputs must be selectable.

The external-output pickers (home-page combos, tray routing menu, Sonar output
override, D-Bus external_audio_devices) only matched ``alsa_output.*`` sinks,
silently hiding every Bluetooth device (``bluez_output.*``). A shared predicate,
``pw_utils.is_external_output_sink``, now accepts both families while still
excluding the SteelSeries headset and ASM's own virtual/EQ nodes.
"""

from arctis_sound_manager.pw_utils import is_external_output_sink


class _Sink:
    def __init__(self, name, proplist=None):
        self.name = name
        self.proplist = proplist or {}


def test_alsa_output_is_selectable():
    assert is_external_output_sink(_Sink("alsa_output.pci-0000_00_1f.3.analog-stereo"))


def test_bluetooth_output_is_selectable():
    assert is_external_output_sink(
        _Sink("bluez_output.AC_80_0A_12_34_56.1", {"node.nick": "WH-1000XM4"})
    )


def test_steelseries_alsa_by_name_is_excluded():
    assert not is_external_output_sink(_Sink("alsa_output.usb-SteelSeries_Arctis-00"))


def test_steelseries_by_vendor_id_is_excluded():
    assert not is_external_output_sink(
        _Sink("alsa_output.usb-1038_arctis", {"device.vendor.id": "0x1038"})
    )


def test_virtual_arctis_sink_is_excluded():
    assert not is_external_output_sink(_Sink("Arctis_Game"))


def test_eq_node_is_excluded():
    assert not is_external_output_sink(_Sink("effect_input.sonar-game-eq"))


def test_monitor_or_source_like_name_is_excluded():
    # Only playback node families are accepted; anything else is rejected.
    assert not is_external_output_sink(_Sink("alsa_input.pci-0000_00_1f.3.analog-stereo"))


def test_missing_proplist_is_tolerated():
    sink = _Sink("bluez_output.00_11_22_33_44_55.1")
    sink.proplist = None
    assert is_external_output_sink(sink)


# ── D-Bus GetListOptions('external_audio_devices') id fallback ──────────────

def _dbus_sink(props):
    s = _Sink(props.get("node.name", ""), props)
    return s


def test_get_list_options_bluetooth_without_nick_uses_node_name():
    import json
    from unittest.mock import MagicMock

    from arctis_sound_manager.dbus_service import ArctisManagerDbusSettingsService

    svc = ArctisManagerDbusSettingsService.__new__(ArctisManagerDbusSettingsService)
    svc.core_engine = MagicMock()
    # dbus_next's @method wrapper calls the function but discards its return
    # value, so reach the original callable through the stored metadata.
    get_list_options = (
        ArctisManagerDbusSettingsService.get_list_options.__dict__["__DBUS_METHOD"].fn
    )
    svc.core_engine.pa_audio_manager.sink_list_wrapper.return_value = [
        # Bluetooth: no node.nick, only node.name + node.description
        _dbus_sink({
            "node.name": "bluez_output.AC_80_0A_12_34_56.1",
            "node.description": "WH-1000XM4",
        }),
        # ALSA with a nick
        _dbus_sink({
            "node.name": "alsa_output.pci-0000_00_1f.3.analog-stereo",
            "node.nick": "Speakers",
            "node.description": "Built-in Audio",
        }),
        # SteelSeries headset — must be excluded
        _dbus_sink({
            "node.name": "alsa_output.usb-SteelSeries",
            "node.nick": "Arctis",
            "device.vendor.id": "0x1038",
        }),
    ]

    result = json.loads(get_list_options(svc, "external_audio_devices"))
    by_id = {r["id"]: r["name"] for r in result}

    # BT device falls back to node.name as id, keeps its description as name
    assert by_id["bluez_output.AC_80_0A_12_34_56.1"] == "WH-1000XM4"
    # ALSA device keeps its node.nick as id
    assert by_id["Speakers"] == "Built-in Audio"
    # SteelSeries excluded entirely
    assert not any("SteelSeries" in r["name"] or r["id"] == "Arctis" for r in result)
