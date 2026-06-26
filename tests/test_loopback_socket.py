# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for the PipeWire socket resolution helpers introduced in issue #90.

These helpers pin the active PipeWire socket in the environment of spawned
pw-loopback child processes so they connect to the correct socket even when
the daemon's own XDG_RUNTIME_DIR is stale or container-relative (Distrobox /
Bazzite Steam Game Mode).

All tests use monkeypatch on os.environ and/or os.path.exists — no real
PipeWire process or socket file is required (except for the tmpdir-based
tests that verify the happy-path with a real file on disk).
"""
from __future__ import annotations

import os

import pytest

from arctis_sound_manager.loopback_manager import (
    _pw_loopback_env,
    _resolve_pipewire_socket,
    current_pipewire_socket_signature,
)


# ── _resolve_pipewire_socket ──────────────────────────────────────────────────


class TestResolvePipewireSocket:
    """Tests for _resolve_pipewire_socket() — socket path discovery."""

    def test_returns_xdg_candidate_when_file_exists(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Happy path: XDG_RUNTIME_DIR/pipewire-0 exists → return it."""
        fake_socket = tmp_path / "pipewire-0"
        fake_socket.touch()
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
        monkeypatch.delenv("PIPEWIRE_REMOTE", raising=False)
        result = _resolve_pipewire_socket()
        assert result == str(fake_socket)

    def test_respects_pipewire_remote_env(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """PIPEWIRE_REMOTE overrides the default 'pipewire-0' socket name."""
        custom_name = "pipewire-custom"
        fake_socket = tmp_path / custom_name
        fake_socket.touch()
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
        monkeypatch.setenv("PIPEWIRE_REMOTE", custom_name)
        result = _resolve_pipewire_socket()
        assert result == str(fake_socket)

    def test_fallback_when_xdg_candidate_missing(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When the XDG candidate does not exist, probe the host-side fallback."""
        # tmp_path has no pipewire-0 file — XDG candidate is absent.
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
        monkeypatch.delenv("PIPEWIRE_REMOTE", raising=False)
        uid = os.getuid()
        fallback = f"/run/user/{uid}/pipewire-0"
        # Mock os.path.exists so only the fallback path is considered present.
        monkeypatch.setattr("os.path.exists", lambda p: str(p) == fallback)
        result = _resolve_pipewire_socket()
        assert result == fallback

    def test_returns_none_when_no_socket_found(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Returns None when neither the XDG candidate nor the fallback exists."""
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
        monkeypatch.delenv("PIPEWIRE_REMOTE", raising=False)
        monkeypatch.setattr("os.path.exists", lambda p: False)
        result = _resolve_pipewire_socket()
        assert result is None

    def test_fallback_xdg_runtime_dir_not_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When XDG_RUNTIME_DIR is absent, default to /run/user/{uid} as base."""
        monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
        monkeypatch.delenv("PIPEWIRE_REMOTE", raising=False)
        uid = os.getuid()
        expected = f"/run/user/{uid}/pipewire-0"
        # The candidate built from the fallback runtime_dir equals the host
        # fallback, so the function returns it on the first os.path.exists hit.
        monkeypatch.setattr("os.path.exists", lambda p: str(p) == expected)
        result = _resolve_pipewire_socket()
        assert result == expected

    def test_fallback_not_probed_when_equals_candidate(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When candidate already equals the fallback path, it is not re-probed."""
        # Build an XDG_RUNTIME_DIR path that equals /run/user/{uid} so that
        # candidate == host_fallback and the second probe is skipped.
        uid = os.getuid()
        monkeypatch.setenv("XDG_RUNTIME_DIR", f"/run/user/{uid}")
        monkeypatch.delenv("PIPEWIRE_REMOTE", raising=False)
        # Make os.path.exists always return False — if the code probed twice,
        # the second probe would still return False; the function should return
        # None either way (one probe or two doesn't change the result here, but
        # we confirm the return value is sane).
        monkeypatch.setattr("os.path.exists", lambda p: False)
        assert _resolve_pipewire_socket() is None

    def test_never_raises_on_unexpected_exception(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Any unexpected OS error must be swallowed — never raises to caller."""
        def _exploding_exists(p: object) -> bool:
            raise RuntimeError("simulated OS failure")

        monkeypatch.setattr("os.path.exists", _exploding_exists)
        # Should not raise; must return None
        result = _resolve_pipewire_socket()
        assert result is None


# ── _pw_loopback_env ──────────────────────────────────────────────────────────


class TestPwLoopbackEnv:
    """Tests for _pw_loopback_env() — environment builder for pw-loopback."""

    def test_returns_none_when_no_socket(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Returns None when _resolve_pipewire_socket() finds nothing."""
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
        monkeypatch.delenv("PIPEWIRE_REMOTE", raising=False)
        monkeypatch.setattr("os.path.exists", lambda p: False)
        result = _pw_loopback_env()
        assert result is None

    def test_pins_xdg_runtime_dir(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Returned env has XDG_RUNTIME_DIR set to socket's parent directory."""
        socket = tmp_path / "pipewire-0"
        socket.touch()
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
        monkeypatch.delenv("PIPEWIRE_REMOTE", raising=False)
        env = _pw_loopback_env()
        assert env is not None
        assert env["XDG_RUNTIME_DIR"] == str(tmp_path)

    def test_pins_pipewire_remote(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Returned env has PIPEWIRE_REMOTE set to the socket's basename."""
        socket = tmp_path / "pipewire-0"
        socket.touch()
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
        monkeypatch.delenv("PIPEWIRE_REMOTE", raising=False)
        env = _pw_loopback_env()
        assert env is not None
        assert env["PIPEWIRE_REMOTE"] == "pipewire-0"

    def test_pins_custom_remote_name(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """PIPEWIRE_REMOTE basename is correctly forwarded for non-default sockets."""
        custom_name = "my-pw-socket"
        socket = tmp_path / custom_name
        socket.touch()
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
        monkeypatch.setenv("PIPEWIRE_REMOTE", custom_name)
        env = _pw_loopback_env()
        assert env is not None
        assert env["PIPEWIRE_REMOTE"] == custom_name
        assert env["XDG_RUNTIME_DIR"] == str(tmp_path)

    def test_returns_copy_not_os_environ_reference(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Returned dict is a copy; mutating it must not affect os.environ."""
        socket = tmp_path / "pipewire-0"
        socket.touch()
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
        monkeypatch.delenv("PIPEWIRE_REMOTE", raising=False)
        env = _pw_loopback_env()
        assert env is not None
        assert env is not os.environ
        env["INJECTED_KEY"] = "injected"
        assert "INJECTED_KEY" not in os.environ

    def test_preserves_other_env_vars(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Non-PipeWire env vars from the parent process are preserved in the result."""
        socket = tmp_path / "pipewire-0"
        socket.touch()
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
        monkeypatch.delenv("PIPEWIRE_REMOTE", raising=False)
        monkeypatch.setenv("MY_CUSTOM_VAR", "hello")
        env = _pw_loopback_env()
        assert env is not None
        assert env.get("MY_CUSTOM_VAR") == "hello"


# ── current_pipewire_socket_signature ────────────────────────────────────────


class TestCurrentPipewireSocketSignature:
    """Tests for current_pipewire_socket_signature() — watchdog helper."""

    def test_returns_empty_string_when_no_socket(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Returns '' (not None) when no socket can be resolved."""
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
        monkeypatch.delenv("PIPEWIRE_REMOTE", raising=False)
        monkeypatch.setattr("os.path.exists", lambda p: False)
        sig = current_pipewire_socket_signature()
        assert sig == ""

    def test_returns_socket_path_when_found(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Returns the socket path string when a socket is found."""
        socket = tmp_path / "pipewire-0"
        socket.touch()
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
        monkeypatch.delenv("PIPEWIRE_REMOTE", raising=False)
        sig = current_pipewire_socket_signature()
        assert sig == str(socket)

    def test_returns_str_type(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Return value is always str (never None) — critical for comparison logic."""
        monkeypatch.setattr("os.path.exists", lambda p: False)
        sig = current_pipewire_socket_signature()
        assert isinstance(sig, str)

    def test_signature_changes_when_socket_changes(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Signature differs between two different socket paths (change-detection invariant)."""
        socket_a = tmp_path / "pipewire-0"
        socket_a.touch()
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
        monkeypatch.delenv("PIPEWIRE_REMOTE", raising=False)
        sig_a = current_pipewire_socket_signature()

        socket_b = tmp_path / "pipewire-1"
        socket_b.touch()
        monkeypatch.setenv("PIPEWIRE_REMOTE", "pipewire-1")
        sig_b = current_pipewire_socket_signature()

        assert sig_a != sig_b
