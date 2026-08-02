# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Suite-wide test safety nets."""

import os
import shlex
import subprocess
from pathlib import Path

import pytest

# Everything that can reach into the live audio graph. Read-only calls are
# included deliberately: a unit test that shells out to the running server is
# reading the developer's machine rather than its own fixture, which is how
# tests come to pass or fail for reasons that have nothing to do with the code.
#
# The service managers are here for the worst case of the lot. Restarting
# pipewire/wireplumber re-enumerates every ALSA device: each physical sink and
# source is destroyed and rebuilt under a new node id, every stream on them is
# interrupted, and the user is left with their sound in pieces — from a test
# run that reported nothing but passes.
_AUDIO_TOOLS = frozenset({
    "pactl", "pacmd", "pasuspender", "wpctl", "pw-cli", "pw-metadata",
    "pw-link", "pw-loopback", "pw-cat", "pw-play", "pw-record", "pw-dump",
    "amixer", "alsactl",
    "systemctl", "dinitctl", "service", "pipewire", "pipewire-pulse",
    "wireplumber", "pulseaudio", "pkill", "killall",
})


def _audio_tool(args, shell: bool) -> str | None:
    """The audio or service binary *args* would run, if any.

    Every token is checked, not just the first: the real calls are wrapped
    (``setsid systemctl …``, ``sh -c "sleep 2 && systemctl --user restart …"``)
    and looking only at argv[0] would wave both of those straight through.
    """
    if isinstance(args, (str, bytes, os.PathLike)):
        args = [args]
    tokens: list[str] = []
    for arg in args:
        text = os.fsdecode(arg) if isinstance(arg, (bytes, os.PathLike)) else str(arg)
        if shell or " " in text:
            try:
                tokens.extend(shlex.split(text))
            except ValueError:
                tokens.extend(text.split())
        else:
            tokens.append(text)
    for token in tokens:
        name = os.path.basename(token)
        if name in _AUDIO_TOOLS:
            return name
    return None


def pytest_configure(config):
    """Give the whole suite a throwaway HOME before any test module imports.

    Half of this package's paths are module-level constants built from
    ``Path.home()`` — ``settings.json``, ``channel_volumes.json``,
    ``eq_bands.json``, ``output_preference.json``, the filter-chain drop-ins.
    They are resolved at *import* time, so a fixture cannot move them after the
    fact; by the time one runs, the constant already points at the developer's
    real configuration. Any test that writes through one of those constants
    rewrites the live setup of whoever ran the suite, and the running daemon
    picks the change up on its own — which is how a test run ends with the
    sound genuinely broken and no failure to show for it.

    Doing it in ``pytest_configure`` is what makes it work: conftest is
    imported before test modules are collected, so every ``Path.home()``
    evaluated during collection sees the temporary directory.

    ``ASM_TEST_REAL_HOME`` keeps the original around for the rare test that
    needs to know what a real home looks like.
    """
    import tempfile

    config.addinivalue_line(
        "markers",
        "live_spawn: this test must spawn a real process to observe how it was "
        "spawned (issue #123's posix_spawn-vs-fork guards), so the live-audio "
        "Popen block is lifted for it. Only for commands that read and change "
        "nothing — 'systemctl show', '--version'. Never for anything that "
        "moves a sink, a link or a service.",
    )

    real_home = os.path.expanduser("~")
    fake_home = tempfile.mkdtemp(prefix="asm-test-home-")
    os.environ["ASM_TEST_REAL_HOME"] = real_home
    os.environ["HOME"] = fake_home
    for name, sub in (("XDG_CONFIG_HOME", ".config"),
                      ("XDG_DATA_HOME", ".local/share"),
                      ("XDG_STATE_HOME", ".local/state"),
                      ("XDG_CACHE_HOME", ".cache")):
        path = os.path.join(fake_home, *sub.split("/"))
        os.makedirs(path, exist_ok=True)
        os.environ[name] = path


@pytest.fixture(autouse=True)
def _no_live_audio_commands_suitewide(monkeypatch, request):
    """Refuse to run pactl/pw-* against the developer's own sound server.

    The suite runs on machines with a *live* ASM daemon, and its subjects are
    the very functions that move default sinks, destroy nodes, relink ports and
    respawn loopbacks. A test that reaches one of those for real does not fail
    — it succeeds, against the developer's audio, and leaves them with their
    sound broken and nothing in the output saying why.

    Blocking at ``Popen`` covers ``run``/``check_output``/``call`` in one
    place, since all of them go through it. Tests that mean to exercise a
    command patch ``subprocess.run`` (or the module's own reference) and never
    reach this; anything that trips it is a test talking to the real system,
    which is the bug being reported here rather than an inconvenience.
    """
    if request.node.get_closest_marker("live_spawn") is not None:
        # The spawn-path guards cannot be satisfied by a mock: what they assert
        # is which syscall CPython used to start the process, which only exists
        # if a process is really started. They are held to read-only commands
        # and skip themselves when the binary is absent.
        yield
        return

    real_popen = subprocess.Popen

    class _GuardedPopen(real_popen):
        def __init__(self, args, *rest, **kwargs):
            tool = _audio_tool(args, bool(kwargs.get("shell")))
            if tool is not None:
                raise AssertionError(
                    f"the test suite tried to run {tool!r} against the live "
                    f"audio server: {args!r}. Mock the call — running it for "
                    f"real rewrites the routing of whoever runs the tests.")
            super().__init__(args, *rest, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", _GuardedPopen)
    yield


@pytest.fixture(autouse=True)
def _no_live_pulse_connection_suitewide(monkeypatch):
    """Same rule for pulsectl: no test opens a socket to the real server.

    ``pulsectl.Pulse`` is how volumes are set and streams are moved, and a
    connection made in a test is a connection to the machine running it.
    """
    try:
        import pulsectl
    except ImportError:
        return

    def _refuse(*_args, **_kwargs):
        raise AssertionError(
            "the test suite tried to open a real pulsectl connection. Patch "
            "pulsectl.Pulse — a live connection reads and writes the "
            "developer's own audio server.")

    monkeypatch.setattr(pulsectl, "Pulse", _refuse)
    yield


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
