# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for stream_guard — which links into a screen-share capture survive.

Discord links every playback stream into its own ``discord_capture`` node, so
the guard has to decide per link whether the source belongs to an allowed
Arctis channel. The rule is structural, not name-based: a source is allowed
when it also feeds one of the allowed channel sinks.
"""
from __future__ import annotations

import json
import types

import pytest

from arctis_sound_manager.stream_guard import (CHANNEL_SINKS, GuardConfig,
                                               describe_cut, link_ports,
                                               links_to_cut, load_config,
                                               save_config)


# ── pw-dump fixture builders ──────────────────────────────────────────────

def _node(node_id: int, name: str) -> dict:
    return {
        "id": node_id,
        "type": "PipeWire:Interface:Node",
        "info": {"props": {"node.name": name}},
    }


def _link(
    link_id: int, out_node: int, in_node: int,
    out_port: int | None = None, in_port: int | None = None,
) -> dict:
    props = {
        "link.output.node": out_node,
        "link.input.node": in_node,
    }
    # Port ids are optional here — most of this module's tests only care
    # about node-level routing decisions. Tests exercising link_ports()
    # (CHA-3) pass them explicitly.
    if out_port is not None:
        props["link.output.port"] = out_port
    if in_port is not None:
        props["link.input.port"] = in_port
    return {
        "id": link_id,
        "type": "PipeWire:Interface:Link",
        "info": {"props": props},
    }


# Node ids mirroring the real graph: three Arctis channels, a capture node
# Discord created, and three apps sitting on different channels.
CAPTURE, GAME, CHAT, MEDIA = 100, 10, 11, 12
OBS, CHROME, VOICE = 20, 21, 22

GAME_ONLY = set(CHANNEL_SINKS["game"])


def _graph(*links: dict, with_capture: bool = True) -> list:
    nodes = [
        _node(GAME, "Arctis_Game"),
        _node(CHAT, "Arctis_Chat"),
        _node(MEDIA, "Arctis_Media"),
        _node(OBS, "OBS-Monitor"),
        _node(CHROME, "Google Chrome"),
        _node(VOICE, "WEBRTC VoiceEngine"),
    ]
    if with_capture:
        nodes.append(_node(CAPTURE, "discord_capture"))
    return [*nodes, *links]


# ── links_to_cut ──────────────────────────────────────────────────────────

def test_no_capture_node_means_nothing_to_cut():
    """The common case: no screen share running, guard must be inert."""
    dump = _graph(
        _link(1, OBS, GAME),
        _link(2, CHROME, MEDIA),
        with_capture=False,
    )
    assert links_to_cut(dump, GAME_ONLY) == []


def test_source_on_allowed_channel_survives():
    dump = _graph(
        _link(1, OBS, GAME),        # OBS plays on the Game channel
        _link(2, OBS, CAPTURE),     # …and Discord captures it
    )
    assert links_to_cut(dump, GAME_ONLY) == []


def test_source_on_another_channel_is_cut():
    dump = _graph(
        _link(1, CHROME, MEDIA),    # Chrome plays on Media
        _link(2, CHROME, CAPTURE),  # Discord grabbed it anyway
    )
    assert links_to_cut(dump, GAME_ONLY) == [2]


def test_chat_channel_is_cut_so_the_call_does_not_echo_itself():
    """Discord's own voice output must never be fed back into the share."""
    dump = _graph(
        _link(1, VOICE, CHAT),
        _link(2, VOICE, CAPTURE),
    )
    assert links_to_cut(dump, GAME_ONLY) == [2]


def test_only_capture_links_are_touched():
    """A stream on a disallowed channel keeps playing — we cut its capture
    link, never its link to the sink the user actually listens on."""
    dump = _graph(
        _link(1, CHROME, MEDIA),
        _link(2, CHROME, CAPTURE),
        _link(3, OBS, GAME),
        _link(4, OBS, CAPTURE),
    )
    assert links_to_cut(dump, GAME_ONLY) == [2]


def test_monitor_of_allowed_sink_survives():
    """Capture reading the channel sink's own monitor is the allowed case."""
    dump = _graph(_link(1, GAME, CAPTURE))
    assert links_to_cut(dump, GAME_ONLY) == []


