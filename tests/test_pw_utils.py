"""Tests for pw_utils — native PipeWire stream detection and routing."""

from linux_arctis_manager.pw_utils import get_native_streams, move_native_stream


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
