# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""What asm-gui says when PySide6 will not import.

Two unrelated failures arrive in the same ``except ImportError`` and they have
opposite fixes. Printing one paragraph for both is not a cosmetic problem: a
Nova 5 owner hit the version mismatch, was told by the message to install
qt6-wayland, did exactly that, saw no change, and concluded the app did not
work. Advice that sends someone to install an unrelated package costs more than
no advice at all, so the two cases are pinned here.

Deliberately importing only the helper: it takes an exception and returns a
string, so these run without PySide6 present — which is the situation being
described.
"""
from __future__ import annotations

from arctis_sound_manager.scripts.gui import _qt_import_help

# Verbatim from the report (Arctis Nova 5, Arch, ASM installed from the
# distro package): PySide6 is found, and fails on a Qt private symbol.
_MISMATCH = ImportError(
    "/usr/lib/python3.14/site-packages/PySide6/QtCore.cpython-314-x86_64-linux-gnu.so: "
    "undefined symbol: _ZN14QObjectPrivateC2E16QtPrivate_6_11_2, version Qt_6_PRIVATE_API"
)


def test_a_mismatch_is_not_reported_as_a_missing_plugin():
    """The bug itself. Qt is installed and loaded here — telling the user to
    install it is the one thing that cannot help."""
    out = _qt_import_help(_MISMATCH)

    assert "qt6-wayland" not in out
    assert "qt6-qtwayland" not in out
    assert "do not match" in out


def test_the_expected_qt_version_is_named():
    """`QtPrivate_6_11_2` is the only place the required version appears, and
    it is what turns "some Qt problem" into something checkable against what is
    installed."""
    assert "6.11.2" in _qt_import_help(_MISMATCH)


def test_the_mismatch_advice_is_a_full_upgrade():
    """On a rolling distro this is a partial upgrade nine times out of ten, and
    the fix is the whole system — not another single package."""
    out = _qt_import_help(_MISMATCH)

    assert "pacman -Syu" in out
    assert "pacman -S qt6" not in out


def test_a_pip_copy_is_the_second_thing_to_check():
    """The other way the two versions drift apart: a pip PySide6 in ~/.local
    shadowing the packaged one it was never built against."""
    out = _qt_import_help(_MISMATCH)

    assert "PySide6.__file__" in out
    assert ".local" in out


def test_a_genuinely_missing_qt_still_gets_the_install_advice():
    """The original message was right for the case it was written for, and has
    to stay right: nothing loaded, so installing Qt is the fix."""
    out = _qt_import_help(ImportError("No module named 'PySide6'"))

    assert "qt6-wayland" in out
    assert "do not match" not in out


def test_a_missing_shared_library_is_treated_as_missing_qt():
    """No symbol resolution happened — the loader never found the library — so
    this belongs with the install advice, not the mismatch advice."""
    out = _qt_import_help(ImportError("libQt6Core.so.6: cannot open shared object file"))

    assert "qt6-wayland" in out
    assert "do not match" not in out


def test_the_original_error_is_always_quoted():
    """Whatever the advice, the raw loader message has to survive: it is what a
    bug report is diagnosed from, and paraphrasing it loses the symbol."""
    for exc in (_MISMATCH, ImportError("No module named 'PySide6'")):
        assert str(exc) in _qt_import_help(exc)