def test_monitor_of_disallowed_sink_is_cut():
    dump = _graph(_link(1, MEDIA, CAPTURE))
    assert links_to_cut(dump, GAME_ONLY) == [1]


def test_filter_chain_input_counts_as_the_same_channel():
    """With the Sonar EQ on, a stream sits on effect_input.sonar-game-eq rather
    than Arctis_Game. The user means the same channel, so it must survive."""
    eq_game = 30
    dump = [
        _node(CAPTURE, "discord_capture"),
        _node(eq_game, "effect_input.sonar-game-eq"),
        _node(OBS, "OBS-Monitor"),
        _link(1, OBS, eq_game),
        _link(2, OBS, CAPTURE),
    ]
    assert links_to_cut(dump, GAME_ONLY) == []


def test_unrouted_source_is_cut():
    """A stream on no Arctis channel at all (e.g. straight to the hardware
    sink) has not been opted in, so it does not go on the stream."""
    stray = 40
    dump = _graph(
        _link(1, stray, CAPTURE),
    ) + [_node(stray, "some-notification-sound")]
    assert links_to_cut(dump, GAME_ONLY) == [1]


def test_multiple_channels_allowed():
    dump = _graph(
        _link(1, CHROME, MEDIA),
        _link(2, CHROME, CAPTURE),
        _link(3, VOICE, CHAT),
        _link(4, VOICE, CAPTURE),
    )
    allowed = set(CHANNEL_SINKS["game"]) | set(CHANNEL_SINKS["media"])
    assert links_to_cut(dump, allowed) == [4]


def test_empty_allowlist_cuts_everything():
    dump = _graph(
        _link(1, OBS, GAME),
        _link(2, OBS, CAPTURE),
        _link(3, CHROME, CAPTURE),
    )
    assert links_to_cut(dump, set()) == [2, 3]


def test_malformed_objects_are_skipped_not_fatal():
    """pw-dump can carry objects mid-teardown with info: null."""
    dump = _graph(_link(1, CHROME, CAPTURE)) + [
        {"id": 900, "type": "PipeWire:Interface:Link", "info": None},
        {"id": 901, "type": "PipeWire:Interface:Node", "info": None},
        {"id": 902, "type": "PipeWire:Interface:Port"},
    ]
    assert links_to_cut(dump, GAME_ONLY) == [1]


def test_capture_self_link_is_left_alone():
    dump = _graph(_link(1, CAPTURE, CAPTURE))
    assert links_to_cut(dump, GAME_ONLY) == []


def test_alternate_capture_node_name_is_policed():
    dump = [
        _node(CAPTURE, "vesktop_capture"),
        _node(CHROME, "Google Chrome"),
        _link(1, CHROME, CAPTURE),
    ]
    assert links_to_cut(dump, GAME_ONLY, capture_nodes=("vesktop_capture",)) == [1]


# ── describe_cut ──────────────────────────────────────────────────────────

def test_describe_cut_names_both_ends():
    dump = _graph(_link(7, CHROME, CAPTURE))
    assert describe_cut(dump, [7]) == ["Google Chrome -> discord_capture"]


def test_describe_cut_survives_unknown_link_id():
    assert describe_cut(_graph(), [999]) == ["#None -> #None"]


# ── link_ports (CHA-3: destroy by content-addressed port pair, not id) ────
#
# PipeWire recycles global object ids within seconds on a churning graph —
# exactly what Discord's screen share produces by relinking continuously.
# A link id captured in one pw-dump can name a completely different object
# by the time a caller destroys it. link_ports() is the hand-off point that
# converts a doomed link's id into its (output-port, input-port) pair from
# the SAME dump, so the actual destroy call (pw-link -d out in, in
# scripts/stream_guard.py) never uses a link id at all.

def test_link_ports_maps_id_to_port_pair():
    dump = _graph(_link(7, CHROME, CAPTURE, out_port=71, in_port=72))
    assert link_ports(dump, [7]) == [(71, 72)]


