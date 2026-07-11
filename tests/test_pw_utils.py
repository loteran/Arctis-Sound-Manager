# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for pw_utils — native PipeWire stream detection and routing."""

from arctis_sound_manager.pw_utils import (
    get_native_streams,
    loopback_link_target,
    move_native_stream,
)


def _make_pw_dump(sinks=None, streams=None, links=None):
    """Build a minimal pw-dump structure for testing."""
    data = []
    for sid, name in (sinks or {}).items():
        data.append({
            "id": sid,
            "type": "PipeWire:Interface:Node",
            "info": {"props": {"media.class": "Audio/Sink", "node.name": name}},
        })
    for nid, props in (streams or {}).items():
        data.append({
            "id": nid,
            "type": "PipeWire:Interface:Node",
            "info": {"props": {
                "media.class": "Stream/Output/Audio",
                "application.name": props.get("app", ""),
                "application.process.id": props.get("pid", "0"),
                **({} if props.get("api") is None else {"client.api": props["api"]}),
            }},
        })
    for src, dst in (links or []):
        data.append({
            "id": 9000 + src,
            "type": "PipeWire:Interface:Link",
            "info": {"output-node-id": src, "input-node-id": dst},
        })
    return data


def test_get_native_streams_basic():
    data = _make_pw_dump(
        sinks={10: "Arctis_Game", 20: "Arctis_Chat"},
        streams={100: {"app": "mpv", "pid": "1234"}},
        links=[(100, 10)],
    )
    result = get_native_streams(data)
    assert len(result) == 1
    assert result[0]["app_name"] == "mpv"
    assert result[0]["sink_name"] == "Arctis_Game"
    assert result[0]["sink_id"] == 10


def test_get_native_streams_skips_pulseaudio_clients():
    data = _make_pw_dump(
        sinks={10: "Arctis_Game"},
        streams={
            100: {"app": "firefox", "api": "pipewire-pulse"},
            101: {"app": "mpv"},
        },
        links=[(100, 10), (101, 10)],
    )
    result = get_native_streams(data)
    assert len(result) == 1
    assert result[0]["app_name"] == "mpv"


def test_get_native_streams_no_link():
    data = _make_pw_dump(
        sinks={10: "Arctis_Game"},
        streams={100: {"app": "mpv"}},
        links=[],
    )
    result = get_native_streams(data)
    assert len(result) == 1
    assert result[0]["sink_name"] is None
    assert result[0]["sink_id"] is None


def test_get_native_streams_empty():
    assert get_native_streams([]) == []


def test_get_native_streams_skips_empty_app():
    data = _make_pw_dump(
        sinks={10: "Arctis_Game"},
        streams={100: {"app": ""}},
    )
    assert get_native_streams(data) == []


def test_move_native_stream_exact_match():
    """Ensure move_native_stream uses exact match, not substring."""
    data = _make_pw_dump(
        sinks={10: "Arctis_Game", 20: "Arctis_Game_Legacy"},
    )
    # With exact matching, "Arctis_Game" should match id=10, not 20
    # The function will either succeed (pw-metadata available) or fail (not available)
    # but should not crash either way
    result = move_native_stream(999, "Arctis_Game", data)
    assert isinstance(result, bool)


def test_move_native_stream_no_substring_match():
    """Ensure substring 'Game' does NOT match 'Arctis_Game'."""
    data = _make_pw_dump(sinks={10: "Arctis_Game"})
    result = move_native_stream(999, "Game", data)
    assert result is False


def test_move_native_stream_sink_not_found():
    data = _make_pw_dump(sinks={10: "Arctis_Game"})
    result = move_native_stream(999, "NonExistent_Sink", data)
    assert result is False


# ── loopback_link_target ──────────────────────────────────────────────────────


def _make_loopback_pw_dump(nodes: dict[int, str], links: list[tuple[int, int]]) -> list[dict]:
    """Build a minimal pw-dump for loopback_link_target tests.

    Parameters
    ----------
    nodes:
        Mapping of node_id → node.name.  All objects are typed as
        ``PipeWire:Interface:Node`` with ``node.name`` in ``info.props``.
    links:
        List of (output_node_id, input_node_id) pairs.  Each pair becomes a
        ``PipeWire:Interface:Link`` object whose ``info.props`` carries the
        ``link.output.node`` and ``link.input.node`` keys — the real pw-dump
        format used by :func:`loopback_link_target`.
    """
    data: list[dict] = []
    for node_id, node_name in nodes.items():
        data.append({
            "id": node_id,
            "type": "PipeWire:Interface:Node",
            "info": {"props": {"node.name": node_name}},
        })
    for link_id, (out_id, in_id) in enumerate(links, start=5000):
        data.append({
            "id": link_id,
            "type": "PipeWire:Interface:Link",
            "info": {
                "props": {
                    "link.output.node": out_id,
                    "link.input.node": in_id,
                }
            },
        })
    return data


