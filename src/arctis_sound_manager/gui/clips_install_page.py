# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
clips_install_page.py — the Video tab's "not on yet" face.

The Video tab is always visible, even on a machine that only wants the mixer
and the EQ and has none of the capture runtime. When Clips is not on, the tab
shows *this* page instead of the recorder: what Clips does, the exact packages
it needs (so anyone who would rather install them through their own package
manager can), and a one-click Install that fetches only those packages through a
single ``pkexec`` prompt.

Shown for two different reasons, which is why the button is not always called
the same thing: nothing installed yet (**Install**), or everything installed and
the feature switched off (**Enable**) — someone who uninstalled Clips last week
should find the way back in, not a tab that only ever offers to install what is
already there.

Deliberately free of any ``gi`` / GStreamer import so it builds and renders on a
system where none of that exists — that is the whole point of showing it.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QTimer, Signal
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
from arctis_sound_manager.gui import clips_setup
from arctis_sound_manager.i18n import I18n

logger = logging.getLogger("ClipsInstallPage")


def _tr(key: str, fallback: str) -> str:
    try:
        value = I18n.translate("ui", key)
    except Exception:  # noqa: BLE001
        return fallback
    return fallback if not value or value == key else value


def clips_runtime_ready() -> bool:
    """Kept as the name the window imports; the answer lives in clips_setup."""
    return clips_setup.runtime_ready()