def test_link_ports_skips_id_absent_from_dump():
    """The ids normally come from links_to_cut() run against this very same
    dump, so a miss here only happens if the object is already gone —
    nothing to destroy either way, and this must not raise."""
    dump = _graph(_link(7, CHROME, CAPTURE, out_port=71, in_port=72))
    assert link_ports(dump, [7, 999]) == [(71, 72)]


def test_link_ports_preserves_caller_order():
    dump = _graph(
        _link(1, OBS, CAPTURE, out_port=11, in_port=12),
        _link(2, CHROME, CAPTURE, out_port=21, in_port=22),
    )
    assert link_ports(dump, [2, 1]) == [(21, 22), (11, 12)]


def test_link_ports_skips_link_missing_port_props():
    """A Link object without link.output.port/link.input.port — e.g. a
    pw-dump from a PipeWire version that omits them, or a malformed entry —
    is skipped rather than producing a (None, None) pair that would crash
    the eventual ``pw-link -d`` call."""
    dump = _graph(_link(7, CHROME, CAPTURE))  # no ports
    assert link_ports(dump, [7]) == []


def test_link_ports_empty_input():
    assert link_ports(_graph(_link(7, CHROME, CAPTURE, out_port=71, in_port=72)), []) == []


# ── GuardConfig ───────────────────────────────────────────────────────────

def test_allowed_sinks_expands_channels():
    cfg = GuardConfig(channels=("game",))
    assert cfg.allowed_sinks() == {"Arctis_Game", "effect_input.sonar-game-eq"}


def test_unknown_channel_names_are_dropped():
    cfg = GuardConfig(channels=("game", "bogus"))
    assert cfg.channels == ("game",)


def test_roundtrip(tmp_path):
    path = tmp_path / "stream_guard.json"
    save_config(GuardConfig(enabled=False, channels=("game", "media")), path)
    assert load_config(path) == GuardConfig(enabled=False, channels=("game", "media"))


def test_missing_config_defaults_to_enabled_game_only(tmp_path):
    """Fail closed: no config must not mean 'broadcast everything'."""
    cfg = load_config(tmp_path / "absent.json")
    assert cfg.enabled is True
    assert cfg.channels == ("game",)


@pytest.mark.parametrize("body", ["{ not json", "[]", '{"channels": 5}'])
def test_corrupt_config_falls_back_to_guarding(tmp_path, body):
    path = tmp_path / "stream_guard.json"
    path.write_text(body)
    cfg = load_config(path)
    assert cfg.enabled is True
    assert cfg.channels == ("game",)


def test_save_is_atomic_and_leaves_no_tmp(tmp_path):
    path = tmp_path / "stream_guard.json"
    save_config(GuardConfig(), path)
    assert json.loads(path.read_text()) == {"enabled": True, "channels": ["game"]}
    assert list(tmp_path.iterdir()) == [path]


# ── idle cost: the guard must not snapshot the graph when nothing captures it ─
#
# The first version re-dumped the whole graph every 5 s regardless, which cost
# ~12 % CPU around the clock on a machine that was not sharing anything — more
# than the polling loop the event-driven design was meant to replace. The
# safety net now backs off while no capture node exists, which is only safe as
# long as the two predicates below keep telling the truth: `capture_node_present`
# decides the cadence, and `_mentions_capture_node` is what wakes the guard when
# a share starts. If either silently stops matching, the guard costs nothing and
# does nothing — the worst of both.

from arctis_sound_manager.scripts import stream_guard as scripts_stream_guard  # noqa: E402
from arctis_sound_manager.scripts.stream_guard import (  # noqa: E402
    _destroy_links, _mentions_capture_node, capture_node_present)


def test_capture_node_present_detects_the_node():
    assert capture_node_present(_graph(_link(1, OBS, CAPTURE))) is True


def test_capture_node_present_false_without_one():
    assert capture_node_present(_graph(_link(1, OBS, GAME), with_capture=False)) is False


# ── _destroy_links (CHA-3) ──────────────────────────────────────────────────
#
# The daemon side of the same fix: destruction goes through pw-link -d with
# the two port ids link_ports() resolved from the dump, never pw-cli destroy
# <link-id>. Pinning the exact argv here is required, not decorative — a
# test that only mocks the subprocess and checks a return value would pass
# just as happily whether the code called "pw-cli destroy 7" or
# "pw-link -d 71 72"; only asserting the literal argv list would have caught
# a regression back to the id-based call. The real command was additionally
# run by hand once against a throwaway loopback pair (see the session
# report) since this replaces the argv shape handed to a PipeWire CLI tool.

