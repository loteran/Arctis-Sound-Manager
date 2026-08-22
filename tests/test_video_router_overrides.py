# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for video_router's routing_overrides.json pruning.

RAPPORT-CHAOS-ASM.md's "Not restored" section found the router had learned
`"pw-cat": "effect_input.chaos-nan"` from a probe node that no longer
existed. `_reachable()` stops a dead override being *applied*, but nothing
ever removed the entry — the file grows without bound and every entry is
walked on each replay.

The obvious prune ("target isn't in the graph right now, drop it") is wrong:
a Bluetooth speaker that's off, a monitor that's unplugged, or the headset
sitting in its case are all exactly the cases an override exists to
remember — pruning those would silently forget the user's choice. These
tests pin down that the prune implemented in video_router.py (a structural
test on the *value*, not a presence check against the live graph) keeps
device-shaped entries no matter how long they're absent, and removes only
entries whose value can never be a device or an ASM-owned node in the first
place.
"""

import json
import time
from pathlib import Path
from unittest.mock import patch

from arctis_sound_manager.power_status import HeadsetPower
from arctis_sound_manager.scripts import video_router
from arctis_sound_manager.scripts.video_router import (
    _is_dead_override_target,
    _prune_dead_overrides,
    _process_tick,
    load_overrides,
    save_overrides,
)


# ── _is_dead_override_target / _prune_dead_overrides (unit level) ────────────

class TestIsDeadOverrideTarget:
    def test_probe_node_outside_asm_namespace_is_dead(self):
        """The exact residual from RAPPORT-CHAOS-ASM.md's restore."""
        assert _is_dead_override_target("effect_input.chaos-nan", set()) is True

    def test_arbitrary_effect_input_name_is_dead(self):
        assert _is_dead_override_target("effect_input.some-random-probe", set()) is True

    def test_asm_sonar_eq_node_is_not_dead(self):
        """A legitimate (if currently un-remapped) ASM effect node — must
        never be pruned merely for matching the effect_input.* namespace."""
        for name in (
            "effect_input.sonar-game-eq",
            "effect_input.sonar-chat-eq",
            "effect_input.sonar-media-eq",
            "effect_input.sonar-micro-eq",
            "effect_input.sonar-output-eq",
        ):
            assert _is_dead_override_target(name, set()) is False, name

    def test_asm_hesuvi_surround_node_is_not_dead(self):
        assert _is_dead_override_target("effect_input.virtual-surround-7.1-hesuvi", set()) is False
        assert _is_dead_override_target("effect_input.virtual-surround-7.1-hesuvi-media", set()) is False

    def test_bluetooth_speaker_name_is_not_dead(self):
        """A real device identity — absence is normal (speaker powered off),
        never grounds for pruning under this test."""
        assert _is_dead_override_target(
            "bluez_output.AA_BB_CC_DD_EE_FF.1"
        , set()) is False

    def test_monitor_hdmi_output_is_not_dead(self):
        assert _is_dead_override_target(
            "alsa_output.pci-0000_01_00.1.hdmi-stereo"
        , set()) is False

    def test_arctis_virtual_channel_is_not_dead(self):
        assert _is_dead_override_target("Arctis_Game", set()) is False

    def test_non_string_value_is_not_dead(self):
        """Defensive: a corrupted entry must not crash the prune."""
        assert _is_dead_override_target(None, set()) is False
        assert _is_dead_override_target(123, set()) is False


class TestPruneDeadOverrides:
    def test_removes_only_dead_entries(self):
        overrides = {
            "pw-cat": "effect_input.chaos-nan",              # dead (the actual incident)
            "Discord": "bluez_output.AA_BB_CC_DD_EE_FF.1",   # merely absent — must survive
            "mpv": "effect_input.sonar-media-eq",            # legit ASM node — must survive
            "Firefox": "Arctis_Media",                       # always-there channel — must survive
        }
        pruned, dead_keys = _prune_dead_overrides(overrides)

        assert dead_keys == ["pw-cat"]
        assert pruned == {
            "Discord": "bluez_output.AA_BB_CC_DD_EE_FF.1",
            "mpv": "effect_input.sonar-media-eq",
            "Firefox": "Arctis_Media",
        }

    def test_does_not_mutate_input(self):
        overrides = {"pw-cat": "effect_input.chaos-nan"}
        _prune_dead_overrides(overrides)
        assert overrides == {"pw-cat": "effect_input.chaos-nan"}, \
            "the caller's dict must be left untouched; the caller re-saves the returned copy"

    def test_nothing_dead_returns_equivalent_dict_and_no_keys(self):
        overrides = {"Discord": "bluez_output.AA_BB_CC_DD_EE_FF.1"}
        pruned, dead_keys = _prune_dead_overrides(overrides)
        assert dead_keys == []
        assert pruned == overrides

    def test_empty_overrides(self):
        pruned, dead_keys = _prune_dead_overrides({})
        assert pruned == {}
        assert dead_keys == []


