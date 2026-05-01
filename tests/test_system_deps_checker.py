"""Tests for system_deps_checker — Phase 2 of ASM_PLAN_DEPS_CHECK.

Strategy: every check function is a thin wrapper around a system call
(`shutil.which`, `subprocess.run`, file existence). We patch those at
the lowest level so the tests run on any CI runner without needing real
LADSPA plugins, real PipeWire, or root.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from arctis_sound_manager import system_deps_checker as sdc
from arctis_sound_manager.system_deps_checker import (
    CheckResult,
    DepCheck,
    Severity,
    _build_checks,
    detect_distro,
    failing,
    install_command_for,
    run_all_checks,
)


# ── Distro detection ──────────────────────────────────────────────────────────


_OS_RELEASE_FEDORA = """\
NAME=Fedora Linux
VERSION="43 (Workstation Edition)"
ID=fedora
ID_LIKE=
PRETTY_NAME="Fedora Linux 43"
"""

_OS_RELEASE_NOBARA = """\
NAME="Nobara Linux"
ID=nobara
ID_LIKE=fedora
"""

_OS_RELEASE_UBUNTU = """\
NAME="Ubuntu"
ID=ubuntu
ID_LIKE=debian
"""

_OS_RELEASE_CACHYOS = """\
NAME="CachyOS Linux"
ID=cachyos
ID_LIKE="cachyos arch"
"""

_OS_RELEASE_NEW_DERIVATIVE = """\
NAME="Some Brand-New Spin"
ID=randomspin
ID_LIKE=fedora
"""

_OS_RELEASE_TOTALLY_UNKNOWN = """\
NAME="Hand-rolled Linux"
ID=experimental
ID_LIKE="exotic"
"""


@pytest.mark.parametrize("os_release_text,expected", [
    (_OS_RELEASE_FEDORA, "fedora"),
    (_OS_RELEASE_NOBARA, "nobara"),
    (_OS_RELEASE_UBUNTU, "ubuntu"),
    (_OS_RELEASE_CACHYOS, "cachyos"),
    # ID unknown but ID_LIKE recognised → fall back to the like value
    (_OS_RELEASE_NEW_DERIVATIVE, "fedora"),
    (_OS_RELEASE_TOTALLY_UNKNOWN, "unknown"),
])
def test_detect_distro_known_and_fallbacks(tmp_path, os_release_text, expected):
    fake = tmp_path / "os-release"
    fake.write_text(os_release_text)
    with patch.object(sdc, "_read_os_release", lambda: sdc._read_os_release.__wrapped__()
                      if False else _parse(fake)):
        # The patch target above is intentionally weird — easier to
        # patch the underlying file read directly.
        pass

    # Cleaner: monkey-patch Path("/etc/os-release").read_text via the helper.
    with patch.object(sdc.Path, "exists", lambda self: True), \
         patch.object(sdc.Path, "read_text", lambda self, *a, **kw: os_release_text):
        assert detect_distro() == expected


def _parse(path):
    out = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def test_detect_distro_no_os_release():
    """No /etc/os-release at all → graceful 'unknown'."""
    with patch.object(sdc.Path, "exists", lambda self: False):
        assert detect_distro() == "unknown"


# ── install_command_for ───────────────────────────────────────────────────────


def _make_check(install_commands):
    return DepCheck(
        name="test", severity=Severity.BLOCKING, feature="test",
        detect=lambda: True, install_commands=install_commands,
    )


def test_install_command_uses_exact_distro_match():
    check = _make_check({
        "fedora": ["dnf", "install", "-y", "foo"],
        "debian": ["apt-get", "install", "-y", "foo"],
    })
    with patch.object(sdc, "detect_distro", lambda: "fedora"):
        assert install_command_for(check) == ["dnf", "install", "-y", "foo"]


def test_install_command_uses_pkgmgr_sibling_when_distro_only_in_id_like():
    """Nobara isn't in install_commands but shares dnf with fedora — pick that."""
    check = _make_check({
        "fedora": ["dnf", "install", "-y", "foo"],
    })
    with patch.object(sdc, "detect_distro", lambda: "nobara"):
        assert install_command_for(check) == ["dnf", "install", "-y", "foo"]


