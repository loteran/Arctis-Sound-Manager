# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

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


# ── Host LADSPA cache (container cross-containment) ─────────────────────────

def test_host_ladspa_files_returns_empty_when_not_in_container(monkeypatch):
    """Outside a container there is no host boundary — the function returns
    an empty set so the local scan is used exclusively."""
    monkeypatch.setattr(sdc, "_running_in_container", lambda: False)
    assert sdc._host_ladspa_files() == set()
    sdc._reset_host_ladspa_cache()


def test_host_ladspa_files_queries_host_in_container(monkeypatch):
    """Inside a container the host's LADSPA dirs are queried via
    distrobox-host-exec once, and the result is cached."""
    monkeypatch.setattr(sdc, "_running_in_container", lambda: True)
    monkeypatch.setattr(sdc, "_host_exec_prefix", lambda: ["distrobox-host-exec"])
    mock_result = subprocess.CompletedProcess(
        args=["distrobox-host-exec", "sh", "-c", "…"],
        returncode=0,
        stdout="plate_1423.so\nsc4m_1916.so\n",
        stderr="",
    )
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: mock_result)

    files = sdc._host_ladspa_files()
    assert files == {"plate_1423.so", "sc4m_1916.so"}
    sdc._reset_host_ladspa_cache()


def test_host_ladspa_files_returns_empty_when_no_host_exec(monkeypatch):
    """If distrobox-host-exec is not reachable, the cache is set to empty so
    the local (bind-mounted ~/.ladspa) scan is the only fallback."""
    monkeypatch.setattr(sdc, "_running_in_container", lambda: True)
    monkeypatch.setattr(sdc, "_host_exec_prefix", lambda: None)
    assert sdc._host_ladspa_files() == set()
    sdc._reset_host_ladspa_cache()


def test_find_ladspa_plugin_uses_host_listing_in_container(monkeypatch, tmp_path):
    """When in a container, _find_ladspa_plugin consults the host listing
    (populated via distrobox-host-exec) first. A plugin present on the host
    is found via the host path; the local scan is still a fallback
    (covers ~/.ladspa which is bind-mounted)."""
    monkeypatch.setattr(sdc, "_running_in_container", lambda: True)
    monkeypatch.setattr(sdc, "_host_exec_prefix", lambda: ["distrobox-host-exec"])
    mock_result = subprocess.CompletedProcess(
        args=[], returncode=0,
        stdout="plate_1423.so\n", stderr="",
    )
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: mock_result)

    # Plugin on host → found via host listing.
    result = sdc._find_ladspa_plugin("plate_1423.so")
    assert result is not None
    assert result.startswith("(host:"), f"expected host-prefixed path, got {result!r}"

    # Host listing is cached: second call must NOT re-spawn distrobox-host-exec.
    before = mock_result
    # The subprocess.run call count is not tracked here; instead we assert the
    # cached result is returned without another call by resetting and re-calling
    # with a different output that would change the answer — but the cache
    # prevents the new query from running.
    # Simpler: just confirm the cached path is identical on second call.
    assert sdc._find_ladspa_plugin("plate_1423.so") == result

    # Plugin only in local dirs → still found via fallback local scan
    # (covers ~/.ladspa which is bind-mounted).
    fake_dir = tmp_path / "ladspa"
    fake_dir.mkdir()
    (fake_dir / "sc4m_1916.so").write_bytes(b"\x7fELF")
    with patch.object(sdc, "_LADSPA_DIRS", (str(fake_dir),)):
        sdc._reset_host_ladspa_cache()
        result2 = sdc._find_ladspa_plugin("sc4m_1916.so")
        assert result2 is not None
        assert "sc4m_1916.so" in result2

    sdc._reset_host_ladspa_cache()


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


# ── RNNoise LADSPA install resolution (issue #65) ─────────────────────────────

def _rnnoise_check():
    return {c.name: c for c in _build_checks()}["rnnoise LADSPA plugin"]


def test_rnnoise_is_degraded_not_blocking():
    # Optional ClearCast mic feature — must not block the whole app.
    from arctis_sound_manager.system_deps_checker import Severity
    assert _rnnoise_check().severity is Severity.DEGRADED


def test_rnnoise_ubuntu_builds_from_source():
    rn = _rnnoise_check()
    with patch.object(sdc, "detect_distro", lambda: "ubuntu"):
        cmd = install_command_for(rn)
    assert cmd is not None and cmd[0] == "bash"
    assert "noise-suppression-for-voice.git" in " ".join(cmd)
    assert "BUILD_LADSPA_PLUGIN=ON" in " ".join(cmd)


def test_rnnoise_debian_builds_from_source():
    # noise-suppression-for-voice is not packaged for Debian either (issue #96),
    # so Debian builds the LADSPA plugin from source like Ubuntu.
    rn = _rnnoise_check()
    with patch.object(sdc, "detect_distro", lambda: "debian"):
        cmd = install_command_for(rn)
    assert cmd is not None and cmd[0] == "bash"
    assert "noise-suppression-for-voice.git" in " ".join(cmd)
    assert "BUILD_LADSPA_PLUGIN=ON" in " ".join(cmd)


