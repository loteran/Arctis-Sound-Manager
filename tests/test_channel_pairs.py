# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the channel-pair resolution helpers (issue #129).

``ensure_loopback_link``/``ensure_capture_link`` used to match ports by
``audio.channel`` NAME only (FL→FL, FR→FR, …). That silently produced zero
links when a positioned 8ch source (the Sonar Media EQ output) was routed
directly into the Arctis's physical ``pro-output-0`` sink, whose ports are
named ``AUX0``/``AUX1`` instead of ``FL``/``FR`` — this is what happens when
Spatial Audio is OFF (the ON path instead targets the HeSuVi sink, which IS
named FL/FR, so WirePlumber's own position-based fan-out to AUX0/AUX1 kicks
in downstream and the bug never showed up there).

``_resolve_channel_pairs`` keeps name-matching as the default (it must
reproduce byte-identical pairs for every case that already works — stereo
loopback → stereo EQ, 8ch EQ → 8ch HeSuVi, mono mic → mono capture, …) and
only falls back to positional matching when literally no channel name is
shared between the two sides.
"""

from arctis_sound_manager.pw_utils import _channel_sort_key, _resolve_channel_pairs


class TestChannelSortKey:
    def test_canonical_channels_sort_in_canonical_order(self) -> None:
        channels = ["SR", "FR", "LFE", "FL", "RL", "FC", "SL", "RR"]
        assert sorted(channels, key=_channel_sort_key) == [
            "FL", "FR", "FC", "LFE", "RL", "RR", "SL", "SR",
        ]

    def test_aux_channels_sort_numerically_not_lexicographically(self) -> None:
        # Lexicographic order would put "AUX10" before "AUX2" — must not happen.
        channels = ["AUX10", "AUX2", "AUX0", "AUX1"]
        assert sorted(channels, key=_channel_sort_key) == [
            "AUX0", "AUX1", "AUX2", "AUX10",
        ]

    def test_positioned_channels_sort_before_aux_channels(self) -> None:
        channels = ["AUX1", "FR", "AUX0", "FL"]
        assert sorted(channels, key=_channel_sort_key) == ["FL", "FR", "AUX0", "AUX1"]

    def test_unknown_channels_sort_last_and_alphabetically(self) -> None:
        channels = ["ZZZ", "FL", "AUX0", "AAA"]
        assert sorted(channels, key=_channel_sort_key) == ["FL", "AUX0", "AAA", "ZZZ"]


class TestResolveChannelPairs:
    def test_stereo_name_match(self) -> None:
        """FL/FR → FL/FR: name-matched pairs, same ports as before the fix."""
        out_ports = {"FL": 101, "FR": 102}
        in_ports = {"FL": 201, "FR": 202}
        assert _resolve_channel_pairs(out_ports, in_ports) == [(101, 201), (102, 202)]

    def test_positional_fallback_8ch_to_aux(self) -> None:
        """8ch positioned EQ output → AUX0/AUX1 pro-audio sink (issue #129):
        no channel name is shared, so the fallback kicks in and pairs FL/FR
        (first in canonical order) with AUX0/AUX1 — exactly the two channels
        that carry a stereo mix, restoring audio when Spatial Audio is OFF."""
        out_ports = {
            "FL": 1, "FR": 2, "FC": 3, "LFE": 4,
            "RL": 5, "RR": 6, "SL": 7, "SR": 8,
        }
        in_ports = {"AUX0": 901, "AUX1": 902}
        assert _resolve_channel_pairs(out_ports, in_ports) == [(1, 901), (2, 902)]

    def test_no_fallback_when_any_name_is_shared(self) -> None:
        """8ch source → 2ch target that DOES use positioned names (FL/FR):
        name-matching succeeds (FL/FR are common), so only those 2 pairs are
        produced and the fallback must NOT trigger."""
        out_ports = {
            "FL": 1, "FR": 2, "FC": 3, "LFE": 4,
            "RL": 5, "RR": 6, "SL": 7, "SR": 8,
        }
        in_ports = {"FL": 201, "FR": 202}
        assert _resolve_channel_pairs(out_ports, in_ports) == [(1, 201), (2, 202)]

    def test_empty_out_ports_returns_empty_list(self) -> None:
        assert _resolve_channel_pairs({}, {"FL": 201, "FR": 202}) == []

    def test_empty_in_ports_returns_empty_list(self) -> None:
        assert _resolve_channel_pairs({"FL": 101, "FR": 102}, {}) == []

    def test_both_empty_returns_empty_list(self) -> None:
        assert _resolve_channel_pairs({}, {}) == []

    def test_aux_source_sorts_numerically_in_fallback(self) -> None:
        """A positioned target fed by an AUX-named source (the reverse of
        the reported bug) must still sort AUX0 < AUX1 < AUX2 numerically."""
        out_ports = {"AUX1": 12, "AUX0": 11, "AUX2": 13}
        in_ports = {"RL": 201, "FL": 202, "FR": 203}
        # in_ports canonical order: FL, FR, RL -> ports 202, 203, 201
        # out_ports numeric order: AUX0, AUX1, AUX2 -> ports 11, 12, 13
        assert _resolve_channel_pairs(out_ports, in_ports) == [
            (11, 202), (12, 203), (13, 201),
        ]
