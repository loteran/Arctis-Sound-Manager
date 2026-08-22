# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""PKG-1 regression: a missing Pillow must degrade, not crash the daemon.

`scripts/daemon.py` imports `core.py` at module scope, which imports
`oled_manager.py` at module scope, which used to do a plain top-level
`from PIL import Image` (and pull in `oled_renderer.py`, itself `from PIL
import ...` at its own top level). On any install missing Pillow (every
.deb built before this fix — see `test_debian_deb_depends_match_control.py`)
the interpreter raised `ModuleNotFoundError` while still resolving
`daemon.py`'s own import block, before `main()` — and therefore
`verify_setup()` and `system_deps_checker`'s DEGRADED "PIL / Pillow" check —
ever got a chance to run. `CoreEngine` pulls in the OLED manager
unconditionally, even for headsets with no screen at all.

This test simulates Pillow being absent with `sys.modules["PIL"] = None`
(Python's import machinery treats a `None` entry as "this import is
disallowed" and raises `ImportError` for it and any `from PIL import X`) —
it does not uninstall anything. It re-imports the affected modules fresh so
their top-level code actually runs under the simulated absence, then
restores the previously-imported modules afterwards so the rest of the
suite is unaffected.
"""
from __future__ import annotations

import importlib
import sys
from unittest import mock

import pytest

# Modules whose top-level code touches PIL, directly or transitively, and
# therefore need to be freshly imported (not read from cache) for this test
# to actually exercise the guard rather than reuse an already-successful
# import from earlier in the suite.
_RELOAD_TARGETS = (
    "arctis_sound_manager.oled_manager",
    "arctis_sound_manager.oled_renderer",
    "arctis_sound_manager.core",
)


def _pil_module_names() -> list[str]:
    return [n for n in sys.modules if n == "PIL" or n.startswith("PIL.")]


def _set_module(name: str, module) -> None:
    """Put *module* into sys.modules under *name*, and — for a dotted name —
    also rebind the parent package's attribute to match.

    `import a.b as x` resolves `x` by walking attributes off the already-
    imported top-level package (`a.b`), not by reading `sys.modules["a.b"]`
    directly. Restoring only `sys.modules` after this fixture's blocked-PIL
    import left `sys.modules["arctis_sound_manager"].oled_manager` pointing
    at the just-reverted stale (PIL-blocked) module object, so a later plain
    `import arctis_sound_manager.oled_manager` elsewhere in the suite kept
    seeing `PIL_AVAILABLE = False` even though `sys.modules` itself had
    already been restored correctly.
    """
    sys.modules[name] = module
    if "." in name:
        parent_name, leaf = name.rsplit(".", 1)
        parent = sys.modules.get(parent_name)
        if parent is not None:
            setattr(parent, leaf, module)


@pytest.fixture
def reimport_without_pil():
    """Yields a function that freshly (re)imports modules with PIL blocked,
    then restores the original sys.modules entries for everything touched.

    Returns the imported module objects directly rather than reading them
    back out of ``sys.modules`` afterwards: ``mock.patch.dict`` restores
    ``sys.modules`` to its pre-``with`` snapshot on exit, which would
    otherwise evict the very modules this test just imported before the
    caller ever gets to inspect them.
    """
    saved = {name: sys.modules.get(name) for name in _RELOAD_TARGETS}
    saved.update({name: sys.modules[name] for name in _pil_module_names()})

    for name in list(saved):
        sys.modules.pop(name, None)

    def _do_import(*module_names: str):
        with mock.patch.dict(sys.modules, {"PIL": None}):
            modules = tuple(importlib.import_module(name) for name in module_names)
        return modules

    try:
        yield _do_import
    finally:
        for name in (*_RELOAD_TARGETS, *_pil_module_names()):
            sys.modules.pop(name, None)
        for name, mod in saved.items():
            if mod is not None:
                _set_module(name, mod)


def test_core_import_chain_survives_missing_pillow(reimport_without_pil):
    """daemon.py -> core.py -> oled_manager.py must not raise ImportError
    just because Pillow is not installed."""
    core, oled_manager = reimport_without_pil(
        "arctis_sound_manager.core", "arctis_sound_manager.oled_manager"
    )

    assert oled_manager.PIL_AVAILABLE is False
    assert oled_manager.Image is None
    # core.py's `from arctis_sound_manager.oled_manager import OledManager`
    # must have resolved to the real (guarded) class, not blown up.
    assert core.OledManager is oled_manager.OledManager


def test_oled_manager_reports_missing_pillow_instead_of_crashing(reimport_without_pil):
    """Constructing OledManager without Pillow must raise a normal,
    catchable exception — the one CoreEngine's own try/except around
    `OledManager(self)` already degrades from (OLED disabled, everything
    else keeps working) — not an ImportError escaping module import."""
    _, oled_manager = reimport_without_pil(
        "arctis_sound_manager.core", "arctis_sound_manager.oled_manager"
    )

    with pytest.raises(RuntimeError, match="Pillow"):
        oled_manager.OledManager(core=mock.Mock())


def test_pillow_present_still_imports_and_constructs_normally():
    """Sanity check: the guard must be a no-op on a normal install (Pillow
    is a real dependency here — see pyproject.toml)."""
    import arctis_sound_manager.oled_manager as oled_manager

    assert oled_manager.PIL_AVAILABLE is True
    assert oled_manager.Image is not None
