"""Tests for video_router — override loading/saving."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from linux_arctis_manager.scripts.video_router import load_overrides, save_overrides


def test_load_overrides_missing_file():
    with patch("linux_arctis_manager.scripts.video_router.OVERRIDES_FILE", Path("/nonexistent/path.json")):
        assert load_overrides() == {}


def test_load_overrides_valid_json(tmp_path):
    f = tmp_path / "overrides.json"
    f.write_text(json.dumps({"firefox": "Arctis_Game"}))
    with patch("linux_arctis_manager.scripts.video_router.OVERRIDES_FILE", f):
        result = load_overrides()
    assert result == {"firefox": "Arctis_Game"}


def test_load_overrides_invalid_json(tmp_path):
    f = tmp_path / "overrides.json"
    f.write_text("not valid json{{{")
    with patch("linux_arctis_manager.scripts.video_router.OVERRIDES_FILE", f):
        result = load_overrides()
    assert result == {}


def test_save_overrides_atomic(tmp_path):
    f = tmp_path / "overrides.json"
    overrides = {"mpv": "Arctis_Chat", "vlc": "Arctis_Game"}
    with patch("linux_arctis_manager.scripts.video_router.OVERRIDES_FILE", f):
        save_overrides(overrides)
    assert json.loads(f.read_text()) == overrides
    # tmp file should be cleaned up (replaced)
    assert not (tmp_path / "overrides.tmp").exists()


def test_save_then_load_roundtrip(tmp_path):
    f = tmp_path / "overrides.json"
    data = {"app1": "sink1", "app2": "sink2"}
    with patch("linux_arctis_manager.scripts.video_router.OVERRIDES_FILE", f):
        save_overrides(data)
        loaded = load_overrides()
    assert loaded == data