# ── _process_tick wiring: the prune must actually reach the on-disk file ────

class _FakeSink:
    def __init__(self, index: int, name: str):
        self.index = index
        self.name = name


class _FakeServerInfo:
    def __init__(self, default_sink_name: str):
        self.default_sink_name = default_sink_name


class _FakePulse:
    """Minimal stand-in for pulsectl.Pulse — no sink-inputs needed for
    these tests, only enough of the tick to reach the prune."""

    def __init__(self, sinks: list, default_sink_name: str):
        self._sinks = sinks
        self._default_sink_name = default_sink_name
        self.moves: list[tuple[int, int]] = []

    def sink_list(self):
        return self._sinks

    def sink_input_list(self):
        return []

    def server_info(self):
        return _FakeServerInfo(self._default_sink_name)

    def sink_input_move(self, si_index, target_index):
        self.moves.append((si_index, target_index))


def test_process_tick_prunes_and_persists_dead_override(tmp_path):
    """End-to-end: a dead entry present in the on-disk overrides file is
    gone after one tick, and a device-shaped entry survives even though the
    corresponding sink is absent from the current graph."""
    overrides_file = tmp_path / "routing_overrides.json"
    overrides_file.write_text(json.dumps({
        "pw-cat": "effect_input.chaos-nan",
        "Discord": "bluez_output.AA_BB_CC_DD_EE_FF.1",
    }))

    game = _FakeSink(0, "Arctis_Game")
    pulse = _FakePulse([game], default_sink_name="Arctis_Game")

    # Skip the native-PipeWire-stream pass entirely — it's throttled by
    # _last_native_check, but that's shared module state other test modules
    # also touch, so pin it explicitly rather than rely on ordering. This
    # test only cares that the prune (which happens earlier in the tick)
    # reached disk.
    with patch("arctis_sound_manager.scripts.video_router.OVERRIDES_FILE", overrides_file), \
         patch("arctis_sound_manager.scripts.video_router.get_headset_power",
               return_value=HeadsetPower.ON), \
         patch.object(video_router, "_last_native_check", time.monotonic()), \
         patch("arctis_sound_manager.scripts.video_router.get_native_streams", return_value=[]):
        _process_tick(pulse)

    on_disk = json.loads(overrides_file.read_text())
    assert on_disk == {"Discord": "bluez_output.AA_BB_CC_DD_EE_FF.1"}, \
        "the dead pw-cat entry must be gone; the merely-absent Bluetooth " \
        "speaker override must survive untouched"


class TestUserOwnedFilterChainIsNotPruned:
    """`effect_input.<name>` is the naming convention of PipeWire
    filter-chains in general, not ASM's private namespace. Someone running
    their own chain and routing an app onto it through ASM's UI must not have
    that choice deleted on the next tick — which a purely structural test
    would do."""

    def test_a_loaded_foreign_chain_survives(self):
        overrides = {"Discord": "effect_input.my-own-eq"}
        pruned, dropped = video_router._prune_dead_overrides(
            overrides, {"effect_input.my-own-eq", "Arctis_Game"})
        assert dropped == []
        assert pruned == overrides

    def test_a_probe_node_that_is_gone_is_still_pruned(self):
        overrides = {"pw-cat": "effect_input.chaos-nan",
                     "Discord": "effect_input.my-own-eq"}
        pruned, dropped = video_router._prune_dead_overrides(
            overrides, {"effect_input.my-own-eq"})
        assert dropped == ["pw-cat"]
        assert pruned == {"Discord": "effect_input.my-own-eq"}

    def test_asm_own_nodes_are_exempt_from_the_liveness_half(self):
        """They legitimately vanish while the filter-chain restarts."""
        overrides = {"Discord": "effect_input.sonar-chat-eq"}
        pruned, dropped = video_router._prune_dead_overrides(overrides, set())
        assert dropped == []
        assert pruned == overrides

    def test_an_absent_device_is_never_pruned_however_long_it_is_gone(self):
        overrides = {"Spotify": "bluez_output.AA_BB_CC.1",
                     "mpv": "alsa_output.pci-0000_09_00.1.hdmi-stereo"}
        pruned, dropped = video_router._prune_dead_overrides(overrides, set())
        assert dropped == []
        assert pruned == overrides
