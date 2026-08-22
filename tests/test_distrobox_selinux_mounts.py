# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""ENV-4 — Distrobox --volume mounts and SELinux relabeling.

RAPPORT-CHAOS-ASM.md's ENV-4 flagged that the --volume mounts for
/run/asm-hidraw, /dev/bus/usb and the PipeWire sockets carry `rslave` (a
mount propagation flag) but no `:z`/`:Z` SELinux relabel option, and reasoned
that this could let an SELinux-enforcing host (Silverblue and other
Fedora-derived images) silently deny the container access while install and
the health check both report success.

Investigation (see the fix commit) found that `distrobox create` itself
unconditionally passes `--security-opt label=disable --security-opt
apparmor=unconfined` to podman/docker for every container it creates — true
of both the current Go rewrite and the legacy shell implementation, back to
at least v1.7.2.1 (pkg/containermanager/providers/podman.go and docker.go in
distrobox's own source). A Distrobox container is therefore never
SELinux-confined to begin with: :z/:Z on these mounts would be a no-op at
best, and :Z ("private") on /dev/bus/usb or the PipeWire sockets — resources
the host and other processes also use — would risk relabeling shared host
state, which is the actual hazard the mount-options text below has to avoid
reintroducing.

The fix therefore does NOT add :z/:Z. It (a) documents why, next to every
--volume site that could tempt a future "obviously it's missing :z" patch,
and (b) adds a verify_mount_access / asm_verify_mount_access diagnostic that
actually probes readability from inside the container and, on failure,
prints the getenforce / ausearch commands a Silverblue reporter would need to
run to settle whether SELinux is the cause.

These tests pin both parts down at the text level so a future edit can't
silently drop the explanation or the diagnostic from one generator while
touching the others — exactly the kind of drift that bit the tray unit name
(see test_distrobox_unit_names.py).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_DISTROBOX_DIR = Path(__file__).resolve().parents[1] / "scripts" / "distrobox"

_INSTALL_SCRIPTS = ["bazzite.sh", "silverblue.sh", "steamos.sh"]
_ALL_GENERATORS = ["_common.sh", "bazzite.sh", "silverblue.sh", "steamos.sh"]


def _text(name: str) -> str:
    return (_DISTROBOX_DIR / name).read_text()


def test_every_generator_was_found():
    """Guard against a typo'd list silently checking zero files."""
    names = {p.name for p in _DISTROBOX_DIR.glob("*.sh")}
    assert set(_ALL_GENERATORS) <= names


@pytest.mark.parametrize("name", _ALL_GENERATORS)
def test_no_generator_adds_a_bare_z_or_cap_z_relabel_flag(name):
    """None of the three mounts may carry :z or :Z.

    A bare `:z`/`:Z` (or `,z`/`,Z` in a --mount=... form) on the hidraw dir,
    /dev/bus/usb or a PipeWire socket is exactly the "fix" ENV-4's own text
    warns is not safe to apply blindly — :Z in particular would recursively
    relabel real host device nodes / a live socket the host itself uses.
    """
    text = _text(name)
    # Match ":z"/":Z" (or ",z"/",Z") immediately following one of the mount
    # markers on the same logical volume spec, allowing for the "rslave"
    # propagation flag already present (e.g. "...:rslave" — that's fine and
    # must stay; only a trailing/adjacent bare z/Z relabel suffix is banned).
    forbidden = re.compile(r"(asm-hidraw|/dev/bus/usb|pipewire-0)[^\"'\n]*[:,][zZ]\b")
    match = forbidden.search(text)
    assert match is None, (
        f"{name} appears to add a :z/:Z relabel suffix near {match.group(1) if match else '?'} "
        "— see the comment above create_container()'s --volume lines for why "
        "that is deliberately not done here."
    )


@pytest.mark.parametrize("name", _ALL_GENERATORS)
def test_rslave_propagation_flag_is_preserved(name):
    """The rslave propagation flag (unrelated to SELinux) must stay intact
    on the hidraw bind mount — losing it silently reintroduces the udev
    hot-plug race the mount exists to fix.
    """
    text = _text(name)
    var = "ASM_HIDRAW_RUN_DIR" if name == "_common.sh" else "_HIDRAW_RUN_DIR"
    assert f"{var}:${var}:rslave" in text


@pytest.mark.parametrize("name", _ALL_GENERATORS)
def test_generator_explains_why_no_selinux_relabel(name):
    """Every generator must carry the label=disable rationale, not just some.

    This is the drift this test suite exists to catch: it would be easy to
    fix the explanation (or the diagnostic below) in bazzite.sh and forget
    silverblue.sh/steamos.sh/_common.sh, exactly like the tray unit rename.
    """
    text = _text(name)
    assert "label=disable" in text
    assert "container_use_devices" in text


@pytest.mark.parametrize("name", _INSTALL_SCRIPTS)
def test_install_script_defines_and_calls_verify_mount_access(name):
    text = _text(name)
    assert "verify_mount_access() {" in text
    # Must actually be invoked from main(), not just defined and forgotten.
    assert re.search(r"^verify_mount_access\s*$", text, re.MULTILINE), (
        f"{name} defines verify_mount_access but never calls it from main()"
    )
    # Called after the health check, not before (the container must exist
    # and respond before we try to distrobox-enter it to probe the mounts).
    health_idx = text.index("verify_container_health || exit 1")
    call_idx = re.search(r"^verify_mount_access\s*$", text, re.MULTILINE).start()
    assert call_idx > health_idx


def test_common_sh_defines_asm_verify_mount_access():
    text = _text("_common.sh")
    assert "asm_verify_mount_access() {" in text


@pytest.mark.parametrize(
    "name",
    ["_common.sh"] + _INSTALL_SCRIPTS,
)
def test_mount_access_diagnostic_names_the_confirmation_commands(name):
    """The diagnostic must print exactly what a Silverblue reporter needs.

    RAPPORT-CHAOS-ASM.md names `sudo ausearch -m avc -ts recent | grep -i asm`
    and `getenforce` as the confirmation a reporter would have to supply.
    Losing either from the warning path defeats the point of the diagnostic.
    """
    text = _text(name)
    assert "getenforce" in text
    assert "ausearch -m avc -ts recent" in text
    assert "grep -i asm" in text


@pytest.mark.parametrize("name", _ALL_GENERATORS)
def test_all_three_mounts_are_covered_by_the_diagnostic(name):
    """The diagnostic must probe all three resources ENV-4 named, not a subset."""
    text = _text(name)
    # Isolate the diagnostic function body so this doesn't just match the
    # create_container volume lines further up the file.
    fn_match = re.search(
        r"(?:verify_mount_access|asm_verify_mount_access)\(\)\s*\{(.*?)\n\}",
        text,
        re.DOTALL,
    )
    assert fn_match, f"{name}: could not locate the mount-access diagnostic body"
    body = fn_match.group(1)
    hidraw_var = "ASM_HIDRAW_RUN_DIR" if name == "_common.sh" else "_HIDRAW_RUN_DIR"
    assert hidraw_var in body, f"{name}: diagnostic does not probe the hidraw dir"
    for marker in ("/dev/bus/usb", "pipewire-0"):
        assert marker in body, f"{name}: diagnostic does not probe {marker}"


@pytest.mark.parametrize("name", _ALL_GENERATORS)
def test_bash_syntax_is_valid(name):
    import subprocess

    result = subprocess.run(
        ["bash", "-n", str(_DISTROBOX_DIR / name)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
