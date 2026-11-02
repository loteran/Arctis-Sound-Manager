"""asm-cli tools probe: read-only queries, every raw reply recorded."""

import asyncio
from unittest.mock import MagicMock

import pytest

from arctis_sound_manager.core import CoreEngine
from tests.test_hardware_eq import DEVICES, _config


def _engine(config):
    engine = MagicMock()
    engine.device_config = config
    engine.usb_device = MagicMock(idProduct=0x2290)
    engine._frame_taps = []
    engine.get_command_endpoint_address.return_value = 0
    return engine


def test_probe_records_every_frame_after_each_query_and_reads_features():
    """The reply may begin with a report id or the opcode depending on the
    family, so the probe keeps every frame of the window, and a `feature`
    query is answered by GET_REPORT with the spec's report id."""
    engine = _engine(_config("nova_pro_omni.yaml"))
    sent = []

    def send(frame, endpoint):
        sent.append(frame)
        for tap in engine._frame_taps:  # what listen_endpoint_loop would do
            tap.append([0x01, frame[1], 0x2A])
        return True

    engine.send_command.side_effect = send
    engine.get_feature_report.return_value = [0x01, 0x1A, 0x04]

    result = asyncio.run(CoreEngine.probe_device(engine, window=0))

    assert result["ok"] and result["product_id"] == "2290"
    queries = engine.device_config.probe_queries
    assert sent == [q["frame"] for q in queries]
    eq = next(r for r in result["replies"] if r["name"] == "read_radio_eq_settings")
    assert eq["frames"] == ["01 1a 2a"] and eq["feature"] == "01 1a 04"
    engine.get_feature_report.assert_any_call(0x01, 1036)
    assert engine._frame_taps == []  # nothing left listening


def test_probe_without_queries_says_so():
    engine = _engine(MagicMock(probe_queries=[]))
    assert asyncio.run(CoreEngine.probe_device(engine)) == {
        "ok": False, "error": "no_probe_queries"}


@pytest.mark.parametrize("profile", sorted(p.name for p in DEVICES.glob("*.yaml")))
def test_probe_queries_are_bare_queries(profile):
    """Only constants go out: a query frame is a report id and an opcode,
    at most a fixed selector byte — never a value that could set something."""
    for q in _config(profile).probe_queries:
        assert 1 <= len(q["frame"]) <= 2, q
        assert q["reply"] in ("interrupt", "feature")



def test_a_reply_is_decoded_from_its_opcode_whichever_byte_it_sits_on():
    """Offsets count from the opcode, found first or after the report id; a
    status frame that arrived in the same window is not mistaken for it."""
    from arctis_sound_manager.cli_tools import probe_field_values

    entry = {"sent": "06 20", "fields": {"volume": 2, "name": [3, 6]},
             "frames": ["06 b0 00 02", "06 20 00 15 41 42 43 00 00 00"]}
    assert probe_field_values(entry) == {"volume": 0x15, "name": "ABC"}

    bare = {"sent": "20", "fields": {"volume": 2}, "frames": ["20 00 33"]}
    assert probe_field_values(bare) == {"volume": 0x33}

    assert probe_field_values({"sent": "06 80", "fields": {"x": 1}, "frames": []}) is None


def test_every_profile_field_points_inside_a_64_byte_frame_or_its_feature_size():
    for path in sorted(DEVICES.glob("*.yaml")):
        for q in _config(path.name).probe_queries:
            limit = q["size"] if q["reply"] == "feature" else 64
            for name, where in q["fields"].items():
                at, size = (where, 1) if isinstance(where, int) else where
                assert 0 < at and at + size <= limit, (path.name, q["name"], name)


def test_nova5_probe_decodes_bands_and_settings_at_their_wire_offsets():
    """A real Nova 5 reply (#331): bands are 6 bytes on the wire, not the
    spec's 11-byte engine form, so mic volume sits at 61, not 111."""
    from ruamel.yaml import YAML

    from arctis_sound_manager.cli_tools import probe_field_values

    profile = YAML(typ="safe").load(DEVICES / "nova_5.yaml")
    query = next(q for q in profile["device"]["probe_queries"]
                 if q["name"] == "audio_settings")
    entry = {"sent": "20", "fields": query["fields"], "frames": [
        "20 1f 00 01 00 82 05 3e 00 01 00 82 05 7d 00 01 00 82 05 fa 00 01 00 82 05"
        " f4 01 01 00 82 05 e8 03 01 00 82 05 d0 07 01 00 82 05 a0 0f 01 00 82 05"
        " 40 1f 01 00 82 05 80 3e 01 00 82 05 0f 00 00"]}
    values = probe_field_values(entry)
    assert values["eq_band_1"] == "31 Hz, type 1, +0.0 dB, Q 1.41"
    assert values["eq_band_10"] == "16000 Hz, type 1, +0.0 dB, Q 1.41"
    assert (values["mic_volume"], values["sidetone"], values["volume_limiter"]) == (15, 0, 0)


def test_probe_pauses_the_status_poll_and_drops_its_pending_replies():
    """#363: on the Arctis Pro Wireless the status poll's untagged replies
    landed inside the probe's windows and were filed as probe replies."""
    engine = _engine(_config("nova_pro_omni.yaml"))
    engine._status_poll_paused = False
    seen = []

    def send(frame, endpoint):
        seen.append(engine._status_poll_paused)
        return True

    engine.send_command.side_effect = send
    engine.get_feature_report.return_value = None

    asyncio.run(CoreEngine.probe_device(engine, window=0))

    assert seen and all(p is True for p in seen)
    engine._untagged_requests_pending.clear.assert_called_once()
    assert engine._status_poll_paused is False


def test_paused_poll_sends_nothing_but_still_reconciles(monkeypatch):
    from tests.test_nova_5_push_events import _poll_engine, _run_ticks
    engine = _poll_engine(2.0)
    engine._status_poll_paused = True
    _run_ticks(monkeypatch, engine, 3)
    engine.request_device_status.assert_not_called()
    assert engine.reconcile_hardware_eq_mode.call_count == 3
