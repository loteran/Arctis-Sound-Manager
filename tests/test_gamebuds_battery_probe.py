# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for scripts/reverse-engineering/gamebuds_battery_probe.py (#202).

The script's whole job is to tell "found a byte that tracks a real state"
apart from "this family genuinely pushes nothing interesting" — these tests
exercise that classification directly, plus the daemon guard that keeps the
probe from fighting the live ASM daemon for the USB interface (the failure
behind discussion #203), plus argument handling.

The no-device path and the refuse-to-run-while-the-daemon-holds-it path were
also exercised for real against this machine's actual (non-GameBuds) hardware
and its actually-running daemon while writing this script — see the session
notes. They are covered here too, with fakes, so they run in CI.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import usb.core

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "reverse-engineering" / "gamebuds_battery_probe.py"

# Registered in sys.modules *before* exec: the module defines dataclasses, and
# CPython 3.10's dataclasses module looks up `cls.__module__` in sys.modules
# while building them — skipping this raises AttributeError on import.
_spec = importlib.util.spec_from_file_location("gamebuds_battery_probe", SCRIPT)
probe = importlib.util.module_from_spec(_spec)
sys.modules["gamebuds_battery_probe"] = probe
_spec.loader.exec_module(probe)


def frame(elapsed, label, *bytes_):
    return probe.CapturedFrame(elapsed, label, tuple(bytes_))


# ── frame formatting / grouping ─────────────────────────────────────────────

def test_hexs_formats_lowercase_space_separated():
    assert probe.hexs([0x0A, 0xFF, 0x00]) == "0a ff 00"


def test_frame_kind_groups_by_length_and_first_byte():
    a = frame(0.0, "x", 0xB0, 0x01, 0x02)
    b = frame(1.0, "x", 0xB0, 0x09, 0x09)
    c = frame(2.0, "x", 0xB1, 0x01, 0x02)
    d = frame(3.0, "x", 0xB0, 0x01)  # different length -> different kind
    assert a.kind == b.kind
    assert a.kind != c.kind
    assert a.kind != d.kind


def test_group_by_kind_splits_frame_shapes_apart():
    frames = [
        frame(0.0, "baseline", 0xB0, 0x64),
        frame(1.0, "baseline", 0xB0, 0x64),
        frame(2.0, "baseline", 0x05, 0x01, 0x02),
    ]
    groups = probe.group_by_kind(frames)
    assert len(groups) == 2
    assert len(groups[(2, 0xB0)]) == 2
    assert len(groups[(3, 0x05)]) == 1


def test_format_frame_carries_label_and_bytes():
    line = probe.format_frame(frame(12.3, "right_to_case", 0xB0, 0x64))
    assert "right_to_case" in line
    assert "b0 64" in line
    assert "12.3" in line


# ── byte-change detection (the core diagnostic) ─────────────────────────────

def test_constant_byte_is_not_a_candidate():
    """A header/report-id byte that never moves is not interesting."""
    frames = [
        frame(0.0, "baseline", 0xB0, 0x64),
        frame(1.0, "right_to_case", 0xB0, 0x50),
    ]
    reports = probe.analyze_bytes(frames)
    assert reports[0].is_candidate is False  # offset 0: always 0xb0
    assert reports[0].noisy is False
    assert reports[0].varies_across_labels is False


def test_byte_stable_within_action_but_different_across_actions_is_a_candidate():
    """This is the exact shape a state byte (case in/out, battery, ...) makes:
    steady while one action holds, a different steady value under another."""
    frames = [
        frame(0.0, "baseline", 0xB0, 0x64),
        frame(1.0, "baseline", 0xB0, 0x64),
        frame(2.0, "right_to_case", 0xB0, 0x50),
        frame(3.0, "right_to_case", 0xB0, 0x50),
    ]
    reports = probe.analyze_bytes(frames)
    battery_like = reports[1]
    assert battery_like.offset == 1
    assert battery_like.noisy is False
    assert battery_like.varies_across_labels is True
    assert battery_like.is_candidate is True


def test_byte_that_changes_within_a_single_action_is_noisy_not_a_candidate():
    """A counter/sequence byte moves regardless of what the user is doing —
    that is a different bug from a state byte and must not be reported as one."""
    frames = [
        frame(0.0, "baseline", 0xB0, 0x01),
        frame(1.0, "baseline", 0xB0, 0x02),
        frame(2.0, "baseline", 0xB0, 0x03),
    ]
    reports = probe.analyze_bytes(frames)
    counter_byte = reports[1]
    assert counter_byte.noisy is True
    assert counter_byte.is_candidate is False


def test_analyze_bytes_of_empty_input_is_empty():
    assert probe.analyze_bytes([]) == []


# ── verdict: "found something" vs "genuinely reports nothing" ──────────────

def test_verdict_no_frames_at_all():
    assert probe.verdict([]) == "no_frames"


def test_verdict_frames_arrive_but_nothing_ever_changes():
    frames = [
        frame(0.0, "baseline", 0xB0, 0x00),
        frame(1.0, "right_to_case", 0xB0, 0x00),
        frame(2.0, "idle_end", 0xB0, 0x00),
    ]
    assert probe.verdict(frames) == "nothing_varies"


def test_verdict_finds_a_candidate_byte():
    frames = [
        frame(0.0, "baseline", 0xB0, 0x64),
        frame(1.0, "right_to_case", 0xB0, 0x32),
    ]
    assert probe.verdict(frames) == "candidates_found"


def test_verdict_ignores_noise_only_variation():
    """Only a counter byte moving is not enough to call it 'found' — that is
    exactly the false positive this heuristic exists to avoid."""
    frames = [
        frame(0.0, "baseline", 0xB0, 0x01),
        frame(1.0, "baseline", 0xB0, 0x02),
        frame(2.0, "right_to_case", 0xB0, 0x03),
        frame(3.0, "right_to_case", 0xB0, 0x04),
    ]
    assert probe.verdict(frames) == "nothing_varies"


# ── summaries read sensibly for each verdict ────────────────────────────────

def test_summarize_passive_no_frames_points_at_active_probe():
    lines = probe.summarize_passive([])
    text = "\n".join(lines)
    assert "No frames were received" in text
    assert "--send-status-opcodes" in text


def test_summarize_passive_nothing_varies_suggests_two_charge_levels():
    frames = [frame(0.0, "baseline", 0xB0, 0x00), frame(1.0, "idle_end", 0xB0, 0x00)]
    text = "\n".join(probe.summarize_passive(frames))
    assert "did not move enough" in text or "constant" in text
    assert "full charge" in text


def test_summarize_passive_candidates_lists_the_offset_and_values():
    frames = [
        frame(0.0, "baseline", 0xB0, 0x64),
        frame(1.0, "right_to_case", 0xB0, 0x32),
    ]
    text = "\n".join(probe.summarize_passive(frames))
    assert "byte[1]" in text
    assert "baseline=0x64" in text
    assert "right_to_case=0x32" in text
    assert "not a decoded meaning" in text


def test_summarize_active_reports_each_opcode():
    frames = [frame(0.0, "opcode_0x00b0", 0xB0, 0x64)]
    tried = (0x00B0, 0x01B0)
    text = "\n".join(probe.summarize_active(frames, tried))
    assert "0x00b0: ANSWERED (1 frame(s))" in text
    assert "0x01b0: no reply" in text
    assert "Do NOT copy a response_mapping" in text


def test_summarize_active_no_replies_omits_the_carried_over_warning():
    text = "\n".join(probe.summarize_active([], (0x00B0,)))
    assert "no reply" in text
    assert "Do NOT copy" not in text


# ── request padding ──────────────────────────────────────────────────────

def test_build_status_request_single_byte_opcode_padded_to_64():
    req = probe.build_status_request(0x20)
    assert len(req) == 64
    assert req[0] == 0x20
    assert req[1:] == [0x00] * 63


def test_build_status_request_two_byte_opcode_big_endian():
    req = probe.build_status_request(0x06B0)
    assert req[:2] == [0x06, 0xB0]
    assert len(req) == 64
    assert set(req[2:]) == {0x00}


# ── argument handling ───────────────────────────────────────────────────────

def test_default_args_are_the_safe_passive_only_mode():
    args = probe.build_arg_parser().parse_args([])
    assert args.send_status_opcodes is False
    assert args.seconds is None
    assert args.assume_yes is False


def test_send_status_opcodes_is_opt_in_and_explicit():
    args = probe.build_arg_parser().parse_args(["--send-status-opcodes"])
    assert args.send_status_opcodes is True


def test_seconds_override_parses_as_int():
    args = probe.build_arg_parser().parse_args(["--seconds", "30"])
    assert args.seconds == 30


def test_assume_yes_flag():
    args = probe.build_arg_parser().parse_args(["--assume-yes"])
    assert args.assume_yes is True


def test_unknown_argument_is_rejected():
    with pytest.raises(SystemExit):
        probe.build_arg_parser().parse_args(["--nonsense"])


def test_seconds_rejects_non_integer():
    with pytest.raises(SystemExit):
        probe.build_arg_parser().parse_args(["--seconds", "soon"])


# ── daemon guard: detection ──────────────────────────────────────────────

class _Result:
    def __init__(self, stdout=""):
        self.stdout = stdout


def test_daemon_state_running_via_systemd():
    run = lambda argv: _Result("active\n")
    assert probe.daemon_state(run, use_systemd=True, use_dinit=False) == "running"


def test_daemon_state_stopped_via_systemd():
    run = lambda argv: _Result("inactive\n")
    assert probe.daemon_state(run, use_systemd=True, use_dinit=False) == "stopped"


def test_daemon_state_running_via_dinit():
    run = lambda argv: _Result("[STARTED]      arctis-manager\n")
    assert probe.daemon_state(run, use_systemd=False, use_dinit=True) == "running"


def test_daemon_state_stopped_via_dinit():
    run = lambda argv: _Result("[STOPPED]      arctis-manager\n")
    assert probe.daemon_state(run, use_systemd=False, use_dinit=True) == "stopped"


def test_daemon_state_unknown_when_neither_service_manager_present():
    """The Distrobox case: the host's service manager isn't reachable."""
    run = lambda argv: (_ for _ in ()).throw(AssertionError("must not be called"))
    assert probe.daemon_state(run, use_systemd=False, use_dinit=False) == "unknown"


def test_daemon_state_unknown_when_the_command_raises():
    def run(argv):
        raise FileNotFoundError("no systemctl")
    assert probe.daemon_state(run, use_systemd=True, use_dinit=False) == "unknown"


# ── daemon guard: the ensure_daemon_not_running decision tree ──────────────

def test_ensure_daemon_not_running_already_stopped_does_nothing():
    calls = []
    run = lambda argv: calls.append(argv) or _Result("inactive\n")
    stopped = probe.ensure_daemon_not_running(
        ask=lambda p: "y", run=run, use_systemd=True, use_dinit=False)
    assert stopped is False
    # Only the one status check — never a stop/start command.
    assert all("stop" not in a and "start" not in a for a in calls)


def test_ensure_daemon_not_running_refuses_when_user_declines():
    stop_attempted = []

    def run(argv):
        if "stop" in argv:
            stop_attempted.append(argv)
        return _Result("active\n")

    with pytest.raises(probe.DaemonGuardRefused):
        probe.ensure_daemon_not_running(
            ask=lambda p: "n", run=run, use_systemd=True, use_dinit=False)
    assert stop_attempted == []  # declining must never touch the daemon


def test_ensure_daemon_not_running_stops_and_reports_true_on_consent():
    calls = []

    def run(argv):
        calls.append(list(argv))
        if "stop" in argv:
            return _Result()
        if any("stop" in c for c in calls):
            return _Result("inactive\n")  # is-active check taken after the stop
        return _Result("active\n")  # is-active check taken before the stop

    stopped = probe.ensure_daemon_not_running(
        ask=lambda p: "y", run=run, use_systemd=True, use_dinit=False,
        sleep=lambda seconds: None)
    assert stopped is True
    assert any("stop" in c for c in calls)


def test_ensure_daemon_not_running_raises_if_stop_command_itself_fails():
    def run(argv):
        if "stop" in argv:
            raise FileNotFoundError("systemctl vanished")
        return _Result("active\n")

    with pytest.raises(probe.DaemonGuardRefused):
        probe.ensure_daemon_not_running(
            ask=lambda p: "y", run=run, use_systemd=True, use_dinit=False,
            sleep=lambda seconds: None)


def test_ensure_daemon_not_running_raises_if_still_running_after_stop():
    """The stop command ran without raising, but the daemon is still there —
    must not be treated as a success."""
    def run_still_active(argv):
        if "stop" in argv:
            return _Result()
        return _Result("active\n")  # never actually stops

    with pytest.raises(probe.DaemonGuardRefused):
        probe.ensure_daemon_not_running(
            ask=lambda p: "y", run=run_still_active, use_systemd=True, use_dinit=False,
            sleep=lambda seconds: None)


def test_ensure_daemon_not_running_unknown_state_requires_explicit_confirmation():
    with pytest.raises(probe.DaemonGuardRefused):
        probe.ensure_daemon_not_running(
            ask=lambda p: "n", run=lambda a: _Result(),
            use_systemd=False, use_dinit=False)


def test_ensure_daemon_not_running_unknown_state_proceeds_on_y():
    stopped = probe.ensure_daemon_not_running(
        ask=lambda p: "y", run=lambda a: _Result(),
        use_systemd=False, use_dinit=False)
    assert stopped is False  # nothing was actually stopped, just confirmed by hand


# ── daemon command text (what the user is told to type) ────────────────────

def test_daemon_stop_command_prefers_systemd(monkeypatch):
    monkeypatch.setattr(probe, "have_systemd", lambda: True)
    monkeypatch.setattr(probe, "have_dinit", lambda: False)
    assert probe.daemon_stop_command() == "systemctl --user stop arctis-manager"


def test_daemon_stop_command_falls_back_to_dinit(monkeypatch):
    monkeypatch.setattr(probe, "have_systemd", lambda: False)
    monkeypatch.setattr(probe, "have_dinit", lambda: True)
    assert probe.daemon_stop_command() == "dinitctl --user stop arctis-manager"


def test_daemon_start_command_mirrors_stop(monkeypatch):
    monkeypatch.setattr(probe, "have_systemd", lambda: True)
    monkeypatch.setattr(probe, "have_dinit", lambda: False)
    assert probe.daemon_start_command() == "systemctl --user start arctis-manager"


# ── USB interface handling, with fakes (no real hardware/backend touched) ──

class _FakeDevice:
    def __init__(self, kernel_active=True, detach_error=None):
        self.kernel_active = kernel_active
        self.detach_error = detach_error
        self.detached = False
        self.attached = False

    def is_kernel_driver_active(self, iface):
        return self.kernel_active

    def detach_kernel_driver(self, iface):
        if self.detach_error:
            raise self.detach_error
        self.detached = True

    def attach_kernel_driver(self, iface):
        self.attached = True


def test_take_interface_success(monkeypatch):
    monkeypatch.setattr(usb.util, "claim_interface", lambda dev, iface: None)
    dev = _FakeDevice(kernel_active=True)
    assert probe.take_interface(dev) is True
    assert dev.detached is True


def test_take_interface_reports_permission_error_on_detach(monkeypatch, capsys):
    dev = _FakeDevice(kernel_active=True,
                       detach_error=usb.core.USBError("perm", errno=13))
    assert probe.take_interface(dev) is False
    assert "permissions error" in capsys.readouterr().out


def test_take_interface_reports_busy_on_claim(monkeypatch, capsys):
    def fail_claim(dev, iface):
        raise usb.core.USBError("busy", errno=16)
    monkeypatch.setattr(usb.util, "claim_interface", fail_claim)
    dev = _FakeDevice(kernel_active=False)
    assert probe.take_interface(dev) is False
    assert "Something else is holding the buds" in capsys.readouterr().out


def test_give_interface_back_always_reattaches(monkeypatch):
    monkeypatch.setattr(usb.util, "release_interface",
                         lambda dev, iface: (_ for _ in ()).throw(usb.core.USBError("x")))
    dev = _FakeDevice()
    probe.give_interface_back(dev)  # must not raise even though release failed
    assert dev.attached is True


class _FakeEndpoint:
    def __init__(self, address, max_packet_size):
        self.bEndpointAddress = address
        self.wMaxPacketSize = max_packet_size


class _FakeInterface:
    def __init__(self, number, alt, endpoints):
        self.bInterfaceNumber = number
        self.bAlternateSetting = alt
        self._endpoints = endpoints

    def __iter__(self):
        return iter(self._endpoints)


class _FakeConfig:
    def __init__(self, interfaces):
        self._interfaces = interfaces

    def __iter__(self):
        return iter(self._interfaces)


class _FakeUsbDevice:
    def __init__(self, configs):
        self._configs = configs

    def __iter__(self):
        return iter(self._configs)


def test_find_in_endpoint_returns_the_in_endpoint_on_the_right_interface():
    in_ep = _FakeEndpoint(0x83, 64)     # 0x80 bit set -> IN
    out_ep = _FakeEndpoint(0x03, 64)    # OUT
    other_iface = _FakeInterface(0, 0, [_FakeEndpoint(0x81, 64)])
    target_iface = _FakeInterface(probe.INTERFACE, probe.ALT_SETTING, [out_ep, in_ep])
    dev = _FakeUsbDevice([_FakeConfig([other_iface, target_iface])])

    result = probe.find_in_endpoint(dev)
    assert result == (0x83, 64)


def test_find_in_endpoint_returns_none_when_interface_absent():
    dev = _FakeUsbDevice([_FakeConfig([_FakeInterface(0, 0, [])])])
    assert probe.find_in_endpoint(dev) is None


# ── real, read-only checks against whatever is actually plugged in ─────────

def test_find_device_is_read_only_and_matches_nothing_but_gamebuds():
    """No mocking: usb.core.find() only enumerates the bus. Whatever headset
    is actually connected on this machine, it is not a GameBuds PID, so this
    must come back empty rather than matching the wrong device."""
    dev, name, pid = probe.find_device()
    if dev is not None:
        assert pid in probe.PRODUCTS
    else:
        assert name is None and pid is None