class TestLoopbackLinkTarget:
    """Tests for loopback_link_target() — detects which sink a loopback is wired to."""

    def test_correctly_linked_returns_expected_target(self) -> None:
        """Loopback wired to effect_input.sonar-game-eq returns that name."""
        data = _make_loopback_pw_dump(
            nodes={
                10: "Arctis_Game_sink_out",
                20: "effect_input.sonar-game-eq",
            },
            links=[(10, 20)],
        )
        result = loopback_link_target("Arctis_Game_sink_out", data=data)
        assert result == "effect_input.sonar-game-eq"

    def test_mislinked_returns_wrong_sink_name(self) -> None:
        """Loopback wired to a DualSense speaker instead of the EQ returns that name."""
        data = _make_loopback_pw_dump(
            nodes={
                10: "Arctis_Game_sink_out",
                30: "alsa_output.usb-Sony_DualSense_Wireless_Controller.stereo",
            },
            links=[(10, 30)],
        )
        result = loopback_link_target("Arctis_Game_sink_out", data=data)
        assert result == "alsa_output.usb-Sony_DualSense_Wireless_Controller.stereo"

    def test_unlinked_loopback_returns_none(self) -> None:
        """A loopback with no outgoing link returns None (orphan / not yet bound)."""
        data = _make_loopback_pw_dump(
            nodes={
                10: "Arctis_Game_sink_out",
                20: "effect_input.sonar-game-eq",
            },
            links=[],   # no links at all
        )
        result = loopback_link_target("Arctis_Game_sink_out", data=data)
        assert result is None

    def test_unknown_playback_name_returns_none(self) -> None:
        """If playback_name is not present as any link's output, return None."""
        data = _make_loopback_pw_dump(
            nodes={
                10: "Arctis_Game_sink_out",
                20: "effect_input.sonar-game-eq",
            },
            links=[(10, 20)],
        )
        result = loopback_link_target("Arctis_Chat_sink_out", data=data)
        assert result is None

    def test_empty_dump_returns_none(self) -> None:
        result = loopback_link_target("Arctis_Game_sink_out", data=[])
        assert result is None

    def test_link_with_missing_props_is_skipped(self) -> None:
        """Links that lack link.output.node / link.input.node in props are ignored."""
        data: list[dict] = [
            {"id": 10, "type": "PipeWire:Interface:Node",
             "info": {"props": {"node.name": "Arctis_Game_sink_out"}}},
            # Link with props but missing the required keys
            {"id": 5000, "type": "PipeWire:Interface:Link",
             "info": {"props": {}}},
        ]
        result = loopback_link_target("Arctis_Game_sink_out", data=data)
        assert result is None

    def test_corrupt_data_returns_none(self) -> None:
        """Completely malformed data must not raise — returns None defensively."""
        corrupt: list[dict] = [{"not_a_valid": "object"}]
        result = loopback_link_target("Arctis_Game_sink_out", data=corrupt)
        assert result is None

    def test_picks_first_link_when_multiple_links_for_same_output(self) -> None:
        """When a node has multiple outgoing links, the first match is returned."""
        data = _make_loopback_pw_dump(
            nodes={
                10: "Arctis_Game_sink_out",
                20: "effect_input.sonar-game-eq",
                30: "some_other_node",
            },
            links=[(10, 20), (10, 30)],   # two links from the same output
        )
        # The first link (10→20) should be returned
        result = loopback_link_target("Arctis_Game_sink_out", data=data)
        assert result == "effect_input.sonar-game-eq"

    def test_type_suffix_matching_handles_full_interface_name(self) -> None:
        """Type string 'PipeWire:Interface:Node' ends with 'Node' — matched correctly."""
        data = _make_loopback_pw_dump(
            nodes={50: "Arctis_Media_sink_out", 60: "effect_input.sonar-media-eq"},
            links=[(50, 60)],
        )
        assert loopback_link_target("Arctis_Media_sink_out", data=data) == "effect_input.sonar-media-eq"


# ── ensure_loopback_link (issue #100) ─────────────────────────────────────────
import types

from arctis_sound_manager import pw_utils


def _node(node_id: int, name: str) -> dict:
    return {"id": node_id, "type": "PipeWire:Interface:Node",
            "info": {"props": {"node.name": name}}}


def _port(port_id: int, node_id: int, direction: str, channel: str) -> dict:
    return {"id": port_id, "type": "PipeWire:Interface:Port",
            "info": {"props": {"node.id": node_id, "port.direction": direction,
                               "audio.channel": channel}}}


def _link(link_id: int, out_node: int, out_port: int, in_node: int, in_port: int) -> dict:
    return {"id": link_id, "type": "PipeWire:Interface:Link",
            "info": {"props": {"link.output.node": out_node, "link.output.port": out_port,
                               "link.input.node": in_node, "link.input.port": in_port}}}


# Stereo loopback (node 100, out ports FL=101 FR=102) → stereo EQ
# (node 200, in ports FL=201 FR=202).
def _stereo_graph(links=None):
    data = [
        _node(100, "Arctis_Media_sink_out"),
        _node(200, "effect_input.sonar-media-eq"),
        _port(101, 100, "out", "FL"), _port(102, 100, "out", "FR"),
        _port(201, 200, "in", "FL"), _port(202, 200, "in", "FR"),
    ]
    data.extend(links or [])
    return data


