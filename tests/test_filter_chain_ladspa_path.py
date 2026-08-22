# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Issue #203: the filter-chain unit must name the LADSPA directory.

ASM stages LADSPA plugins the host may not have into ~/.ladspa (issue #100)
and writes their absolute path into the generated conf. A systemd user unit
inherits none of the shell's environment, so PipeWire only searches its
built-in directories — and on PipeWire 1.6.4 the reporter's log shows the
absolute path being resolved as a *name* against those directories:

    failed to load plugin '/home/deck/.ladspa/plate_1423.so'
        in '/usr/lib64/ladspa:/usr/lib/ladspa:/usr/lib'

Verified here on PipeWire 1.6.8, where an absolute path outside those
directories does load — so this is version-dependent, and naming the directory
is what makes both behaviours work.

The failure is silent: the filter-chain module carries `nofail`, so HeSuVi
never appears while the channels still point node.target at it. Audio plays
for half a second and stops.

Two files ship this unit — the packaged one and setup.py's fallback — and they
must not drift (the PKG-1 lesson).
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGED = ROOT / "scripts" / "filter-chain.service"


def _env_lines(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines()
            if ln.strip().startswith("Environment=LADSPA_PATH=")]


def _builtin_unit() -> str:
    from arctis_sound_manager.scripts import setup
    return setup._FILTER_CHAIN_SERVICE


def test_the_packaged_unit_names_the_user_plugin_dir():
    lines = _env_lines(PACKAGED.read_text())
    assert lines, "no LADSPA_PATH in the packaged unit"
    assert "%h/.ladspa" in lines[0], (
        "the staged-plugin directory is the whole point: %h/.ladspa is where "
        "ASM copies plugins the host lacks"
    )


def test_the_builtin_fallback_matches_the_packaged_unit():
    """setup.py writes this one when /usr/share has no copy. The two drifting
    apart is exactly the PKG-1 defect, in a different file."""
    assert _env_lines(_builtin_unit()) == _env_lines(PACKAGED.read_text())


def test_the_system_directories_are_still_searched():
    """Naming ~/.ladspa must not drop the system ones: a native install has
    its plugins there and nowhere else."""
    line = _env_lines(PACKAGED.read_text())[0]
    for d in ("/usr/lib64/ladspa", "/usr/lib/ladspa"):
        assert d in line


def test_the_unit_version_was_bumped_so_existing_installs_migrate():
    """The copy in $HOME wins over the packaged one and is only refreshed when
    the version marker moves (PKG-3). Without a bump this fix reaches nobody
    who already has ASM installed."""
    from arctis_sound_manager.scripts import setup
    assert setup._UNIT_VERSION >= 2
