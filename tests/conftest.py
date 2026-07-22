# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Suite-wide test safety nets."""

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_proc_root_suitewide(tmp_path_factory, monkeypatch):
    """Keep the orphan-loopback sweep away from the real ``/proc``, everywhere.

    ``LoopbackManager.start()`` sweeps ``/proc`` for orphaned ``pw-loopback``
    survivors and SIGTERMs any process whose capture-side ``node.name``
    matches the channel it is about to launch. This suite runs on developer
    machines that have a *live* ASM daemon with real ``Arctis_Game`` /
    ``Arctis_Chat`` / ``Arctis_Media`` loopbacks: a test that reaches the
    sweep against the real ``/proc`` would kill the developer's own audio
    routing mid-run.

    ``tests/test_loopback_manager.py`` has its own module-level fixture for
    this, but the danger is not confined to that file — any future test that
    ends up calling ``start()`` inherits it. Pinning ``_PROC_ROOT`` to an
    empty directory for the whole suite makes the safe behaviour the default
    rather than something each new test file has to remember.

    Tests that exercise the sweep itself override ``_PROC_ROOT`` locally,
    which still works: this fixture only sets the baseline.
    """
    try:
        from arctis_sound_manager import loopback_manager
    except Exception:  # pragma: no cover - module import is not this fixture's job
        return
    empty = tmp_path_factory.mktemp("empty_proc")
    monkeypatch.setattr(loopback_manager, "_PROC_ROOT", str(empty), raising=False)
    yield Path(empty)


@pytest.fixture(autouse=True)
def _isolated_conf_dir_suitewide(tmp_path_factory, monkeypatch):
    """Keep the suite away from the developer's real filter-chain configs.

    ``sonar_to_pipewire._CONF_DIR`` points at
    ``~/.config/pipewire/filter-chain.conf.d`` — the *live* audio configuration
    of whoever runs the suite. Any test reaching a code path that reads it
    silently inherits that machine's state (which made four
    ``ensure_physical_output_links`` tests fail the moment it learned to read
    the Output channel's configured target), and any path that *writes* it
    would rewrite a developer's working audio setup mid-run.

    Tests that need a config directory monkeypatch ``_CONF_DIR`` themselves;
    this only moves the default away from the real one.
    """
    try:
        from arctis_sound_manager import sonar_to_pipewire
    except Exception:  # pragma: no cover - module import is not this fixture's job
        return
    empty = tmp_path_factory.mktemp("empty_conf_d")
    monkeypatch.setattr(sonar_to_pipewire, "_CONF_DIR", Path(empty), raising=False)
    yield Path(empty)


@pytest.fixture(autouse=True)
def _isolated_routing_overrides_suitewide(tmp_path_factory, monkeypatch):
    """Keep the suite away from the developer's real routing overrides.

    ``pw_utils.OVERRIDES_FILE`` points at
    ``~/.config/arctis_manager/routing_overrides.json`` — the app→sink pins of
    whoever runs the suite. Any code path that consults them silently inherits
    that machine's state: a redirect_audio test broke on a dev box purely
    because the real file happened to pin Discord to the Chat channel, so the
    stream the test expected to be migrated was (correctly) left alone.

    Tests that need pins monkeypatch the path themselves; this only moves the
    default away from the real one.
    """
    try:
        from arctis_sound_manager import pw_utils
    except Exception:  # pragma: no cover - module import is not this fixture's job
        return
    empty = tmp_path_factory.mktemp("empty_overrides")
    monkeypatch.setattr(
        pw_utils, "OVERRIDES_FILE", Path(empty) / "routing_overrides.json",
        raising=False,
    )
    yield