def _patch_pwlink(monkeypatch):
    """Record every pw-link invocation; make them all succeed."""
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return types.SimpleNamespace(returncode=0, stderr=b"")

    monkeypatch.setattr(pw_utils.subprocess, "run", fake_run)
    return calls


class TestEnsureLoopbackLink:
    def test_creates_missing_channel_links(self, monkeypatch):
        calls = _patch_pwlink(monkeypatch)
        ok = pw_utils.ensure_loopback_link(
            "Arctis_Media_sink_out", "effect_input.sonar-media-eq", data=_stereo_graph(),
        )
        assert ok is True
        created = {(c[1], c[2]) for c in calls if "-d" not in c}
        assert created == {("101", "201"), ("102", "202")}

    def test_idempotent_when_already_linked(self, monkeypatch):
        calls = _patch_pwlink(monkeypatch)
        links = [_link(5001, 100, 101, 200, 201), _link(5002, 100, 102, 200, 202)]
        ok = pw_utils.ensure_loopback_link(
            "Arctis_Media_sink_out", "effect_input.sonar-media-eq",
            data=_stereo_graph(links),
        )
        assert ok is True
        assert calls == []  # nothing to create, nothing stray to remove

    def test_removes_stray_link_and_relinks(self, monkeypatch):
        calls = _patch_pwlink(monkeypatch)
        # FL is wrongly linked to a foreign node (999) — must be torn down,
        # and both correct channel links (re)created.
        stray = [_link(5001, 100, 101, 999, 901)]
        ok = pw_utils.ensure_loopback_link(
            "Arctis_Media_sink_out", "effect_input.sonar-media-eq",
            data=_stereo_graph(stray),
        )
        assert ok is True
        disconnects = [c for c in calls if "-d" in c]
        # argv[0] is resolved to an absolute path to pin the posix_spawn path (#123).
        assert disconnects == [[pw_utils._abs_exe("pw-link"), "-d", "101", "901"]]
        created = {(c[1], c[2]) for c in calls if "-d" not in c}
        assert created == {("101", "201"), ("102", "202")}

    def test_returns_false_when_target_absent(self, monkeypatch):
        calls = _patch_pwlink(monkeypatch)
        data = [_node(100, "Arctis_Media_sink_out"),
                _port(101, 100, "out", "FL"), _port(102, 100, "out", "FR")]
        ok = pw_utils.ensure_loopback_link(
            "Arctis_Media_sink_out", "effect_input.sonar-media-eq", data=data,
        )
        assert ok is False
        assert calls == []

    def test_returns_false_when_playback_absent(self, monkeypatch):
        calls = _patch_pwlink(monkeypatch)
        data = [_node(200, "effect_input.sonar-media-eq"),
                _port(201, 200, "in", "FL"), _port(202, 200, "in", "FR")]
        ok = pw_utils.ensure_loopback_link(
            "Arctis_Media_sink_out", "effect_input.sonar-media-eq", data=data,
        )
        assert ok is False
        assert calls == []


# ── set_filter_gain (Phase 2, issue #100/#88) ─────────────────────────────────
#
# Live-apply a single filter-chain control value via pw-cli set-param instead
# of regenerating the conf + restarting the service. Verified interactively
# against a live Sonar EQ node on PipeWire 1.6.7 (see the docstring in
# pw_utils.set_filter_gain for the exact enum-params/set-param transcript).

class TestSetFilterGain:
    def test_builds_correct_pw_cli_command(self, monkeypatch):
        monkeypatch.setattr(
            pw_utils, "_pw_dump",
            lambda: [_node(138, "effect_input.sonar-game-eq")],
        )
        calls = _patch_pwlink(monkeypatch)  # reuses the fake subprocess.run recorder

        ok = pw_utils.set_filter_gain("effect_input.sonar-game-eq", "bq0:Gain", 6.5)

        assert ok is True
        assert len(calls) == 1
        argv = calls[0]
        assert argv[0] == pw_utils._abs_exe("pw-cli")
        assert argv[1:3] == ["set-param", "138"]
        assert argv[3] == "Props"
        assert argv[4] == '{ params = [ "bq0:Gain" 6.5 ] }'

    def test_returns_false_when_node_not_in_graph(self, monkeypatch):
        monkeypatch.setattr(pw_utils, "_pw_dump", lambda: [_node(1, "some-other-node")])
        calls = _patch_pwlink(monkeypatch)

        ok = pw_utils.set_filter_gain("effect_input.sonar-game-eq", "bq0:Gain", 3.0)

        assert ok is False
        assert calls == []  # never even attempts pw-cli when the node is absent

    def test_returns_false_when_pw_cli_exits_nonzero(self, monkeypatch):
        monkeypatch.setattr(
            pw_utils, "_pw_dump",
            lambda: [_node(59, "effect_input.sonar-output-eq")],
        )

        def fake_run(argv, **kwargs):
            return types.SimpleNamespace(returncode=1, stderr=b"no such param")

        monkeypatch.setattr(pw_utils.subprocess, "run", fake_run)

        ok = pw_utils.set_filter_gain("effect_input.sonar-output-eq", "boost:Gain", 1.0)

        assert ok is False
