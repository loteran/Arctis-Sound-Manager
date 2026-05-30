# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for loopback_manager — command-line construction and process registry.

All subprocess.Popen calls are mocked; no real PipeWire process is started.
"""
from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, call, patch

import pytest

from arctis_sound_manager.loopback_manager import (
    LoopbackManager,
    LoopbackSpec,
    _build_pw_loopback_argv,
    make_specs,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def media_spec() -> LoopbackSpec:
    """The validated Media channel spec (matches the command in the plan)."""
    return LoopbackSpec(
        channel="media",
        capture_name="Arctis_Media",
        playback_name="Arctis_Media_sink_out",
        target="effect_input.sonar-media-eq",
        description="Arctis Nova Pro Wireless Media",
    )


@pytest.fixture
def game_spec() -> LoopbackSpec:
    return LoopbackSpec(
        channel="game",
        capture_name="Arctis_Game",
        playback_name="Arctis_Game_sink_out",
        target="effect_input.sonar-game-eq",
        description="Arctis Nova Pro Wireless Game",
    )


@pytest.fixture
def chat_spec() -> LoopbackSpec:
    return LoopbackSpec(
        channel="chat",
        capture_name="Arctis_Chat",
        playback_name="Arctis_Chat_sink_out",
        target="effect_input.sonar-chat-eq",
        description="Arctis Nova Pro Wireless Chat",
    )


@pytest.fixture
def all_sonar_specs(game_spec, chat_spec, media_spec) -> list[LoopbackSpec]:
    return [game_spec, chat_spec, media_spec]


def _mock_proc(returncode: int | None = None) -> MagicMock:
    """Build a mock subprocess.Popen that simulates a running process."""
    proc = MagicMock(spec=subprocess.Popen)
    proc.pid = 12345
    proc.returncode = returncode
    # poll() returns None when process is alive, an int when it has exited
    proc.poll.return_value = returncode
    proc.wait.return_value = returncode
    return proc


# ── _build_pw_loopback_argv ───────────────────────────────────────────────────

class TestBuildArgv:
    def test_starts_with_pw_loopback(self, media_spec: LoopbackSpec) -> None:
        argv = _build_pw_loopback_argv(media_spec)
        assert argv[0] == "pw-loopback"

    def test_has_three_elements(self, media_spec: LoopbackSpec) -> None:
        argv = _build_pw_loopback_argv(media_spec)
        assert len(argv) == 3

    def test_capture_props_flag(self, media_spec: LoopbackSpec) -> None:
        argv = _build_pw_loopback_argv(media_spec)
        assert argv[1].startswith("--capture-props=")

    def test_playback_props_flag(self, media_spec: LoopbackSpec) -> None:
        argv = _build_pw_loopback_argv(media_spec)
        assert argv[2].startswith("--playback-props=")

    # ── capture-props content ──────────────────────────────────────────────

    def test_capture_node_name(self, media_spec: LoopbackSpec) -> None:
        argv = _build_pw_loopback_argv(media_spec)
        assert "node.name=Arctis_Media" in argv[1]

    def test_capture_media_class(self, media_spec: LoopbackSpec) -> None:
        argv = _build_pw_loopback_argv(media_spec)
        assert "media.class=Audio/Sink" in argv[1]

    def test_capture_channels_2(self, media_spec: LoopbackSpec) -> None:
        argv = _build_pw_loopback_argv(media_spec)
        assert "audio.channels=2" in argv[1]

    def test_capture_position_fl_fr(self, media_spec: LoopbackSpec) -> None:
        argv = _build_pw_loopback_argv(media_spec)
        assert "audio.position=[FL FR]" in argv[1]

    def test_capture_no_8ch(self, media_spec: LoopbackSpec) -> None:
        """The capture side must never declare 8ch — PipeWire negotiates it."""
        argv = _build_pw_loopback_argv(media_spec)
        assert "audio.channels=8" not in argv[1]

    # ── playback-props content ─────────────────────────────────────────────

    def test_playback_node_name(self, media_spec: LoopbackSpec) -> None:
        argv = _build_pw_loopback_argv(media_spec)
        assert "node.name=Arctis_Media_sink_out" in argv[2]

    def test_playback_node_target(self, media_spec: LoopbackSpec) -> None:
        argv = _build_pw_loopback_argv(media_spec)
        assert "node.target=effect_input.sonar-media-eq" in argv[2]

    def test_playback_dont_remix_false(self, media_spec: LoopbackSpec) -> None:
        """stream.dont-remix=false is required to allow 2ch → 8ch expansion."""
        argv = _build_pw_loopback_argv(media_spec)
        assert "stream.dont-remix=false" in argv[2]

    def test_playback_dont_fallback_true(self, media_spec: LoopbackSpec) -> None:
        argv = _build_pw_loopback_argv(media_spec)
        assert "node.dont-fallback=true" in argv[2]

    def test_playback_linger_true(self, media_spec: LoopbackSpec) -> None:
        argv = _build_pw_loopback_argv(media_spec)
        assert "node.linger=true" in argv[2]

    def test_playback_latency_msec(self, media_spec: LoopbackSpec) -> None:
        argv = _build_pw_loopback_argv(media_spec)
        assert "latency.msec=50" in argv[2]

    def test_playback_description(self, media_spec: LoopbackSpec) -> None:
        argv = _build_pw_loopback_argv(media_spec)
        # Description is quoted because it contains spaces.
        assert f'node.description="{media_spec.description}"' in argv[2]

    def test_capture_has_quoted_description(self, media_spec: LoopbackSpec) -> None:
        # The capture side is the sink apps see (Discord/browser pickers, mixers),
        # so the user-facing description must be there, quoted (spaces).
        argv = _build_pw_loopback_argv(media_spec)
        assert f'node.description="{media_spec.description}"' in argv[1]

    # ── Per-channel correctness ────────────────────────────────────────────

    def test_game_channel_names(self, game_spec: LoopbackSpec) -> None:
        argv = _build_pw_loopback_argv(game_spec)
        assert "node.name=Arctis_Game" in argv[1]
        assert "node.name=Arctis_Game_sink_out" in argv[2]
        assert "node.target=effect_input.sonar-game-eq" in argv[2]

    def test_chat_channel_names(self, chat_spec: LoopbackSpec) -> None:
        argv = _build_pw_loopback_argv(chat_spec)
        assert "node.name=Arctis_Chat" in argv[1]
        assert "node.name=Arctis_Chat_sink_out" in argv[2]
        assert "node.target=effect_input.sonar-chat-eq" in argv[2]

    def test_custom_target_in_playback(self) -> None:
        """Verify that the target field is faithfully forwarded."""
        spec = LoopbackSpec(
            channel="game",
            capture_name="Arctis_Game",
            playback_name="Arctis_Game_sink_out",
            target="alsa_output.usb-SteelSeries_Arctis.HiFi__hw_Arctis__sink",
            description="Arctis Game",
        )
        argv = _build_pw_loopback_argv(spec)
        assert (
            "node.target=alsa_output.usb-SteelSeries_Arctis.HiFi__hw_Arctis__sink"
            in argv[2]
        )


# ── LoopbackManager.start / stop / is_running ─────────────────────────────────

class TestStartStop:
    def test_start_launches_process(self, media_spec: LoopbackSpec) -> None:
        mgr = LoopbackManager()
        mock_proc = _mock_proc()
        with patch("subprocess.Popen", return_value=mock_proc) as mock_popen:
            mgr.start(media_spec)
            mock_popen.assert_called_once()
            argv = mock_popen.call_args[0][0]
            assert argv[0] == "pw-loopback"

    def test_start_stores_handle(self, media_spec: LoopbackSpec) -> None:
        mgr = LoopbackManager()
        mock_proc = _mock_proc()
        with patch("subprocess.Popen", return_value=mock_proc):
            mgr.start(media_spec)
        assert mgr.is_running("media")

    def test_is_running_false_for_unknown_channel(self) -> None:
        mgr = LoopbackManager()
        assert mgr.is_running("nonexistent") is False

    def test_is_running_false_after_process_exits(self, media_spec: LoopbackSpec) -> None:
        mgr = LoopbackManager()
        mock_proc = _mock_proc(returncode=0)   # process already exited
        with patch("subprocess.Popen", return_value=mock_proc):
            mgr.start(media_spec)
        assert mgr.is_running("media") is False

    def test_stop_calls_terminate(self, media_spec: LoopbackSpec) -> None:
        mgr = LoopbackManager()
        mock_proc = _mock_proc()
        with patch("subprocess.Popen", return_value=mock_proc):
            mgr.start(media_spec)
        mgr.stop("media")
        mock_proc.terminate.assert_called_once()

    def test_stop_removes_from_registry(self, media_spec: LoopbackSpec) -> None:
        mgr = LoopbackManager()
        mock_proc = _mock_proc()
        with patch("subprocess.Popen", return_value=mock_proc):
            mgr.start(media_spec)
        mgr.stop("media")
        assert mgr.is_running("media") is False

    def test_stop_noop_for_unknown_channel(self) -> None:
        mgr = LoopbackManager()
        mgr.stop("nonexistent")  # must not raise

    def test_stop_noop_when_already_exited(self, media_spec: LoopbackSpec) -> None:
        """Stopping a process that has already exited should not call terminate."""
        mgr = LoopbackManager()
        mock_proc = _mock_proc(returncode=1)   # already dead
        with patch("subprocess.Popen", return_value=mock_proc):
            mgr.start(media_spec)
        mgr.stop("media")
        mock_proc.terminate.assert_not_called()

    def test_stop_kills_if_terminate_times_out(self, media_spec: LoopbackSpec) -> None:
        mgr = LoopbackManager()
        mock_proc = _mock_proc()
        mock_proc.wait.side_effect = subprocess.TimeoutExpired(cmd="pw-loopback", timeout=2.0)
        with patch("subprocess.Popen", return_value=mock_proc):
            mgr.start(media_spec)
        mgr.stop("media")
        mock_proc.terminate.assert_called_once()
        mock_proc.kill.assert_called_once()


# ── LoopbackManager.start: replaces existing process ──────────────────────────

class TestStartReplacesExisting:
    def test_second_start_stops_first_process(self, media_spec: LoopbackSpec) -> None:
        """A second start() on the same channel must stop the previous process."""
        mgr = LoopbackManager()
        old_proc = _mock_proc()
        new_proc = _mock_proc()
        new_proc.pid = 99999

        with patch("subprocess.Popen", side_effect=[old_proc, new_proc]):
            mgr.start(media_spec)
            mgr.start(media_spec)   # second call: should stop old_proc first

        old_proc.terminate.assert_called_once()

    def test_second_start_registers_new_handle(self, media_spec: LoopbackSpec) -> None:
        mgr = LoopbackManager()
        old_proc = _mock_proc()
        new_proc = _mock_proc()
        new_proc.pid = 99999

        with patch("subprocess.Popen", side_effect=[old_proc, new_proc]):
            mgr.start(media_spec)
            mgr.start(media_spec)

        # The registered handle must be the new proc
        with mgr._lock:
            assert mgr._handles["media"] is new_proc


# ── LoopbackManager.recreate ─────────────────────────────────────────────────

class TestRecreate:
    def test_recreate_stops_then_starts(self, media_spec: LoopbackSpec) -> None:
        mgr = LoopbackManager()
        old_proc = _mock_proc()
        new_proc = _mock_proc()
        new_proc.pid = 55555

        with patch("subprocess.Popen", side_effect=[old_proc, new_proc]) as mock_popen:
            mgr.start(media_spec)
            mgr.recreate(media_spec)

        old_proc.terminate.assert_called_once()
        assert mock_popen.call_count == 2

    def test_recreate_on_unregistered_channel_just_starts(
        self, media_spec: LoopbackSpec
    ) -> None:
        """recreate() on a channel that was never started should simply start it."""
        mgr = LoopbackManager()
        proc = _mock_proc()
        with patch("subprocess.Popen", return_value=proc) as mock_popen:
            mgr.recreate(media_spec)
        mock_popen.assert_called_once()
        assert mgr.is_running("media")


# ── LoopbackManager.stop_all / recreate_all ──────────────────────────────────

class TestStopAll:
    def test_stop_all_terminates_all_processes(
        self, all_sonar_specs: list[LoopbackSpec]
    ) -> None:
        mgr = LoopbackManager()
        procs = [_mock_proc() for _ in all_sonar_specs]
        with patch("subprocess.Popen", side_effect=procs):
            for spec in all_sonar_specs:
                mgr.start(spec)
        mgr.stop_all()
        for proc in procs:
            proc.terminate.assert_called_once()

    def test_stop_all_clears_registry(
        self, all_sonar_specs: list[LoopbackSpec]
    ) -> None:
        mgr = LoopbackManager()
        procs = [_mock_proc() for _ in all_sonar_specs]
        with patch("subprocess.Popen", side_effect=procs):
            for spec in all_sonar_specs:
                mgr.start(spec)
        mgr.stop_all()
        for spec in all_sonar_specs:
            assert mgr.is_running(spec.channel) is False


class TestRecreateAll:
    def test_recreate_all_stops_then_starts_each(
        self, all_sonar_specs: list[LoopbackSpec]
    ) -> None:
        mgr = LoopbackManager()
        initial_procs = [_mock_proc() for _ in all_sonar_specs]
        new_procs = [_mock_proc() for _ in all_sonar_specs]
        for p, i in zip(new_procs, range(len(new_procs))):
            p.pid = 80000 + i

        with patch(
            "subprocess.Popen",
            side_effect=initial_procs + new_procs,
        ) as mock_popen:
            for spec in all_sonar_specs:
                mgr.start(spec)
            mgr.recreate_all(all_sonar_specs)

        # Each initial process should have been terminated
        for proc in initial_procs:
            proc.terminate.assert_called_once()
        # Popen called 6 times total: 3 initial + 3 after recreate
        assert mock_popen.call_count == 6

    def test_recreate_all_with_empty_list_stops_all(
        self, all_sonar_specs: list[LoopbackSpec]
    ) -> None:
        mgr = LoopbackManager()
        procs = [_mock_proc() for _ in all_sonar_specs]
        with patch("subprocess.Popen", side_effect=procs):
            for spec in all_sonar_specs:
                mgr.start(spec)
        mgr.recreate_all([])
        for spec in all_sonar_specs:
            assert mgr.is_running(spec.channel) is False


# ── make_specs helper ─────────────────────────────────────────────────────────

class TestMakeSpecs:
    PHYS_GAME = "alsa_output.usb-SteelSeries_game"
    PHYS_CHAT = "alsa_output.usb-SteelSeries_chat"

    def test_sonar_mode_game_target(self) -> None:
        specs = make_specs(sonar=True, physical_game=self.PHYS_GAME, physical_chat=self.PHYS_CHAT)
        game = next(s for s in specs if s.channel == "game")
        assert game.target == "effect_input.sonar-game-eq"

    def test_sonar_mode_chat_target(self) -> None:
        specs = make_specs(sonar=True, physical_game=self.PHYS_GAME, physical_chat=self.PHYS_CHAT)
        chat = next(s for s in specs if s.channel == "chat")
        assert chat.target == "effect_input.sonar-chat-eq"

    def test_sonar_mode_media_target(self) -> None:
        specs = make_specs(sonar=True, physical_game=self.PHYS_GAME, physical_chat=self.PHYS_CHAT)
        media = next(s for s in specs if s.channel == "media")
        assert media.target == "effect_input.sonar-media-eq"

    def test_simple_mode_game_target_is_physical(self) -> None:
        specs = make_specs(sonar=False, physical_game=self.PHYS_GAME, physical_chat=self.PHYS_CHAT)
        game = next(s for s in specs if s.channel == "game")
        assert game.target == self.PHYS_GAME

    def test_simple_mode_chat_target_is_physical_chat(self) -> None:
        specs = make_specs(sonar=False, physical_game=self.PHYS_GAME, physical_chat=self.PHYS_CHAT)
        chat = next(s for s in specs if s.channel == "chat")
        assert chat.target == self.PHYS_CHAT

    def test_simple_mode_media_target_is_physical_game(self) -> None:
        """Media uses the game (HiFi) output, same as game channel."""
        specs = make_specs(sonar=False, physical_game=self.PHYS_GAME, physical_chat=self.PHYS_CHAT)
        media = next(s for s in specs if s.channel == "media")
        assert media.target == self.PHYS_GAME

    def test_returns_three_specs(self) -> None:
        specs = make_specs(sonar=True, physical_game=self.PHYS_GAME, physical_chat=self.PHYS_CHAT)
        assert len(specs) == 3

    def test_channel_names(self) -> None:
        specs = make_specs(sonar=True, physical_game=self.PHYS_GAME, physical_chat=self.PHYS_CHAT)
        channels = {s.channel for s in specs}
        assert channels == {"game", "chat", "media"}

    def test_capture_names(self) -> None:
        specs = make_specs(sonar=True, physical_game=self.PHYS_GAME, physical_chat=self.PHYS_CHAT)
        names = {s.capture_name for s in specs}
        assert names == {"Arctis_Game", "Arctis_Chat", "Arctis_Media"}

    def test_playback_names(self) -> None:
        specs = make_specs(sonar=True, physical_game=self.PHYS_GAME, physical_chat=self.PHYS_CHAT)
        names = {s.playback_name for s in specs}
        assert names == {"Arctis_Game_sink_out", "Arctis_Chat_sink_out", "Arctis_Media_sink_out"}

    def test_description_includes_device_name(self) -> None:
        specs = make_specs(
            sonar=True,
            physical_game=self.PHYS_GAME,
            physical_chat=self.PHYS_CHAT,
            device_name="Nova Pro WL",
        )
        for spec in specs:
            assert "Nova Pro WL" in spec.description

    def test_default_device_name(self) -> None:
        specs = make_specs(sonar=True, physical_game=self.PHYS_GAME, physical_chat=self.PHYS_CHAT)
        for spec in specs:
            assert "Arctis" in spec.description


# ── LoopbackManager.restart_dead ─────────────────────────────────────────────


class TestRestartDead:
    """Tests for restart_dead() — watchdog recovery of crashed loopbacks.

    Key invariants:
    - A loopback whose process has exited (poll() returns an int) is relaunched
      and included in the return value.
    - A loopback whose process is still running (poll() returns None) is NOT
      relaunched.
    - A channel stopped intentionally via stop() has its spec removed; it is
      NOT relaunched by restart_dead().
    - An empty manager returns an empty list.
    """

    def test_returns_empty_when_nothing_registered(self) -> None:
        mgr = LoopbackManager()
        assert mgr.restart_dead() == []

    def test_dead_loopback_is_restarted_and_listed(
        self, media_spec: LoopbackSpec
    ) -> None:
        """A process whose poll() returns a non-None exit code must be relaunched."""
        mgr = LoopbackManager()
        dead_proc = _mock_proc(returncode=1)
        new_proc = _mock_proc()
        new_proc.pid = 77777

        with patch("subprocess.Popen", side_effect=[dead_proc, new_proc]) as mock_popen:
            mgr.start(media_spec)          # Popen call 1 — dead_proc
            restarted = mgr.restart_dead() # Popen call 2 — new_proc

        assert "media" in restarted
        assert len(restarted) == 1
        assert mock_popen.call_count == 2

    def test_alive_loopback_is_not_restarted(
        self, media_spec: LoopbackSpec
    ) -> None:
        """A process still running (poll()=None) must not be touched."""
        mgr = LoopbackManager()
        live_proc = _mock_proc(returncode=None)  # poll() returns None → alive

        with patch("subprocess.Popen", return_value=live_proc) as mock_popen:
            mgr.start(media_spec)
            restarted = mgr.restart_dead()

        assert restarted == []
        # Popen was called exactly once (for the initial start only)
        assert mock_popen.call_count == 1

    def test_intentionally_stopped_channel_not_restarted(
        self, media_spec: LoopbackSpec
    ) -> None:
        """A channel stopped via stop() must not be revived by restart_dead()."""
        mgr = LoopbackManager()
        proc = _mock_proc()

        with patch("subprocess.Popen", return_value=proc):
            mgr.start(media_spec)

        mgr.stop("media")  # intentional stop — removes spec from _specs

        with patch("subprocess.Popen") as mock_popen:
            restarted = mgr.restart_dead()

        assert restarted == []
        mock_popen.assert_not_called()

    def test_mixed_channels_only_dead_ones_restarted(
        self,
        game_spec: LoopbackSpec,
        chat_spec: LoopbackSpec,
        media_spec: LoopbackSpec,
    ) -> None:
        """With multiple channels, only the dead ones are restarted."""
        mgr = LoopbackManager()
        live_proc = _mock_proc(returncode=None)   # game — alive
        dead_proc = _mock_proc(returncode=2)      # chat — dead
        live_proc2 = _mock_proc(returncode=None)  # media — alive
        revived_proc = _mock_proc()
        revived_proc.pid = 88888

        with patch(
            "subprocess.Popen",
            side_effect=[live_proc, dead_proc, live_proc2, revived_proc],
        ):
            mgr.start(game_spec)
            mgr.start(chat_spec)
            mgr.start(media_spec)
            restarted = mgr.restart_dead()

        assert restarted == ["chat"]

    def test_stop_all_prevents_watchdog_from_reviving(
        self, all_sonar_specs: list[LoopbackSpec]
    ) -> None:
        """stop_all() must clear _specs so restart_dead() is a no-op afterwards."""
        mgr = LoopbackManager()
        procs = [_mock_proc() for _ in all_sonar_specs]

        with patch("subprocess.Popen", side_effect=procs):
            for spec in all_sonar_specs:
                mgr.start(spec)

        mgr.stop_all()  # clears both _handles and _specs

        with patch("subprocess.Popen") as mock_popen:
            restarted = mgr.restart_dead()

        assert restarted == []
        mock_popen.assert_not_called()

    def test_restart_dead_updates_handle(
        self, media_spec: LoopbackSpec
    ) -> None:
        """After restart_dead(), is_running() must return True for the channel."""
        mgr = LoopbackManager()
        dead_proc = _mock_proc(returncode=0)
        new_proc = _mock_proc(returncode=None)  # alive after restart
        new_proc.pid = 55566

        with patch("subprocess.Popen", side_effect=[dead_proc, new_proc]):
            mgr.start(media_spec)
            mgr.restart_dead()

        assert mgr.is_running("media") is True