def test_install_command_returns_none_for_unknown_distro_without_internal():
    check = _make_check({"fedora": ["dnf", "install", "-y", "foo"]})
    with patch.object(sdc, "detect_distro", lambda: "unknown"):
        assert install_command_for(check) is None


def test_install_command_falls_back_to_internal_when_distro_unknown():
    check = _make_check({
        "fedora": ["dnf", "install", "-y", "foo"],
        "_internal": ["asm-setup"],
    })
    with patch.object(sdc, "detect_distro", lambda: "unknown"):
        assert install_command_for(check) == ["asm-setup"]


def test_install_command_returns_none_when_check_has_no_commands():
    check = _make_check({})
    with patch.object(sdc, "detect_distro", lambda: "fedora"):
        assert install_command_for(check) is None


# ── Detection helpers ────────────────────────────────────────────────────────


def test_find_ladspa_plugin_finds_match(tmp_path):
    fake_dir = tmp_path / "ladspa"
    fake_dir.mkdir()
    (fake_dir / "plate_1423.so").write_bytes(b"\x7fELF")
    with patch.object(sdc, "_LADSPA_DIRS", (str(fake_dir),)):
        assert sdc._find_ladspa_plugin("plate_1423.so") is not None


def test_find_ladspa_plugin_supports_glob(tmp_path):
    fake_dir = tmp_path / "ladspa"
    fake_dir.mkdir()
    (fake_dir / "librnnoise_ladspa.so").write_bytes(b"\x7fELF")
    with patch.object(sdc, "_LADSPA_DIRS", (str(fake_dir),)):
        assert sdc._find_ladspa_plugin("librnnoise*.so") is not None


def test_find_ladspa_plugin_returns_none_when_missing(tmp_path):
    with patch.object(sdc, "_LADSPA_DIRS", (str(tmp_path),)):
        assert sdc._find_ladspa_plugin("plate_1423.so") is None


def test_find_ladspa_plugin_skips_missing_dirs():
    with patch.object(sdc, "_LADSPA_DIRS", ("/nonexistent/path",)):
        assert sdc._find_ladspa_plugin("plate_1423.so") is None


def test_can_import_returns_true_for_stdlib():
    assert sdc._can_import("os") is True


def test_can_import_returns_false_for_missing():
    assert sdc._can_import("definitely_not_a_real_module_42") is False


def test_pipewire_version_ok_parses_real_output():
    fake_run = subprocess.CompletedProcess(
        args=["pw-cli", "--version"], returncode=0,
        stdout=("pw-cli\n"
                "Compiled with libpipewire 1.2.7\n"
                "Linked with libpipewire 1.2.7\n"),
        stderr="",
    )
    with patch.object(subprocess, "run", lambda *a, **kw: fake_run):
        assert sdc._pipewire_version_ok(min_major=1, min_minor=0) is True
        assert sdc._pipewire_version_ok(min_major=1, min_minor=2) is True
        assert sdc._pipewire_version_ok(min_major=2, min_minor=0) is False


def test_pipewire_version_ok_rejects_old_pipewire():
    fake_run = subprocess.CompletedProcess(
        args=["pw-cli", "--version"], returncode=0,
        stdout="Compiled with libpipewire 0.3.65\n", stderr="",
    )
    with patch.object(subprocess, "run", lambda *a, **kw: fake_run):
        assert sdc._pipewire_version_ok(min_major=1, min_minor=0) is False


def test_pipewire_version_ok_returns_false_when_binary_missing():
    def boom(*a, **kw):
        raise FileNotFoundError("pw-cli")
    with patch.object(subprocess, "run", boom):
        assert sdc._pipewire_version_ok() is False


