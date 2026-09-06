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

import fcntl
import os
import sys
import tempfile
import threading
import time
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

    def test_gui_unit_is_named_for_the_desktop_entry(self):
        # Not cosmetic: xdg-desktop-portal reads a non-sandboxed process's app id
        # off the unit its cgroup names. Under a name that does not match
        # app-<AppID>[-<random>], the id resolves empty, the GlobalShortcuts
        # portal answers "NotAllowed: An app id is required", and the clip
        # shortcut cannot bind. ArctisManager is ArctisManager.desktop's id.
        self.assertEqual(sc._resolve("arctis-gui", "systemd"), "app-ArctisManager")

    def test_unknown_name_passthrough(self):
        self.assertEqual(sc._resolve("some-other", "dinit"), "some-other")


class RestartSystemd(unittest.TestCase):
    def test_single_call_with_all_units(self):
        with mock.patch.object(sc, "detect_init", return_value="systemd"), \
             mock.patch.object(sc, "manager_available", return_value=True), \
             mock.patch("arctis_sound_manager.pw_utils.quiesce_filter_chain"), \
             mock.patch("subprocess.run", return_value=_ok()) as run:
            self.assertTrue(sc.restart("pipewire", "filter-chain", "arctis-manager"))
            # Absolute path + close_fds=False pin the posix_spawn (vfork) path so
            # the daemon never fork()s from its libusb-active process (issue #123).
            run.assert_called_once_with(
                [sc._abs_exe("systemctl"), "--user", "restart", "pipewire", "filter-chain", "arctis-manager"],
                check=False,
                close_fds=False,
            )


class RestartDinit(unittest.TestCase):
    def test_one_call_per_service_and_filterchain_mapped(self):
        with mock.patch.object(sc, "detect_init", return_value="dinit"), \
             mock.patch.object(sc, "manager_available", return_value=True), \
             mock.patch("arctis_sound_manager.pw_utils.quiesce_filter_chain"), \
             mock.patch("subprocess.run", return_value=_ok()) as run:
            self.assertTrue(sc.restart("filter-chain", "arctis-manager"))
        calls = [c.args[0] for c in run.call_args_list]
        self.assertEqual(calls, [
            [sc._abs_exe("dinitctl"), "restart", "pipewire-filter-chain"],
            [sc._abs_exe("dinitctl"), "restart", "arctis-manager"],
        ])

    def test_failure_propagates_as_false(self):
        with mock.patch.object(sc, "detect_init", return_value="dinit"), \
             mock.patch.object(sc, "manager_available", return_value=True), \
             mock.patch("arctis_sound_manager.pw_utils.quiesce_filter_chain"), \
             mock.patch("subprocess.run", side_effect=[_ok(), _ok(returncode=1)]):
            self.assertFalse(sc.restart("filter-chain", "arctis-manager"))

    def test_restart_quiesces_filter_chain_first(self):
        """#233: restarting filter-chain must park its graph first, for every
        caller — not just the one call site that remembered to do it."""
        with mock.patch.object(sc, "detect_init", return_value="dinit"), \
             mock.patch.object(sc, "manager_available", return_value=True), \
             mock.patch.object(sc, "_should_restart_filter_chain_now", return_value=True), \
             mock.patch("arctis_sound_manager.pw_utils.quiesce_filter_chain") as quiesce, \
             mock.patch("subprocess.run", return_value=_ok()):
            sc.restart("filter-chain")
        quiesce.assert_called_once()

    def test_restart_skips_quiesce_for_unrelated_services(self):
        with mock.patch.object(sc, "detect_init", return_value="dinit"), \
             mock.patch.object(sc, "manager_available", return_value=True), \
             mock.patch("arctis_sound_manager.pw_utils.quiesce_filter_chain") as quiesce, \
             mock.patch("subprocess.run", return_value=_ok()):
            sc.restart("pipewire", "arctis-manager")
        quiesce.assert_not_called()

    def test_restart_skips_entirely_when_coalescing_absorbs_it(self):
        with mock.patch.object(sc, "detect_init", return_value="dinit"), \
             mock.patch.object(sc, "manager_available", return_value=True), \
             mock.patch.object(sc, "_should_restart_filter_chain_now", return_value=False), \
             mock.patch("arctis_sound_manager.pw_utils.quiesce_filter_chain") as quiesce, \
             mock.patch("subprocess.run") as run:
            self.assertTrue(sc.restart("filter-chain"))
        quiesce.assert_not_called()
        run.assert_not_called()