class ClipsInstallPage(QWidget):
    """Explains Clips and turns it on — installing its runtime if needed."""

    # Emitted once the feature is on and its runtime is confirmed present, so
    # the window can swap this page for the real recorder without a restart.
    clips_installed = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._build()
        # Probed when first shown, not here: the window builds this page at
        # start-up whether or not anyone opens the tab, and the probe is a
        # gst-inspect subprocess per component (slow right after login, while
        # GStreamer's registry is cold).
        self._probed = False
        self._status.setText(_tr("clips_install_checking", "Checking what is installed…"))

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._probed:
            self._probed = True
            # After this paint, so the tab appears at once with "Checking…".
            QTimer.singleShot(0, self._first_refresh)

    def _first_refresh(self) -> None:
        self._status.setText("")
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

        title = QLabel(_tr("clips_install_title", "Video Clips"))
        title.setStyleSheet("font-size: 22pt; font-weight: bold; background: transparent;")
        root.addWidget(title)

        # Two keys joined here rather than one carrying a "\n\n": the language
        # files are INI, which has no escape for a newline — a single key
        # renders the backslash-n on screen.
        intro = QLabel("\n\n".join((
            _tr("clips_install_intro",
                "Record the last seconds of play in the background and save "
                "them on a keypress — with Game, Chat and Mic on separate "
                "audio tracks, so a clip stays remixable afterwards. A "
                "library, a trim editor and drag-to-Discord sharing come with "
                "it."),
            _tr("clips_install_intro2",
                "Clips is optional: it needs a screen recorder's software "
                "(GStreamer, PyGObject and ffmpeg) that an audio-only setup "
                "has no other use for, so it is installed only when you ask "
                "for it here. Nothing below is touched until you press "
                "Install."),
        )))
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color: {_theme.c('TEXT_SECONDARY')}; font-size: 11pt; background: transparent;")
        root.addWidget(intro)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"background: {_theme.c('BORDER')}; border: none; max-height: 1px;")
        root.addWidget(sep)

        req = QLabel(_tr("clips_install_what", "What gets installed"))
        req.setStyleSheet("font-size: 13pt; font-weight: bold; background: transparent;")
        root.addWidget(req)

        # The per-component list (name + package + present/missing), filled in _refresh.
        self._list_box = QVBoxLayout()
        self._list_box.setSpacing(6)
        root.addLayout(self._list_box)

        self._manual_hint = QLabel(_tr(
            "clips_install_manual",
            "Prefer to install these yourself? Run this in a terminal, then "
            "press “I've installed it”:"))
        self._manual_hint.setWordWrap(True)
        self._manual_hint.setStyleSheet(f"color: {_theme.c('TEXT_SECONDARY')}; font-size: 10pt; background: transparent;")
        root.addWidget(self._manual_hint)

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

        self._install_btn = QPushButton(_tr("clips_install", "Install"))
        self._install_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._install_btn.clicked.connect(self._on_install)
        actions.addWidget(self._install_btn)

        self._recheck_btn = QPushButton(_tr("clips_install_recheck", "I've installed it"))
        self._recheck_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._recheck_btn.clicked.connect(self._on_recheck)
        actions.addWidget(self._recheck_btn)
        self._style_buttons()

        actions.addStretch(1)

        # Someone who turned Clips off but left the packages installed has, up
        # to now, had nowhere to go: the tab offered to Enable and nothing else,
        # and the packages it once installed stayed on the machine with no
        # mention of them anywhere. Shown only when there is something to remove.
        self._remove_btn = QPushButton(_tr("clips_remove_packages", "Remove packages"))
        self._remove_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._remove_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {_theme.c('TEXT_SECONDARY')};"
            f" border: 1px solid {_theme.c('BORDER')}; border-radius: 6px; padding: 8px 16px; font-size: 10pt; }}"
            f"QPushButton:hover {{ border-color: {_theme.c('ACCENT')}; color: {_theme.c('TEXT_PRIMARY')}; }}"
        )
        self._remove_btn.clicked.connect(self._on_remove)
        actions.addWidget(self._remove_btn)

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
        from arctis_sound_manager.system_deps_checker import install_command_for

        # One probe per refresh: the list, the install command, the Remove
        # button and the Install/Enable choice all read this snapshot. Each
        # used to probe again — four gst-inspect passes per refresh.
        results = clips_setup.probe()
        missing = [check for check, ok in results if not ok]

        self._clear_list()
        for check, present in results:
            argv = install_command_for(check)
            pkgs = (", ".join(clips_setup.packages_in(argv)) if argv
                    else _tr("clips_install_own_pm", "(install via your package manager)"))
            mark = "✓" if present else "•"
            color = "#3fb950" if present else _theme.c("TEXT_SECONDARY")
            row = QLabel(f"<span style='color:{color}'>{mark}</span>  <b>{check.name}</b>"
                         f" — <span style='color:{_theme.c('TEXT_SECONDARY')}'>{pkgs}</span>"
                         + (f"  <i>({_tr('clips_install_present', 'already present')})</i>"
                            if present else ""))
            row.setTextFormat(Qt.TextFormat.RichText)
            row.setStyleSheet("font-size: 10.5pt; background: transparent;")
            self._list_box.addWidget(row)

        cmd = clips_setup.manual_command(clips_setup.install_argvs(missing))
        self._manual_field.setText(cmd or "")
        self._manual_field.setVisible(cmd is not None)
        self._manual_hint.setVisible(cmd is not None)


        # Nothing on this machine to remove means no offer to remove it. The
        # button is about the packages, not about the feature, so it follows
        # what the probe found rather than the toggle.
        self._remove_btn.setVisible(any(ok for _, ok in results))

        # Said before the password prompt, not after it. Installing
        # python-gobject on a pipx install succeeds and changes nothing, and
        # without this the screen would send the user around that loop first.
        self._install_btn.setEnabled(True)
        if clips_setup.bindings_unreachable(missing):
            self._install_btn.setEnabled(False)
            self._recheck_btn.setVisible(False)
            # The command would run, report success, and change nothing. Leaving
            # it on screen next to the explanation invites exactly the loop the
            # explanation is there to end.
            self._manual_hint.setVisible(False)
            self._manual_field.setVisible(False)
            self._status.setText(_tr(
                "clips_install_venv",
                "Clips cannot run from this install of ASM. It is running from "
                "an isolated Python environment — a pipx or venv install — and "
                "PyGObject is a system package that such an environment cannot "
                "see, however many times it is installed. Use your "
                "distribution's package of ASM, or recreate the environment "
                "with --system-site-packages."))
            return

        # Nothing left to fetch: this is the switched-off state, not the
        # missing-runtime one. "Install" would be a lie about what the button
        # is going to do, and there is nothing to re-check by hand either.
        if not missing:
            self._install_btn.setText(_tr("clips_install_enable", "Enable"))
            self._recheck_btn.setVisible(False)
            self._status.setText(_tr("clips_install_all_present",
                                     "Everything Clips needs is already installed."))
        else:
            self._install_btn.setText(_tr("clips_install", "Install"))
            self._recheck_btn.setVisible(True)

    # ── Actions ────────────────────────────────────────────────────────────

    def _on_install(self) -> None:
        argvs = clips_setup.install_argvs()

        if argvs:
            self._install_btn.setEnabled(False)
            self._status.setText(_tr("clips_install_running",
                                     "Installing… a password prompt will appear."))
            QApplication.processEvents()
            try:
                ok, detail = clips_setup.run_batch(argvs)
            except clips_setup.NoPkexec:
                self._install_btn.setEnabled(True)
                self._status.setText(_tr(
                    "clips_install_no_pkexec",
                    "pkexec is not available — install the packages listed "
                    "above manually, then press “I've installed it”."))
                return
            finally:
                self._install_btn.setEnabled(True)

            if not ok:
                if clips_setup.looks_like_dependency_conflict(detail):
                    # Nothing on this screen can fix this one, and the package
                    # manager's own wording ("installing pipewire breaks
                    # dependency 'pipewire=…' required by pipewire-pulse")
                    # does not lead anyone to the fix. So name the fix.
                    upgrade = clips_setup.system_upgrade_command()
                    self._status.setText(
                        _tr("clips_install_partial_upgrade",
                            "Install failed: this machine's packages and its "
                            "repositories disagree, so the capture packages "
                            "cannot be resolved. Update the whole system "
                            "first, then try again:")
                        + (("\n\n" + upgrade) if upgrade else "")
                        + "\n\n" + clips_setup.last_line(detail))
                else:
                    self._status.setText(
                        _tr("clips_install_failed", "Install failed: {0}").format(
                            clips_setup.last_line(detail)
                            or _tr("clips_pkg_failed",
                                   "The package manager refused. "
                                   "Nothing was changed.")))
                self._refresh()
                return

        self._finish_if_ready()

    def _on_recheck(self) -> None:
        self._refresh()
        self._finish_if_ready(manual=True)

    def _on_remove(self) -> None:
        """Remove the capture packages from the screen that offers to install
        them — the same conversation the recorder's Uninstall has."""
        clips_setup.confirm_and_remove(self)
        self._refresh()

    def _finish_if_ready(self, manual: bool = False) -> None:
        """Re-probe; if the runtime is now present, turn the feature on and ask
        the window to swap in the recorder.

        Re-probed rather than trusting the exit code: a package manager can
        succeed and still leave the thing undetectable — the wrong package for
        the distro, or a plugin registry that has not been re-scanned.
        """
        if not clips_setup.runtime_ready():
            self._refresh()
            if manual:
                self._status.setText(_tr(
                    "clips_install_still_missing",
                    "Still missing some components — check the list above. You "
                    "may need to open a terminal and run the command shown."))
            return

        clips_setup.set_enabled(True)
        self._status.setText(_tr("clips_install_done",
                                 "✓ Installed. Opening the recorder…"))
        self.clips_installed.emit()

    def _style_buttons(self) -> None:
        self._install_btn.setStyleSheet(
            f"QPushButton {{ background: {_theme.c('ACCENT')}; color: #fff; border: none;"
            f" border-radius: 6px; padding: 8px 20px; font-size: 11pt; font-weight: bold; }}"
            f"QPushButton:hover {{ background: {_theme.c('BG_BUTTON_HOVER')}; }}"
            f"QPushButton:disabled {{ background: {_theme.c('BORDER')}; color: {_theme.c('TEXT_SECONDARY')}; }}"
        )
        self._recheck_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {_theme.c('TEXT_SECONDARY')};"
            f" border: 1px solid {_theme.c('BORDER')}; border-radius: 6px; padding: 8px 16px; font-size: 10pt; }}"
            f"QPushButton:hover {{ border-color: {_theme.c('ACCENT')}; color: {_theme.c('TEXT_PRIMARY')}; }}"
        )

    def apply_theme(self, t=None) -> None:
        """Repaint after a theme change (called by main_app). The rows and the
        manual-command field carry inline colors, so rebuild them from the
        active theme; the static labels restyle on their own."""
        self._style_buttons()
        try:
            self._refresh()
        except Exception:  # noqa: BLE001
            pass