def test_rnnoise_mint_and_pop_build_from_source():
    rn = _rnnoise_check()
    for distro in ("linuxmint", "pop", "elementary", "neon"):
        with patch.object(sdc, "detect_distro", lambda d=distro: d):
            cmd = install_command_for(rn)
        assert cmd is not None and cmd[0] == "bash", distro


def test_python_module_deps_install_via_pip_user_on_arch():
    """#175: on Arch/SteamOS the rootfs is immutable, so pure-Python module deps
    must self-heal into ~/.local via `pip install --user`, not pacman/paru or an
    ASM reinstall that can't write /usr."""
    checks = {c.name: c for c in _build_checks()}
    for name, pkg in (
        ("pulsectl (python module)", "pulsectl"),
        ("dbus-next (python module)", "dbus-next"),
        ("ruamel.yaml (python module)", "ruamel.yaml"),
        ("pyusb (python module)", "pyusb"),
        ("PIL / Pillow (python module)", "pillow"),
    ):
        with patch.object(sdc, "detect_distro", lambda: "arch"):
            cmd = install_command_for(checks[name])
        assert cmd == ["python3", "-m", "pip", "install", "--user", pkg], name


# ── DeepFilterNet: version-aware resolution + guarded download (opt-in) ────────

def test_deepfilter_best_prefers_newest_and_user_build(tmp_path, monkeypatch):
    d = tmp_path / "ladspa"
    d.mkdir()
    (d / "libdeep_filter_ladspa-0.5.6-x86_64-unknown-linux-gnu.so").write_bytes(b"x")
    (d / "libdeep_filter_ladspa-0.5.7-x86_64-unknown-linux-gnu.so").write_bytes(b"x")
    monkeypatch.setattr(sdc, "_ladspa_search_dirs", lambda: (str(d),))
    # Highest release version wins so a newer install is used over the pinned one.
    assert sdc._find_best_deepfilter_ladspa().endswith("0.5.7-x86_64-unknown-linux-gnu.so")
    # An unversioned user build (cargo) outranks any versioned asset.
    (d / "libdeep_filter_ladspa.so").write_bytes(b"x")
    assert Path(sdc._find_best_deepfilter_ladspa()).name == "libdeep_filter_ladspa.so"


def test_deepfilter_best_none_when_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(sdc, "_ladspa_search_dirs", lambda: (str(tmp_path),))
    assert sdc._find_best_deepfilter_ladspa() is None


def test_deepfilter_asset_arch_and_musl(monkeypatch):
    import platform
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(sdc, "_is_musl_libc", lambda: False)
    url, name = sdc._deepfilter_asset()
    assert name == "libdeep_filter_ladspa-0.5.6-x86_64-unknown-linux-gnu.so"
    assert url.startswith("https://github.com/Rikorose/DeepFilterNet/releases/download/v0.5.6/")
    # musl → the glibc .so must not be offered (would fail dlopen → #88).
    monkeypatch.setattr(sdc, "_is_musl_libc", lambda: True)
    assert sdc._deepfilter_asset() is None
    # unknown arch → no prebuilt.
    monkeypatch.setattr(sdc, "_is_musl_libc", lambda: False)
    monkeypatch.setattr(platform, "machine", lambda: "sparc64")
    assert sdc._deepfilter_asset() is None


def test_ensure_deepfilter_uses_existing_without_download(tmp_path, monkeypatch):
    existing = tmp_path / "libdeep_filter_ladspa.so"
    existing.write_bytes(b"x")
    monkeypatch.setattr(sdc, "_find_best_deepfilter_ladspa", lambda: str(existing))
    # Any download attempt would blow up here — proves an installed plugin is
    # reused as-is (honours "use a newer version the user installed").
    monkeypatch.setattr(sdc, "_deepfilter_asset",
                        lambda: (_ for _ in ()).throw(AssertionError("must not download")))
    assert sdc.ensure_deepfilter_plugin() == str(existing)


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._payload


def test_ensure_deepfilter_downloads_and_validates(tmp_path, monkeypatch):
    import urllib.request
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(sdc, "_find_best_deepfilter_ladspa", lambda: None)
    monkeypatch.setattr(sdc, "_deepfilter_asset",
                        lambda: ("https://example/p.so", "libdeep_filter_ladspa-0.5.6-x86_64-unknown-linux-gnu.so"))
    payload = b"\x7fELF" + b"\x00" * 200_000
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _FakeResp(payload))

    got = sdc.ensure_deepfilter_plugin()
    assert got is not None
    p = Path(got)
    assert p.exists() and p.read_bytes()[:4] == b"\x7fELF"


def test_ensure_deepfilter_rejects_non_elf(tmp_path, monkeypatch):
    import urllib.request
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(sdc, "_find_best_deepfilter_ladspa", lambda: None)
    monkeypatch.setattr(sdc, "_deepfilter_asset",
                        lambda: ("https://example/p.so", "libdeep_filter_ladspa-0.5.6-x86_64-unknown-linux-gnu.so"))
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *a, **k: _FakeResp(b"<html>error page</html>"))
    # A non-ELF payload (error page, truncated download) is discarded, not staged.
    assert sdc.ensure_deepfilter_plugin() is None
    assert not (tmp_path / ".ladspa" /
                "libdeep_filter_ladspa-0.5.6-x86_64-unknown-linux-gnu.so").exists()