class FilterChainCoalescing(unittest.TestCase):
    """#233: cross-process debounce for standalone filter-chain restarts.

    Uses a scratch dir for the lock/stamp files (never the real
    XDG_RUNTIME_DIR) and a zeroed coalescing window so these run instantly.
    """

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        tmp = Path(self._tmpdir.name)
        patches = [
            mock.patch.object(sc, "_FC_LOCK_PATH", tmp / "restart.lock"),
            mock.patch.object(sc, "_FC_STAMP_PATH", tmp / "restart.stamp"),
            mock.patch.object(sc, "_FC_COALESCE_WINDOW_S", 0.0),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_first_call_proceeds(self):
        self.assertTrue(sc._should_restart_filter_chain_now())

    def test_second_call_within_min_interval_is_absorbed(self):
        self.assertTrue(sc._should_restart_filter_chain_now())
        self.assertFalse(sc._should_restart_filter_chain_now())

    def test_call_after_min_interval_proceeds_again(self):
        self.assertTrue(sc._should_restart_filter_chain_now())
        sc._FC_STAMP_PATH.write_text(str(time.time() - sc._FC_MIN_INTERVAL_S - 1))
        self.assertTrue(sc._should_restart_filter_chain_now())

    def test_held_lock_absorbs_a_concurrent_caller(self):
        # Simulate a second process/thread already holding the lock: open our
        # own fd on the same path and flock it, as the real holder would.
        fd = os.open(sc._FC_LOCK_PATH, os.O_CREAT | os.O_RDWR, 0o600)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX)
        self.assertFalse(sc._should_restart_filter_chain_now())

    def test_concurrent_burst_collapses_to_one_restart(self):
        """End-to-end regression for #233: a burst of near-simultaneous
        restart("filter-chain") calls — as the GUI and the daemon can both
        fire for the same preset/surround change — must reach the actual
        service manager exactly once, not once per call.

        Manually verified live against the real filter-chain.service on
        2026-09-06: 10 concurrent calls (2 processes x 5 threads) produced
        exactly one Stopped/Started cycle in journalctl and no coredump.
        This locks the same behaviour in under the real service_control
        code path (not just _should_restart_filter_chain_now in isolation),
        with subprocess.run mocked so it never touches real services.
        """
        with mock.patch.object(sc, "detect_init", return_value="dinit"), \
             mock.patch.object(sc, "manager_available", return_value=True), \
             mock.patch("arctis_sound_manager.pw_utils.quiesce_filter_chain"), \
             mock.patch("subprocess.run", return_value=_ok()) as run:
            results = []
            results_lock = threading.Lock()

            def call():
                ok = sc.restart("filter-chain", timeout=15)
                with results_lock:
                    results.append(ok)

            threads = [threading.Thread(target=call) for _ in range(10)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5)

        self.assertEqual(results, [True] * 10, "every caller should see success")
        # Exactly one real dinitctl invocation reached the service manager.
        run.assert_called_once_with(
            [sc._abs_exe("dinitctl"), "restart", "pipewire-filter-chain"],
            check=False,
            close_fds=False,
            timeout=15,
        )


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
             mock.patch.object(sc, "_should_restart_filter_chain_now", return_value=True), \
             mock.patch("arctis_sound_manager.pw_utils.quiesce_filter_chain"), \
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
            [sc._abs_exe("dinitctl"), "enable", "arctis-manager"],
            [sc._abs_exe("dinitctl"), "start", "arctis-manager"],
        ])


if __name__ == "__main__":
    unittest.main()