def test_pipewire_running_yields_true_when_pactl_succeeds():
    fake_run = subprocess.CompletedProcess(args=["pactl", "info"], returncode=0,
                                            stdout="Server", stderr="")
    with patch.object(subprocess, "run", lambda *a, **kw: fake_run):
        assert sdc._pipewire_running() is True


def test_pipewire_running_yields_false_when_pactl_missing():
    def boom(*a, **kw):
        raise FileNotFoundError("pactl")
    with patch.object(subprocess, "run", boom):
        assert sdc._pipewire_running() is False


def test_dbus_session_via_env(monkeypatch):
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/1000/bus")
    assert sdc._dbus_session_available() is True


def test_dbus_session_via_socket(monkeypatch, tmp_path):
    monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)
    fake_bus = tmp_path / "bus"
    fake_bus.touch()
    with patch.object(sdc, "Path", lambda *a, **kw: fake_bus):
        # Path() is heavily used elsewhere — restore right after the call
        assert sdc._dbus_session_available() is True


def test_hrir_present_returns_false_when_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert sdc._hrir_present() is False


def test_hrir_present_returns_true_when_file_nonempty(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    target = tmp_path / ".local" / "share" / "pipewire" / "hrir_hesuvi" / "hrir.wav"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"RIFF" + b"\x00" * 100)
    assert sdc._hrir_present() is True


def test_hrir_present_returns_false_for_empty_file(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    target = tmp_path / ".local" / "share" / "pipewire" / "hrir_hesuvi" / "hrir.wav"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"")
    assert sdc._hrir_present() is False


# ── run_all_checks + failing ──────────────────────────────────────────────────


def test_run_all_checks_returns_one_result_per_check():
    results = run_all_checks()
    assert len(results) == len(_build_checks())
    for r in results:
        assert isinstance(r, CheckResult)
        assert isinstance(r.check, DepCheck)


def test_run_all_checks_treats_exception_as_failure():
    """If a check raises (e.g. transient subprocess timeout), don't crash —
    record it as failed so the GUI surfaces the issue rather than hiding it."""
    boom = DepCheck(
        name="exploding-check", severity=Severity.BLOCKING, feature="test",
        detect=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        install_commands={},
    )
    with patch.object(sdc, "_build_checks", lambda: [boom]):
        results = run_all_checks()
    assert len(results) == 1
    assert results[0].ok is False


def test_failing_filters_by_severity():
    checks = [
        DepCheck("a", Severity.BLOCKING, "fa", lambda: False),
        DepCheck("b", Severity.DEGRADED, "fb", lambda: False),
        DepCheck("c", Severity.OPTIONAL, "fc", lambda: False),
        DepCheck("d", Severity.BLOCKING, "fd", lambda: True),  # passing
    ]
    results = [CheckResult(check=c, ok=c.detect()) for c in checks]
    # Default: BLOCKING + DEGRADED, drops OPTIONAL and passing
    out = failing(results)
    names = sorted(r.check.name for r in out)
    assert names == ["a", "b"]
    # Tighten to BLOCKING only
    out = failing(results, min_severity=Severity.BLOCKING)
    assert [r.check.name for r in out] == ["a"]
    # Loosen to OPTIONAL — picks everything failing
    out = failing(results, min_severity=Severity.OPTIONAL)
    assert sorted(r.check.name for r in out) == ["a", "b", "c"]


def test_every_check_in_registry_has_a_feature_string():
    """Lint: any new check must explain what breaks if missing."""
    for check in _build_checks():
        assert check.feature, f"{check.name} has no feature description"


def test_every_check_either_has_install_commands_or_user_action():
    """A check that fails must give the user *some* recourse — either a
    package install command or a user_action explanation. Otherwise the
    Phase 4 dialog would render an empty row."""
    for check in _build_checks():
        assert check.install_commands or check.user_action, (
            f"{check.name} offers neither install_commands nor user_action — "
            "the user has no way to recover"
        )
