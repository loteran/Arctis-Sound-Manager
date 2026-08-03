# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
clips_install_page.py — the Video tab's "not installed yet" screen.

The Video tab is always visible, even on a machine that only wants the mixer
and the EQ and has none of the capture runtime. When that runtime is absent,
the tab shows *this* page instead of the recorder: what Clips does, the exact
packages it needs (so anyone who would rather install them through their own
package manager can), and a one-click Install that fetches only those packages
through a single ``pkexec`` prompt.

Deliberately free of any ``gi`` / GStreamer import so it builds and renders on a
system where none of that exists — that is the whole point of showing it.
"""
from __future__ import annotations

import logging
import shutil
import subprocess

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

import arctis_sound_manager.gui.theme as _theme

logger = logging.getLogger("ClipsInstallPage")


def clips_runtime_ready() -> bool:
    """True when every blocking package Clips needs is present on this machine.

    This is what decides whether the Video tab shows the recorder or this
    install screen — it is about the runtime actually being there, not about
    the Settings opt-in flag.
    """
    try:
        from arctis_sound_manager.system_deps_checker import Severity, clip_dep_checks
        return all(
            c.detect() for c in clip_dep_checks() if c.severity is Severity.BLOCKING
        )
    except Exception as exc:  # noqa: BLE001 — never let a probe failure hide the tab
        logger.debug("clips_runtime_ready probe failed, assuming not ready: %s", exc)
        return False


def _missing_checks() -> list:
    from arctis_sound_manager.system_deps_checker import clip_dep_checks
    return [c for c in clip_dep_checks() if not c.detect()]


def _packages_of(argv: list[str]) -> list[str]:
    """The package names in a package-manager argv (drop the manager, the
    subcommand and any -flags): apt-get install -y A B -> [A, B]."""
    return [tok for tok in argv[2:] if not tok.startswith("-")]


def _manual_command() -> str | None:
    """A single, copy-pasteable command that installs every missing package on
    this distro, or None when the distro is unknown (no argv to offer)."""
    from arctis_sound_manager.system_deps_checker import install_command_for
    argvs = [a for a in (install_command_for(c) for c in _missing_checks()) if a]
    if not argvs:
        return None
    base = argvs[0][:2]  # e.g. ["apt-get", "install"] / ["pacman", "-S"]
    pkgs: list[str] = []
    for argv in argvs:
        for p in _packages_of(argv):
            if p not in pkgs:
                pkgs.append(p)
    if not pkgs:
        return None
    return "sudo " + " ".join(base + pkgs)


class ClipsInstallPage(QWidget):
    """Explains Clips and installs its runtime on demand."""

    # Emitted after the runtime is confirmed present, so the window can swap
    # this page for the real recorder without a restart.
    clips_installed = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._build()
        self._refresh()

    # ── UI ─────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        outer.addWidget(scroll)

        body = QWidget()
        scroll.setWidget(body)
        root = QVBoxLayout(body)
        root.setContentsMargins(40, 32, 40, 32)
        root.setSpacing(16)
        root.setAlignment(Qt.AlignmentFlag.AlignTop)

        title = QLabel("Video Clips")
        title.setStyleSheet("font-size: 22pt; font-weight: bold; background: transparent;")
        root.addWidget(title)

        intro = QLabel(
            "Record the last seconds of play in the background and save them on a "
            "keypress — with Game, Chat and Mic on separate audio tracks, so a clip "
            "stays remixable afterwards. A library, a trim editor and drag-to-Discord "
            "sharing come with it.\n\n"
            "Clips is optional: it needs a screen recorder's software (GStreamer, "
            "PyGObject and ffmpeg) that an audio-only setup has no other use for, so it "
            "is installed only when you ask for it here. Nothing below is touched until "
            "you press Install."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color: {_theme.c('TEXT_SECONDARY')}; font-size: 11pt; background: transparent;")
        root.addWidget(intro)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"background: {_theme.c('BORDER')}; border: none; max-height: 1px;")
        root.addWidget(sep)

        req = QLabel("What gets installed")
        req.setStyleSheet("font-size: 13pt; font-weight: bold; background: transparent;")
        root.addWidget(req)

        # The per-component list (name + package + present/missing), filled in _refresh.
        self._list_box = QVBoxLayout()
        self._list_box.setSpacing(6)
        root.addLayout(self._list_box)

        manual_hint = QLabel(
            "Prefer to install these yourself? Run this in a terminal, then press "
            "“I've installed it”:"
        )
        manual_hint.setWordWrap(True)
        manual_hint.setStyleSheet(f"color: {_theme.c('TEXT_SECONDARY')}; font-size: 10pt; background: transparent;")
        root.addWidget(manual_hint)

        self._manual_field = QLineEdit()
        self._manual_field.setReadOnly(True)
        self._manual_field.setStyleSheet(
            f"QLineEdit {{ background: {_theme.c('BG_BUTTON')}; border: 1px solid {_theme.c('BORDER')};"
            f" border-radius: 6px; color: {_theme.c('TEXT_PRIMARY')}; padding: 6px 10px;"
            f" font-family: monospace; font-size: 10pt; }}"
        )
        root.addWidget(self._manual_field)

        # ── Action row ────────────────────────────────────────────────────
        actions = QHBoxLayout()
        actions.setSpacing(10)

        self._install_btn = QPushButton("Install")
        self._install_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._install_btn.setStyleSheet(
            f"QPushButton {{ background: {_theme.c('ACCENT')}; color: #fff; border: none;"
            f" border-radius: 6px; padding: 8px 20px; font-size: 11pt; font-weight: bold; }}"
            f"QPushButton:hover {{ background: {_theme.c('BG_BUTTON_HOVER')}; }}"
            f"QPushButton:disabled {{ background: {_theme.c('BORDER')}; color: {_theme.c('TEXT_SECONDARY')}; }}"
        )
        self._install_btn.clicked.connect(self._on_install)
        actions.addWidget(self._install_btn)

        self._recheck_btn = QPushButton("I've installed it")
        self._recheck_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._recheck_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {_theme.c('TEXT_SECONDARY')};"
            f" border: 1px solid {_theme.c('BORDER')}; border-radius: 6px; padding: 8px 16px; font-size: 10pt; }}"
            f"QPushButton:hover {{ border-color: {_theme.c('ACCENT')}; color: {_theme.c('TEXT_PRIMARY')}; }}"
        )
        self._recheck_btn.clicked.connect(self._on_recheck)
        actions.addWidget(self._recheck_btn)

        actions.addStretch(1)
        root.addLayout(actions)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setStyleSheet(f"color: {_theme.c('TEXT_SECONDARY')}; font-size: 10pt; background: transparent;")
        root.addWidget(self._status)

        root.addStretch(1)

    def _clear_list(self) -> None:
        while self._list_box.count():
            item = self._list_box.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _refresh(self) -> None:
        """Rebuild the component list and the manual command from a live probe."""
        from arctis_sound_manager.system_deps_checker import (
            clip_dep_checks, install_command_for)

        self._clear_list()
        for check in clip_dep_checks():
            present = False
            try:
                present = bool(check.detect())
            except Exception:  # noqa: BLE001
                present = False
            argv = install_command_for(check)
            pkgs = ", ".join(_packages_of(argv)) if argv else "(install via your package manager)"
            mark = "✓" if present else "•"
            color = "#3fb950" if present else _theme.c("TEXT_SECONDARY")
            row = QLabel(f"<span style='color:{color}'>{mark}</span>  <b>{check.name}</b>"
                         f" — <span style='color:{_theme.c('TEXT_SECONDARY')}'>{pkgs}</span>"
                         + ("  <i>(already present)</i>" if present else ""))
            row.setTextFormat(Qt.TextFormat.RichText)
            row.setStyleSheet("font-size: 10.5pt; background: transparent;")
            self._list_box.addWidget(row)

        cmd = _manual_command()
        self._manual_field.setText(cmd or "Install PyGObject, the GStreamer plugin sets and ffmpeg with your package manager.")
        self._manual_field.setVisible(cmd is not None)

        if not _missing_checks():
            self._status.setText("Everything Clips needs is already installed.")
            self._install_btn.setText("Enable")

    # ── Actions ────────────────────────────────────────────────────────────

    def _on_install(self) -> None:
        from arctis_sound_manager.system_deps_checker import install_command_for

        missing = _missing_checks()
        argvs = [a for a in (install_command_for(c) for c in missing) if a]

        if argvs:
            if not shutil.which("pkexec"):
                self._status.setText(
                    "pkexec is not available — install the packages listed above "
                    "manually, then press “I've installed it”.")
                return
            self._install_btn.setEnabled(False)
            self._status.setText("Installing… a password prompt will appear.")
            QApplication.processEvents()

            def _q(a: list[str]) -> str:
                return " ".join(f"'{t}'" if " " in t else t for t in a)

            try:
                proc = subprocess.run(
                    ["pkexec", "sh", "-c", " && ".join(_q(a) for a in argvs)],
                    capture_output=True, text=True, timeout=900)
            except (OSError, subprocess.SubprocessError) as exc:
                logger.warning("clip install failed: %s", exc)
                self._status.setText(f"Install failed: {exc}")
                self._install_btn.setEnabled(True)
                return
            finally:
                self._install_btn.setEnabled(True)

            if proc.returncode != 0:
                detail = (proc.stderr or proc.stdout or "").strip().splitlines()
                self._status.setText("Install failed: " + (detail[-1] if detail else "package manager returned an error."))
                self._refresh()
                return

        self._finish_if_ready()

    def _on_recheck(self) -> None:
        self._refresh()
        self._finish_if_ready(manual=True)

    def _finish_if_ready(self, manual: bool = False) -> None:
        """Re-probe; if the runtime is now present, turn the feature on and ask
        the window to swap in the recorder."""
        if not clips_runtime_ready():
            self._refresh()
            if manual:
                self._status.setText(
                    "Still missing some components — check the list above. "
                    "You may need to open a terminal and run the command shown.")
            return

        try:
            from arctis_sound_manager.settings import GeneralSettings
            s = GeneralSettings.read_from_file()
            s.clips_enabled = True
            s.write_to_file()
        except Exception as exc:  # noqa: BLE001
            logger.warning("could not persist clips_enabled: %s", exc)

        self._status.setText("✓ Installed. Opening the recorder…")
        self.clips_installed.emit()

    def apply_theme(self, t=None) -> None:
        """Repaint after a theme change (called by main_app). The rows and the
        manual-command field carry inline colors, so rebuild them from the
        active theme; the static labels restyle on their own."""
        try:
            self._refresh()
        except Exception:  # noqa: BLE001
            pass
