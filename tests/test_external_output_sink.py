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


# ── issue #139: the headset must be *selectable*, never *auto-selected* ──────
#
# Routing the Output channel at the headset is a legitimate setup: a second
# path to it with a flat EQ and no spatial processing (video editing, music
# production) instead of swapping EQ profiles. But ASM picking it on its own
# would silently duplicate a route the user never asked for, since Game / Chat
# / Media already end up there. Hence one predicate, two answers.

def test_headset_is_offered_when_the_user_is_choosing():
    assert is_external_output_sink(
        _Sink("alsa_output.usb-SteelSeries_Arctis-00"), allow_headset=True
    )
    assert is_external_output_sink(
        _Sink("alsa_output.usb-1038_arctis", {"device.vendor.id": "0x1038"}),
        allow_headset=True,
    )


def test_headset_is_never_auto_selected():
    """The default must stay conservative — this is what stops ASM from
    quietly routing the Output channel to the headset by itself."""
    assert not is_external_output_sink(_Sink("alsa_output.usb-SteelSeries_Arctis-00"))
    assert not is_external_output_sink(
        _Sink("alsa_output.usb-1038_arctis", {"device.vendor.id": "0x1038"})
    )


def test_allow_headset_still_rejects_asm_own_nodes():
    """Relaxing the headset rule must not turn ASM's own virtual sinks into
    selectable outputs — routing a channel at one of those would be a loop."""
    for name in ("Arctis_Game", "effect_input.sonar-game-eq",
                 "effect_output.virtual-surround-7.1-hesuvi"):
        assert not is_external_output_sink(_Sink(name), allow_headset=True), name


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
        # SteelSeries headset — listed since issue #139 (see below)
        _dbus_sink({
            "node.name": "alsa_output.usb-SteelSeries",
            "node.nick": "Arctis",
            "device.vendor.id": "0x1038",
        }),
        # ASM's own virtual sink — must never be offered as an output
        _dbus_sink({
            "node.name": "Arctis_Game",
            "node.nick": "Arctis Game",
        }),
    ]

    result = json.loads(get_list_options(svc, "external_audio_devices"))
    by_id = {r["id"]: r["name"] for r in result}

    # BT device falls back to node.name as id, keeps its description as name
    assert by_id["bluez_output.AC_80_0A_12_34_56.1"] == "WH-1000XM4"
    # ALSA device keeps its node.nick as id
    assert by_id["Speakers"] == "Built-in Audio"
    # The headset is offered (issue #139): pointing the Output channel at it is
    # a deliberate setup — a second path to the headset with a flat EQ and no
    # spatial processing. ASM still never auto-selects it; that is enforced by
    # is_external_output_sink()'s default, covered above.
    assert "Arctis" in by_id
    # ASM's own virtual sinks stay out — routing a channel at one is a loop.
    assert not any(r["id"] == "Arctis Game" for r in result)


# ── issue #139 (2nd half): the daemon must honour the user's choice ──────────
#
# The GUI passes the user's pick as an override, but ensure_sonar_eq_configs()
# calls _resolve_external_output() with none. Without reading the setting there,
# the daemon auto-detects a different sink, sees a mismatch against the conf on
# disk and regenerates it — reverting the user's choice on every startup and
# every repair pass, which made the headset unselectable in practice.

def test_resolve_external_output_prefers_the_saved_setting(monkeypatch, tmp_path):
    import arctis_sound_manager.sonar_to_pipewire as stp

    home = tmp_path
    cfg = home / ".config" / "arctis_manager" / "settings"
    cfg.mkdir(parents=True)
    (cfg / "general_settings.yaml").write_text(
        'external_output_device: Arctis Nova Pro Wireless\n'
    )
    monkeypatch.setattr(stp.Path, "home", staticmethod(lambda: home))

    class _S:
        def __init__(self, name, nick, ch):
            self.name, self.channel_count = name, ch
            self.proplist = {"node.nick": nick}

    class _Pulse:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def sink_list(self):
            return [
                _S("alsa_output.pci-hdmi", "HDMI Output", 2),
                _S("alsa_output.usb-SteelSeries_Arctis-00", "Arctis Nova Pro Wireless", 2),
            ]

    monkeypatch.setitem(__import__("sys").modules, "pulsectl",
                        type("m", (), {"Pulse": _Pulse}))

    name, _ch, _pos = stp._resolve_external_output()

    assert name == "alsa_output.usb-SteelSeries_Arctis-00", (
        "the daemon must resolve the saved setting — including the headset — "
        "instead of auto-detecting and overwriting the user's choice"
    )


def test_resolve_external_output_falls_back_to_autodetect_when_unset(monkeypatch, tmp_path):
    """No saved setting → auto-detection, which still skips the headset."""
    import arctis_sound_manager.sonar_to_pipewire as stp

    monkeypatch.setattr(stp.Path, "home", staticmethod(lambda: tmp_path))

    class _S:
        def __init__(self, name, ch, vendor=""):
            self.name, self.channel_count = name, ch
            self.proplist = {"device.vendor.id": vendor} if vendor else {}

    class _Pulse:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def sink_list(self):
            return [
                _S("alsa_output.usb-SteelSeries_Arctis-00", 2, "0x1038"),
                _S("alsa_output.pci-hdmi", 2),
            ]

    monkeypatch.setitem(__import__("sys").modules, "pulsectl",
                        type("m", (), {"Pulse": _Pulse}))

    name, _ch, _pos = stp._resolve_external_output()

    assert name == "alsa_output.pci-hdmi", "auto-detection must never pick the headset"
