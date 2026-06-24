# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for utils — ObservableDict, JsonSerializable, project_version."""

import sys
import types
from pathlib import Path
from unittest import mock

from arctis_sound_manager.utils import ObservableDict, project_version


def test_observable_dict_notifies_on_change():
    changes = []
    d = ObservableDict()
    d.add_observer(lambda k, v: changes.append((k, v)))

    d["a"] = 1
    assert changes == [("a", 1)]

    d["a"] = 2
    assert changes == [("a", 1), ("a", 2)]


def test_observable_dict_no_notify_on_same_value():
    changes = []
    d = ObservableDict()
    d.add_observer(lambda k, v: changes.append((k, v)))

    d["a"] = 1
    d["a"] = 1  # same value — should NOT notify
    assert changes == [("a", 1)]


def test_observable_dict_update():
    changes = []
    d = ObservableDict()
    d.add_observer(lambda k, v: changes.append((k, v)))

    d.update({"x": 10, "y": 20})
    assert d["x"] == 10
    assert d["y"] == 20
    assert ("x", 10) in changes
    assert ("y", 20) in changes


def test_observable_dict_update_single_arg():
    d = ObservableDict()
    try:
        d.update({"a": 1}, {"b": 2})
    except TypeError:
        pass  # expected — update takes exactly 1 positional arg


def test_observable_dict_to_dict():
    d = ObservableDict({"a": 1, "b": 2})
    result = d.to_dict()
    assert result == {"a": 1, "b": 2}


def test_project_version_returns_string():
    v = project_version()
    assert isinstance(v, str)
    assert len(v) > 0


# ── project_version path-mapping tests ────────────────────────────────────

class _FakeDist:
    """Minimal importlib.metadata Distribution stub."""

    def __init__(self, version: str, root: Path):
        self.version = version
        self._root = root

    def locate_file(self, rel):
        return self._root / rel


def test_project_version_picks_distribution_by_path(tmp_path):
    """project_version resolves the dist whose root contains the running package."""
    # Simulate two installs in different prefixes
    stale_root = tmp_path / "user_site"
    fresh_root = tmp_path / "sys_site"

    pkg_dir = fresh_root / "arctis_sound_manager"
    pkg_dir.mkdir(parents=True)
    fake_init = pkg_dir / "__init__.py"
    fake_init.touch()

    stale_pkg_dir = stale_root / "arctis_sound_manager"
    stale_pkg_dir.mkdir(parents=True)

    import arctis_sound_manager

    # stale_dist reports 1.0.0, fresh_dist reports 1.0.86
    stale_dist = _FakeDist("1.0.0", stale_root)
    fresh_dist = _FakeDist("1.0.86", fresh_root)

    with (
        mock.patch.object(arctis_sound_manager, "__file__", str(fake_init)),
        mock.patch("arctis_sound_manager.utils.distributions", return_value=iter([stale_dist, fresh_dist])),
    ):
        v = project_version()

    assert v == "1.0.86", (
        f"project_version() must return the version of the distribution whose "
        f"root contains the running package file, got {v!r}"
    )


def test_project_version_fallback_on_no_match(tmp_path):
    """Falls back to name-based lookup when no distribution root matches."""
    pkg_dir = tmp_path / "arctis_sound_manager"
    pkg_dir.mkdir(parents=True)
    fake_init = pkg_dir / "__init__.py"
    fake_init.touch()

    import arctis_sound_manager

    # A distribution whose root is elsewhere — won't match pkg_dir
    other_root = tmp_path / "somewhere_else"
    other_root.mkdir(parents=True)
    non_matching_dist = _FakeDist("9.9.9", other_root)

    with (
        mock.patch.object(arctis_sound_manager, "__file__", str(fake_init)),
        mock.patch("arctis_sound_manager.utils.distributions", return_value=iter([non_matching_dist])),
        mock.patch(
            "arctis_sound_manager.utils.version",
            side_effect=lambda _: "1.0.50",
        ),
    ):
        v = project_version()

    assert v == "1.0.50", (
        "project_version() must fall back to the name-based lookup when no "
        "distribution root contains the running package"
    )


def test_project_version_final_fallback_dev(tmp_path):
    """Falls back to 'dev' when both path-mapping and name lookup fail."""
    pkg_dir = tmp_path / "arctis_sound_manager"
    pkg_dir.mkdir(parents=True)
    fake_init = pkg_dir / "__init__.py"
    fake_init.touch()

    import arctis_sound_manager
    from importlib.metadata import PackageNotFoundError

    with (
        mock.patch.object(arctis_sound_manager, "__file__", str(fake_init)),
        mock.patch("arctis_sound_manager.utils.distributions", return_value=iter([])),
        mock.patch("arctis_sound_manager.utils.version", side_effect=PackageNotFoundError),
    ):
        v = project_version()

    assert v == "dev"