def _fake_pw_run_recorder(calls: list[list[str]], returncode: int = 0, stderr: bytes = b""):
    def fake_pw_run(argv, **kwargs):
        calls.append(argv)
        return types.SimpleNamespace(returncode=returncode, stderr=stderr, stdout="")
    return fake_pw_run


def test_destroy_links_uses_pw_link_dash_d_with_port_pair(monkeypatch):
    """Pins the exact argv: ['pw-link', '-d', <out-port>, <in-port>] — never
    ['pw-cli', 'destroy', <link-id>]."""
    calls: list[list[str]] = []
    monkeypatch.setattr(scripts_stream_guard, "_pw_run", _fake_pw_run_recorder(calls))

    dump = _graph(_link(7, CHROME, CAPTURE, out_port=71, in_port=72))
    destroyed = _destroy_links(dump, [7])

    assert destroyed == 1
    assert calls == [["pw-link", "-d", "71", "72"]]


def test_destroy_links_never_uses_pw_cli_destroy(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(scripts_stream_guard, "_pw_run", _fake_pw_run_recorder(calls))

    dump = _graph(
        _link(1, OBS, CAPTURE, out_port=11, in_port=12),
        _link(2, CHROME, CAPTURE, out_port=21, in_port=22),
    )
    _destroy_links(dump, [1, 2])

    assert all(argv[:2] != ["pw-cli", "destroy"] for argv in calls)
    assert all(argv[:2] == ["pw-link", "-d"] for argv in calls)


def test_destroy_links_skips_id_no_longer_in_the_dump(monkeypatch):
    """The link this call was told to cut is no longer in *dump* at all
    (e.g. dump was taken, then this same tick recomputed doomed against a
    fresher one) — nothing to destroy, and no PipeWire CLI call is made."""
    calls: list[list[str]] = []
    monkeypatch.setattr(scripts_stream_guard, "_pw_run", _fake_pw_run_recorder(calls))

    dump = _graph()  # link 7 absent
    destroyed = _destroy_links(dump, [7])

    assert destroyed == 0
    assert calls == []


def test_destroy_links_failure_is_counted_not_raised(monkeypatch):
    """A destroy that fails because the link is already gone (Discord tore
    it down itself) is the expected, benign case — logged, not raised."""
    calls: list[list[str]] = []
    monkeypatch.setattr(
        scripts_stream_guard, "_pw_run",
        _fake_pw_run_recorder(calls, returncode=1, stderr=b"No such link"),
    )

    dump = _graph(_link(7, CHROME, CAPTURE, out_port=71, in_port=72))
    destroyed = _destroy_links(dump, [7])  # must not raise

    assert destroyed == 0
    assert calls == [["pw-link", "-d", "71", "72"]]


def test_capture_node_present_ignores_similar_names():
    dump = [_node(CAPTURE, "discord_capture_something_else"), _node(OBS, "OBS-Monitor")]
    assert capture_node_present(dump) is False


def test_mentions_capture_node_spots_a_starting_share():
    """PipeWire names the node when Discord creates it — that is the wake-up."""
    chunk = (b'  added:\n\tid: 214\n\ttype: PipeWire:Interface:Node/3\n'
             b'\t\tnode.name = "discord_capture"\n\t\tmedia.class = "Audio/Sink"\n')
    assert _mentions_capture_node(chunk) is True


def test_mentions_capture_node_ignores_ordinary_churn():
    """Volume moves and stream starts must not wake the guard on an idle graph."""
    chunk = (b'  changed:\n\tid: 96\n\ttype: PipeWire:Interface:Node/3\n'
             b'\t\tnode.name = "Google Chrome"\n\t\tchannelVolumes: [ 0.5, 0.5 ]\n')
    assert _mentions_capture_node(chunk) is False


def test_mentions_capture_node_handles_an_empty_burst():
    assert _mentions_capture_node(b"") is False
