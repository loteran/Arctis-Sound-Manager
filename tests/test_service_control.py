# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression tests for the init-system abstraction (service_control).

Run with:  python3 -m unittest tests.test_service_control  (from repo root,
with src/ on PYTHONPATH) or via the project's test runner.

These lock the behaviour that prevents issue #25 from regressing:
* the ``filter-chain`` -> ``pipewire-filter-chain`` mapping on dinit;
* ``restart`` (not ``start``) being used to apply new configs;
* graceful no-op (no FileNotFoundError) when the init manager is absent.
"""

import sys
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from arctis_sound_manager import service_control as sc  # noqa: E402


def _ok(returncode=0, stdout="", stderr=""):
    return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


class ResolveMapping(unittest.TestCase):
    def test_filter_chain_maps_per_init(self):
        self.assertEqual(sc._resolve("filter-chain", "systemd"), "filter-chain")
        self.assertEqual(sc._resolve("filter-chain", "dinit"), "pipewire-filter-chain")

    def test_gui_has_no_dinit_service(self):
        self.assertIsNone(sc._resolve("arctis-gui", "dinit"))
        self.assertEqual(sc._resolve("arctis-gui", "systemd"), "arctis-gui")

    def test_unknown_name_passthrough(self):
        self.assertEqual(sc._resolve("some-other", "dinit"), "some-other")


class RestartSystemd(unittest.TestCase):
    def test_single_call_with_all_units(self):
        with mock.patch.object(sc, "detect_init", return_value="systemd"), \
             mock.patch.object(sc, "manager_available", return_value=True), \
             mock.patch("subprocess.run", return_value=_ok()) as run:
            self.assertTrue(sc.restart("pipewire", "filter-chain", "arctis-manager"))
            run.assert_called_once_with(
                ["systemctl", "--user", "restart", "pipewire", "filter-chain", "arctis-manager"],
                check=False,
            )


class RestartDinit(unittest.TestCase):
    def test_one_call_per_service_and_filterchain_mapped(self):
        with mock.patch.object(sc, "detect_init", return_value="dinit"), \
             mock.patch.object(sc, "manager_available", return_value=True), \
             mock.patch("subprocess.run", return_value=_ok()) as run:
            self.assertTrue(sc.restart("filter-chain", "arctis-manager"))
        calls = [c.args[0] for c in run.call_args_list]
        self.assertEqual(calls, [
            ["dinitctl", "restart", "pipewire-filter-chain"],
            ["dinitctl", "restart", "arctis-manager"],
        ])

    def test_failure_propagates_as_false(self):
        with mock.patch.object(sc, "detect_init", return_value="dinit"), \
             mock.patch.object(sc, "manager_available", return_value=True), \
             mock.patch("subprocess.run", side_effect=[_ok(), _ok(returncode=1)]):
            self.assertFalse(sc.restart("filter-chain", "arctis-manager"))


class GuiSkippedOnDinit(unittest.TestCase):
    def test_arctis_gui_restart_is_noop_true(self):
        with mock.patch.object(sc, "detect_init", return_value="dinit"), \
             mock.patch.object(sc, "manager_available", return_value=True), \
             mock.patch("subprocess.run", return_value=_ok()) as run:
            self.assertTrue(sc.restart("arctis-gui"))
            run.assert_not_called()


class NoManagerNeverCrashes(unittest.TestCase):
    def test_unknown_init_returns_false(self):
        with mock.patch.object(sc, "detect_init", return_value="unknown"), \
             mock.patch.object(sc, "manager_available", return_value=False), \
             mock.patch("subprocess.run") as run:
            self.assertFalse(sc.restart("filter-chain"))
            run.assert_not_called()

    def test_missing_binary_does_not_raise(self):
        # Even if manager_available lies, _run must swallow FileNotFoundError.
        with mock.patch.object(sc, "detect_init", return_value="systemd"), \
             mock.patch.object(sc, "manager_available", return_value=True), \
             mock.patch("subprocess.run", side_effect=FileNotFoundError):
            self.assertFalse(sc.restart("filter-chain"))


class EnableNow(unittest.TestCase):
    def test_dinit_enable_now_enables_then_starts(self):
        with mock.patch.object(sc, "detect_init", return_value="dinit"), \
             mock.patch.object(sc, "manager_available", return_value=True), \
             mock.patch("subprocess.run", return_value=_ok()) as run:
            self.assertTrue(sc.enable("arctis-manager", now=True))
        calls = [c.args[0] for c in run.call_args_list]
        self.assertEqual(calls, [
            ["dinitctl", "enable", "arctis-manager"],
            ["dinitctl", "start", "arctis-manager"],
        ])


if __name__ == "__main__":
    unittest.main()
