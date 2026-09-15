# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Exit must leave the audio server exactly as it found it.

The tray's Exit used to bounce pipewire, wireplumber and pipewire-pulse on
the way out. Every client's PipeWire fd was cut at once — plasmashell crashes
in QSocketNotifier on that, taking the panel and the launcher with it — a
Bluetooth headset dropped and re-paired, and the quantum and codec the user
had settled on were reset. The daemon and the filter-chain are already
stopped by then, so the bounce removed nothing that was still there.
"""
from __future__ import annotations

import inspect
import os
import textwrap

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")


def _source(cls, name):
    return textwrap.dedent(inspect.getsource(getattr(cls, name)))


def test_tray_exit_does_not_restart_pipewire():
    from arctis_sound_manager.gui.systray_app import QSystrayApp
    src = _source(QSystrayApp, "sig_stop")
    assert "restart_detached" not in src
    assert '"pipewire"' not in src.replace("# ", "")  # not even in a call


def test_app_exit_releases_the_clip_capture_first():
    """The portal session and the encoder are closed before the interpreter
    tears objects down — left to finalisation the GUI SEGV'd in _gi and the
    compositor's end of the screencast was cut mid-frame."""
    from arctis_sound_manager.gui.main_app import QMainApp
    src = _source(QMainApp, "sig_stop")
    assert "shutdown" in src
    assert src.index("shutdown") < src.index("self.app.quit()")
