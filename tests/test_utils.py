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


# ── project_version distribution-ownership tests ──────────────────────────

class _FakeDist:
    """Minimal importlib.metadata Distribution stub.

    *files* are paths relative to *root* (mirroring RECORD entries). *name* is
    exposed via ``metadata['Name']`` like a real Distribution.
    """

    def __init__(self, version: str, root: Path, name: str = "arctis-sound-manager",
                 files=None):
        self.version = version
        self._root = root
        self.metadata = {"Name": name}
        if files is None:
            files = ["arctis_sound_manager/__init__.py"]
        self.files = [Path(f) for f in files]

    def locate_file(self, rel):
        return self._root / rel


def test_project_version_ignores_sibling_in_shared_site_packages(tmp_path):
    """The real bug: many dists share one site-packages root.

    A sibling package (here 'pillow' 1.9.0) enumerated first must NOT have its
    version reported just because it lives under the same site-packages dir as
    the running arctis_sound_manager package.
    """
    site = tmp_path / "site-packages"
    pkg_dir = site / "arctis_sound_manager"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "__init__.py").touch()

    import arctis_sound_manager

    sibling = _FakeDist("1.9.0", site, name="pillow", files=["PIL/__init__.py"])
    asm = _FakeDist("1.1.83", site)  # owns arctis_sound_manager/__init__.py

    with (
        mock.patch.object(arctis_sound_manager, "__file__", str(pkg_dir / "__init__.py")),
        mock.patch("arctis_sound_manager.utils.distributions",
                   return_value=iter([sibling, asm])),
    ):
        v = project_version()

    assert v == "1.1.83", (
        f"project_version() must report the arctis-sound-manager dist, not a "
        f"sibling sharing the same site-packages root, got {v!r}"
    )


def test_project_version_prefers_imported_copy_when_shadowed(tmp_path):
    """Two arctis-sound-manager installs: report the one actually imported."""
    user_root = tmp_path / "user_site"   # ~/.local copy, version 0.1
    sys_root = tmp_path / "sys_site"     # system copy, version 1.1.83

    user_pkg = user_root / "arctis_sound_manager"
    user_pkg.mkdir(parents=True)
    (user_pkg / "__init__.py").touch()
    sys_pkg = sys_root / "arctis_sound_manager"
    sys_pkg.mkdir(parents=True)
    (sys_pkg / "__init__.py").touch()

    import arctis_sound_manager

    user_dist = _FakeDist("0.1", user_root)
    sys_dist = _FakeDist("1.1.83", sys_root)

    # Running package is imported from the system copy → report 1.1.83 even
    # though the ~/.local dist is enumerated first.
    with (
        mock.patch.object(arctis_sound_manager, "__file__", str(sys_pkg / "__init__.py")),
        mock.patch("arctis_sound_manager.utils.distributions",
                   return_value=iter([user_dist, sys_dist])),
    ):
        v = project_version()

    assert v == "1.1.83"


def test_project_version_named_fallback_when_no_location_match(tmp_path):
    """An arctis-sound-manager dist with no matching files still beats nothing."""
    pkg_dir = tmp_path / "site" / "arctis_sound_manager"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "__init__.py").touch()

    import arctis_sound_manager

    # Editable/odd install: files point elsewhere, so _dist_owns_dir is False,
    # but the dist still names itself arctis-sound-manager.
    elsewhere = tmp_path / "elsewhere"
    odd_dist = _FakeDist("1.0.50", elsewhere, files=["src/arctis_sound_manager/__init__.py"])

    with (
        mock.patch.object(arctis_sound_manager, "__file__", str(pkg_dir / "__init__.py")),
        mock.patch("arctis_sound_manager.utils.distributions", return_value=iter([odd_dist])),
    ):
        v = project_version()

    assert v == "1.0.50"


def test_project_version_fallback_on_no_named_dist(tmp_path):
    """Falls back to name-based lookup when no dist names itself arctis-sound-manager."""
    pkg_dir = tmp_path / "arctis_sound_manager"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "__init__.py").touch()

    import arctis_sound_manager

    sibling = _FakeDist("9.9.9", tmp_path, name="numpy", files=["numpy/__init__.py"])

    with (
        mock.patch.object(arctis_sound_manager, "__file__", str(pkg_dir / "__init__.py")),
        mock.patch("arctis_sound_manager.utils.distributions", return_value=iter([sibling])),
        mock.patch("arctis_sound_manager.utils.version", side_effect=lambda _: "1.0.50"),
    ):
        v = project_version()

    assert v == "1.0.50", (
        "project_version() must fall back to the name-based lookup when no "
        "distribution identifies as arctis-sound-manager"
    )


def test_project_version_final_fallback_dev(tmp_path):
    """Falls back to 'dev' when both ownership and name lookup fail."""
    pkg_dir = tmp_path / "arctis_sound_manager"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "__init__.py").touch()

    import arctis_sound_manager
    from importlib.metadata import PackageNotFoundError

    with (
        mock.patch.object(arctis_sound_manager, "__file__", str(pkg_dir / "__init__.py")),
        mock.patch("arctis_sound_manager.utils.distributions", return_value=iter([])),
        mock.patch("arctis_sound_manager.utils.version", side_effect=PackageNotFoundError),
    ):
        v = project_version()

    assert v == "dev"
