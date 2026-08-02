# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Device / Settings page — ArctisSonar GUI visual style.
Matches the ref_settingsPage.png design.
"""
import logging
import shutil
from pathlib import Path

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from arctis_sound_manager.gui.anc_widget import QAncWidget
from arctis_sound_manager.gui.qt_widgets.q_dual_state import QDualState
from arctis_sound_manager.i18n import I18n
from arctis_sound_manager.gui.components import (
    DividerLine,
    SectionTitle,
)
from arctis_sound_manager.gui.settings_widget import QSettingsWidget
from arctis_sound_manager.autostart import active_backend_name, autostart_enabled, set_autostart
log = logging.getLogger(__name__)

import arctis_sound_manager.gui.theme as _theme
from arctis_sound_manager.gui.theme import (
    ACCENT,
    BG_BUTTON,
    BG_BUTTON_HOVER,
    BG_MAIN,
    BORDER,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    THEMES,
    all_theme_labels, get_theme, get_theme_label, is_builtin,
    reload_user_themes,
    delete_user_theme,
)

_SERVICE = "arctis-manager.service"
_GUI_SERVICE = "arctis-gui.service"


def _autostart_enabled() -> bool:
    from arctis_sound_manager import service_control as sc
    return sc.is_enabled("arctis-manager")


_GUI_SERVICE_TEMPLATE = """\
[Unit]
Description=Arctis Sound Manager — System Tray
After=graphical-session.target arctis-manager.service
Wants=arctis-manager.service

[Service]
Type=simple
ExecStart={asm_gui} --systray
Restart=on-failure
RestartSec=5

[Install]
WantedBy=graphical-session.target
"""


def _ensure_gui_service() -> Path | None:
    """Create ~/.config/systemd/user/arctis-gui.service if missing. Returns path or None."""
    gui_service_path = Path.home() / ".config" / "systemd" / "user" / _GUI_SERVICE
    if gui_service_path.exists():
        return gui_service_path
    asm_gui = shutil.which("asm-gui")
    if not asm_gui:
        return None
    gui_service_path.parent.mkdir(parents=True, exist_ok=True)
    gui_service_path.write_text(_GUI_SERVICE_TEMPLATE.format(asm_gui=asm_gui))
    from arctis_sound_manager import service_control as sc
    sc.daemon_reload()
    return gui_service_path


def _set_autostart(enabled: bool) -> None:
    from arctis_sound_manager import service_control as sc
    from arctis_sound_manager.init_system import (
        detect_init, write_xdg_autostart, remove_xdg_autostart,
    )

    if enabled:
        sc.enable("arctis-manager")
    else:
        sc.disable("arctis-manager")

    # dinit has no GUI service — autostart is handled via XDG desktop file.
    if detect_init() == "dinit":
        if enabled:
            write_xdg_autostart()
        else:
            remove_xdg_autostart()
        return

    # systemd: enable/disable the GUI tray service too.
    gui_service_path = _ensure_gui_service() if enabled else (
        Path.home() / ".config" / "systemd" / "user" / _GUI_SERVICE
    )
    if gui_service_path and gui_service_path.exists():
        if enabled:
            sc.enable("arctis-gui")
        else:
            sc.disable("arctis-gui")


def _styled_button(text: str) -> QPushButton:
    btn = QPushButton(text)
    btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    btn.setFixedHeight(44)
    btn.setStyleSheet(
        f"""
        QPushButton {{
            background-color: {_theme.c('BG_BUTTON')};
            color: {_theme.c('TEXT_PRIMARY')};
            border: none;
            border-radius: 6px;
            font-size: 11pt;
            padding: 0 16px;
        }}
        QPushButton:hover {{
            background-color: {_theme.c('BG_BUTTON_HOVER')};
        }}
        """
    )
    return btn


class DevicePage(QWidget):
    """
    Settings page with:
    - Title "Arctis Sound Manager" bold + subtitle "Device Settings"
    - Theme selector chips
    - "General Settings" section title (gray ~20pt)
    - Settings form rows (labels + controls)
    - Horizontal divider
    - "Devices" section with a card showing connected headset
    """

    sig_theme_changed = Signal(str)
    sig_theme_create = Signal()
    sig_theme_edit = Signal(str)   # theme_id
    sig_update_result = Signal(str, str, str)  # re-emits (version, url, wheel_url) from manual re-check

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setStyleSheet(f"background-color: {BG_MAIN};")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Scrollable content area ───────────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(
            f"QScrollArea {{ background-color: {_theme.c('BG_MAIN')}; border: none; }}"
        )
        self._scroll = scroll

        content = QWidget()
        content.setStyleSheet(f"background-color: {_theme.c('BG_MAIN')};")
        self._content = content
        content_layout = QVBoxLayout(content)
        content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        content_layout.setContentsMargins(36, 12, 36, 12)
        content_layout.setSpacing(0)

        # ── Top row : check for updates (left) + language selector (right) ──────
        title_row = QHBoxLayout()
        title_row.setSpacing(16)

        self._check_update_btn = _styled_button(I18n.translate("ui", "check_for_updates"))
        self._check_update_btn.setFixedWidth(220)
        self._check_update_btn.clicked.connect(self._on_check_update)
        title_row.addWidget(self._check_update_btn)

        self._update_status_lbl = QLabel("")
        self._update_status_lbl.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 10pt; background: transparent;"
        )
        self._update_status_lbl.setWordWrap(True)
        title_row.addWidget(self._update_status_lbl, stretch=1)

        self._update_url: str = ""

        lang_row = QHBoxLayout()
        lang_row.setSpacing(6)
        lang_row.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        lang_label = QLabel("Language:")
        lang_label.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 10pt; background: transparent;"
        )
        lang_row.addWidget(lang_label)

        self._lang_combo = QComboBox()
        self._lang_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self._lang_combo.setFixedHeight(30)
        self._lang_combo.setStyleSheet(f"""
            QComboBox {{
                background-color: {BG_BUTTON};
                color: {TEXT_PRIMARY};
                border: 1px solid {BORDER};
                border-radius: 6px;
                padding: 4px 10px;
                font-size: 10pt;
                min-width: 120px;
            }}
            QComboBox:hover {{
                background-color: {BG_BUTTON_HOVER};
            }}
            QComboBox::drop-down {{ border: none; }}
            QComboBox QAbstractItemView {{
                background-color: {BG_BUTTON};
                color: {TEXT_PRIMARY};
                selection-background-color: {ACCENT};
                selection-color: #ffffff;
                border: 1px solid {BORDER};
            }}
        """)
        self._lang_codes: list[str] = []
        for code, display in I18n.available_languages():
            self._lang_combo.addItem(display)
            self._lang_codes.append(code)
        self._lang_combo.currentIndexChanged.connect(self._on_lang_combo)
        self._refresh_lang_combo()
        lang_row.addWidget(self._lang_combo)
        title_row.addLayout(lang_row)
        content_layout.addLayout(title_row)
        content_layout.addSpacing(8)

        # ── Theme selector ────────────────────────────────────────────────────
        theme_title = SectionTitle(I18n.translate("ui", "interface_theme"))
        content_layout.addWidget(theme_title)
        content_layout.addSpacing(6)

        # Ligne 1 : combo thème
        theme_combo_row = QHBoxLayout()
        self._theme_combo = QComboBox()
        self._theme_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        theme_combo_row.addWidget(self._theme_combo)
        theme_combo_row.addStretch(1)
        content_layout.addLayout(theme_combo_row)
        content_layout.addSpacing(6)

        # Ligne 2 : boutons
        theme_btn_row = QHBoxLayout()
        self._theme_create_btn = QPushButton(I18n.translate("ui", "theme_create"))
        self._theme_edit_btn = QPushButton(I18n.translate("ui", "theme_edit"))
        self._theme_delete_btn = QPushButton(I18n.translate("ui", "theme_delete"))
        self._theme_export_btn = QPushButton(I18n.translate("ui", "theme_export"))
        self._theme_import_btn = QPushButton(I18n.translate("ui", "theme_import"))
        theme_btn_row.addWidget(self._theme_create_btn)
        theme_btn_row.addWidget(self._theme_edit_btn)
        theme_btn_row.addWidget(self._theme_delete_btn)
        theme_btn_row.addWidget(self._theme_export_btn)
        theme_btn_row.addWidget(self._theme_import_btn)
        theme_btn_row.addStretch(1)
        content_layout.addLayout(theme_btn_row)

        # Connexions des widgets de thème
        self._theme_combo.currentIndexChanged.connect(self._on_theme_combo_changed)
        self._theme_create_btn.clicked.connect(self.sig_theme_create.emit)
        self._theme_edit_btn.clicked.connect(lambda: self.sig_theme_edit.emit(self._theme_combo.currentData() or ""))
        self._theme_delete_btn.clicked.connect(self._on_theme_delete)
        self._theme_export_btn.clicked.connect(self._on_theme_export)
        self._theme_import_btn.clicked.connect(self._on_theme_import)

        content_layout.addSpacing(12)
        content_layout.addWidget(DividerLine())
        content_layout.addSpacing(12)

        # ── ANC / Transparent section ─────────────────────────────────────────
        # Grouped in one container so it can be hidden wholesale: plenty of
        # headsets have no noise cancelling at all (the Nova 7P, for one), and
        # showing them dead controls reads as a broken app rather than as an
        # absent feature. Shown only once the daemon reports the setting.
        self._anc_section = QWidget(content)
        anc_layout = QVBoxLayout(self._anc_section)
        anc_layout.setContentsMargins(0, 0, 0, 0)
        anc_layout.setSpacing(0)

        anc_layout.addWidget(SectionTitle(I18n.translate("ui", "noise_cancelling")))
        anc_layout.addSpacing(4)

        self._anc_widget = QAncWidget(self._anc_section)
        self._anc_widget.setStyleSheet(f"""
            QWidget {{ background-color: {BG_MAIN}; color: {TEXT_PRIMARY}; }}
            QLabel  {{ background-color: transparent; color: {TEXT_PRIMARY}; font-size: 11pt; }}
        """)
        anc_layout.addWidget(self._anc_widget)
        anc_layout.addSpacing(6)
        anc_layout.addWidget(DividerLine())
        anc_layout.addSpacing(6)

        self._anc_section.setVisible(False)
        content_layout.addWidget(self._anc_section)

        # ── Device Settings section ────────────────────────────────────────────
        device_settings_title = SectionTitle(I18n.translate("ui", "device_settings"))
        content_layout.addWidget(device_settings_title)
        content_layout.addSpacing(4)

        self._device_widget = QSettingsWidget(content, "device", "device")
        self._device_widget.setStyleSheet(
            f"""
            QWidget {{
                background-color: {BG_MAIN};
                color: {TEXT_PRIMARY};
            }}
            QLabel {{
                background-color: transparent;
                color: {TEXT_PRIMARY};
                font-size: 11pt;
            }}
            """
        )
        content_layout.addWidget(self._device_widget)
        content_layout.addSpacing(6)

        # ── Horizontal divider ─────────────────────────────────────────────────
        content_layout.addWidget(DividerLine())
        content_layout.addSpacing(6)

        # ── General Settings section ───────────────────────────────────────────
        general_title = SectionTitle(I18n.translate("ui", "general_settings"))
        content_layout.addWidget(general_title)
        content_layout.addSpacing(4)

        self._general_widget = QSettingsWidget(content, "general", "general")
        self._general_widget.setStyleSheet(
            f"""
            QWidget {{
                background-color: {BG_MAIN};
                color: {TEXT_PRIMARY};
            }}
            QLabel {{
                background-color: transparent;
                color: {TEXT_PRIMARY};
                font-size: 11pt;
            }}
            """
        )
        content_layout.addWidget(self._general_widget)

        # ── Startup toggle ─────────────────────────────────────────────────────
        # Mirror QSettingsWidget.get_widget() row structure exactly so this
        # manual toggle lines up with the general/device-settings toggles above.
        # The row layout must be *set on a QWidget* (not added to content_layout
        # via addLayout): a sub-layout added with addLayout inherits the parent
        # layout's spacing (0 here), whereas a layout set on a widget resolves to
        # the style's default label→control spacing (6px) — the same value
        # get_widget() gets. Without this the toggle sat 6px to the left.
        startup_roww = QWidget()
        startup_row = QHBoxLayout()
        startup_row.setContentsMargins(0, 4, 0, 4)
        startup_roww.setLayout(startup_row)
        startup_label = QLabel(I18n.translate("ui", "launch_at_startup"))
        startup_label.setFixedWidth(260)
        startup_label.setWordWrap(True)
        startup_label.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-size: 11pt; background: transparent;"
        )
        startup_row.addWidget(startup_label)

        self._startup_toggle = QDualState(
            off_text=I18n.translate("settings_values", "off"),
            on_text=I18n.translate("settings_values", "on"),
            init_state="right" if autostart_enabled() else "left",
        )
        self._startup_toggle.setToolTip(f"Autostart via: {active_backend_name()}")
        self._startup_toggle.checkStateChanged.connect(self._on_autostart_toggled)
        startup_row.addWidget(self._startup_toggle)
        startup_row.addStretch(1)
        content_layout.addWidget(startup_roww)

        content_layout.addSpacing(16)

        # ── Telemetry toggle ───────────────────────────────────────────────────
        from arctis_sound_manager.telemetry import get_consent, set_consent

        telemetry_roww = QWidget()
        telemetry_row = QHBoxLayout()
        telemetry_row.setContentsMargins(0, 4, 0, 4)
        telemetry_roww.setLayout(telemetry_row)
        telemetry_label = QLabel("Telemetry — share anonymous usage data")
        telemetry_label.setFixedWidth(260)
        telemetry_label.setWordWrap(True)
        telemetry_label.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-size: 11pt; background: transparent;"
        )
        telemetry_row.addWidget(telemetry_label)

        consent = get_consent()
        self._telemetry_toggle = QDualState(
            off_text=I18n.translate("settings_values", "off"),
            on_text=I18n.translate("settings_values", "on"),
            init_state="right" if consent is True else "left",
        )
        self._telemetry_toggle.checkStateChanged.connect(
            lambda state: set_consent(state == Qt.CheckState.Checked)
        )
        telemetry_row.addWidget(self._telemetry_toggle)
        telemetry_row.addStretch(1)
        content_layout.addWidget(telemetry_roww)

        content_layout.addSpacing(16)

        # ── Clips toggle ──────────────────────────────────────────────────────
        # The only feature whose packages a base install does not already have,
        # so it is the only one with a switch that installs them.
        from arctis_sound_manager.settings import GeneralSettings as _GS

        clips_roww = QWidget()
        clips_row = QHBoxLayout()
        clips_row.setContentsMargins(0, 4, 0, 4)
        clips_roww.setLayout(clips_row)
        clips_label = QLabel(I18n.translate("ui", "clips_enable_setting"))
        clips_label.setFixedWidth(260)
        clips_label.setWordWrap(True)
        clips_label.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-size: 11pt; background: transparent;"
        )
        clips_row.addWidget(clips_label)

        # A button, not a switch. A switch says the state is ASM's to flip;
        # this one installs or removes distro packages, which is slow, asks for
        # a password, and can fail — none of which a switch can express. It also
        # stops the feature from being toggled off and on casually, which is
        # what turned an off-by-default feature into a package transaction the
        # user did not know they had started.
        self._clips_btn = QPushButton("")
        self._clips_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clips_btn.setToolTip(I18n.translate("ui", "clips_enable_tooltip"))
        self._clips_btn.clicked.connect(self._on_clips_button)
        clips_row.addWidget(self._clips_btn)

        self._clips_status = QLabel("")
        self._clips_status.setWordWrap(True)
        self._clips_status.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 9pt; background: transparent;"
        )
        clips_row.addWidget(self._clips_status, stretch=1)

        clips_row.addStretch(0)
        content_layout.addWidget(clips_roww)

        self._refresh_clips_row()

        content_layout.addStretch(1)

        scroll.setWidget(content)
        outer.addWidget(scroll)

        # Populate theme combo with the currently-active theme.
        from arctis_sound_manager.settings import GeneralSettings
        current_theme = GeneralSettings.read_from_file().theme
        self.refresh_theme_combo(current_theme)

        # Apply the currently-active theme on first paint.
        self.apply_theme()

    # ── Theme propagation ─────────────────────────────────────────────────────

    def apply_theme(self, t=None) -> None:
        """Restyle the device/settings page for the current active theme."""
        self.setStyleSheet(f"background-color: {_theme.c('BG_MAIN')};")
        self._scroll.setStyleSheet(f"QScrollArea {{ background-color: {_theme.c('BG_MAIN')}; border: none; }}")
        self._content.setStyleSheet(f"background-color: {_theme.c('BG_MAIN')};")

        # Device settings widget
        if hasattr(self, "_device_widget"):
            self._device_widget.setStyleSheet(f"""
                QWidget {{ background-color: {_theme.c('BG_MAIN')}; color: {_theme.c('TEXT_PRIMARY')}; }}
                QLabel {{ background-color: transparent; color: {_theme.c('TEXT_PRIMARY')}; font-size: 11pt; }}
            """)

        # General settings widget
        if hasattr(self, "_general_widget"):
            self._general_widget.setStyleSheet(f"""
                QWidget {{ background-color: {_theme.c('BG_MAIN')}; color: {_theme.c('TEXT_PRIMARY')}; }}
                QLabel {{ background-color: transparent; color: {_theme.c('TEXT_PRIMARY')}; font-size: 11pt; }}
            """)

        # ANC widget background + pill colors
        if hasattr(self, "_anc_widget"):
            self._anc_widget.setStyleSheet(f"""
                QWidget {{ background-color: {_theme.c('BG_MAIN')}; color: {_theme.c('TEXT_PRIMARY')}; }}
                QLabel  {{ background-color: transparent; color: {_theme.c('TEXT_PRIMARY')}; font-size: 11pt; }}
            """)
            if hasattr(self._anc_widget, "apply_theme"):
                self._anc_widget.apply_theme(t)

        # Language combo
        if hasattr(self, "_lang_combo"):
            self._lang_combo.setStyleSheet(f"""
                QComboBox {{
                    background-color: {_theme.c('BG_BUTTON')};
                    color: {_theme.c('TEXT_PRIMARY')};
                    border: 1px solid {_theme.c('BORDER')};
                    border-radius: 6px;
                    padding: 4px 10px;
                    font-size: 10pt;
                    min-width: 120px;
                }}
                QComboBox:hover {{ background-color: {_theme.c('BG_BUTTON_HOVER')}; }}
                QComboBox::drop-down {{ border: none; }}
                QComboBox QAbstractItemView {{
                    background-color: {_theme.c('BG_BUTTON')};
                    color: {_theme.c('TEXT_PRIMARY')};
                    selection-background-color: {_theme.c('ACCENT')};
                    selection-color: #ffffff;
                    border: 1px solid {_theme.c('BORDER')};
                }}
            """)

        # Update status label
        if hasattr(self, "_update_status_lbl"):
            self._update_status_lbl.setStyleSheet(
                f"color: {_theme.c('TEXT_SECONDARY')}; font-size: 10pt; background: transparent;"
            )

        # Check-for-updates button — restyle via factory function
        if hasattr(self, "_check_update_btn"):
            self._check_update_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {_theme.c('BG_BUTTON')};
                    color: {_theme.c('TEXT_PRIMARY')};
                    border: none;
                    border-radius: 6px;
                    font-size: 11pt;
                    padding: 0 16px;
                }}
                QPushButton:hover {{ background-color: {_theme.c('BG_BUTTON_HOVER')}; }}
            """)

        # Theme combo and buttons state are set via refresh_theme_combo / _update_theme_buttons_state.

    # ── Theme selector ────────────────────────────────────────────────────────

    def refresh_theme_combo(self, selected: str | None = None) -> None:
        self._theme_combo.blockSignals(True)
        self._theme_combo.clear()
        reload_user_themes()
        labels = all_theme_labels()
        for tid, label in labels.items():
            self._theme_combo.addItem(label, tid)
        if selected:
            idx = self._theme_combo.findData(selected)
            if idx >= 0:
                self._theme_combo.setCurrentIndex(idx)
        self._theme_combo.blockSignals(False)
        self._update_theme_buttons_state()

    def _update_theme_buttons_state(self) -> None:
        tid = self._theme_combo.currentData()
        user_theme = tid is not None and not is_builtin(tid)
        self._theme_edit_btn.setEnabled(user_theme)
        self._theme_delete_btn.setEnabled(user_theme)
        self._theme_export_btn.setEnabled(tid is not None)

    def _on_theme_combo_changed(self, index: int) -> None:
        tid = self._theme_combo.itemData(index)
        if tid:
            self._update_theme_buttons_state()
            self.sig_theme_changed.emit(tid)

    def _on_theme_delete(self) -> None:
        from PySide6.QtWidgets import QMessageBox
        tid = self._theme_combo.currentData()
        if not tid or is_builtin(tid):
            return
        name = get_theme_label(tid)
        msg = I18n.translate("ui", "theme_delete_confirm").format(name=name)
        reply = QMessageBox.question(self, "", msg,
                                      QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            delete_user_theme(tid)
            self.refresh_theme_combo("steelseries")
            self.sig_theme_changed.emit("steelseries")

    def _on_theme_export(self) -> None:
        from arctis_sound_manager.gui.theme_export_dialog import ThemeExportDialog
        tid = self._theme_combo.currentData()
        if not tid:
            return
        ThemeExportDialog(get_theme_label(tid), dict(get_theme(tid)), self).exec()

    def _on_theme_import(self) -> None:
        from arctis_sound_manager.gui.theme_import_dialog import ThemeImportDialog
        dlg = ThemeImportDialog(self)
        if dlg.exec() and dlg.imported_theme_id:
            self.refresh_theme_combo(dlg.imported_theme_id)
            self.sig_theme_changed.emit(dlg.imported_theme_id)

    # ── Signal forwarding ─────────────────────────────────────────────────────

    @Slot(object)
    def update_status(self, status: dict):
        self._anc_widget.update_status(status)

    @Slot(object)
    def update_settings(self, settings: dict):
        self._general_widget.update_settings(settings)
        self._device_widget.update_settings(settings)
        self._update_anc_visibility(settings)

    def _update_anc_visibility(self, settings: dict) -> None:
        """Show the ANC section only for headsets that actually have it.

        The device profile is the authority: a headset without noise
        cancelling simply doesn't declare the setting, so its controls could
        never do anything. An empty config means "no device yet" — keep the
        section as it is rather than flashing it away on a transient update.
        """
        config = settings.get('settings_config') or {}
        if not config:
            return
        self._anc_section.setVisible('noise_cancelling' in config)

    # ── Language ───────────────────────────────────────────────────────────────

    def _refresh_lang_combo(self):
        current = I18n.current_lang()
        if current in self._lang_codes:
            self._lang_combo.blockSignals(True)
            self._lang_combo.setCurrentIndex(self._lang_codes.index(current))
            self._lang_combo.blockSignals(False)

    @Slot()
    def rebuild_lang_combo(self) -> None:
        """Repopulate the combo after LangUpdateWorker downloads new files."""
        self._lang_combo.blockSignals(True)
        self._lang_combo.clear()
        self._lang_codes.clear()
        for code, display in I18n.available_languages():
            self._lang_combo.addItem(display)
            self._lang_codes.append(code)
        self._refresh_lang_combo()
        self._lang_combo.blockSignals(False)

    def _on_autostart_toggled(self, state: Qt.CheckState) -> None:
        set_autostart(state == Qt.CheckState.Checked)

    # ── Clips: install / uninstall ────────────────────────────────────────────
    #
    # This was a switch that opened the general dependency dialog when anything
    # was missing. That dialog lists *every* failing check in ASM, and one of
    # them — "pipewire-pulse running" — is remediated by restarting PipeWire and
    # pipewire-pulse. Pressing its Install-all button therefore tore down the
    # audio graph: the headset's card came back with its profile off, and
    # WirePlumber persisted that, so switching Clips on took the user's sound
    # away and kept it away. Reported as "the Clips button breaks it".
    #
    # Nothing here reaches that dialog any more. Clips owns its own packages,
    # installs and removes only those, and cannot touch a service.

    def _clips_missing(self) -> list:
        from arctis_sound_manager.system_deps_checker import clip_dep_checks

        missing = []
        for check in clip_dep_checks():
            try:
                ok = bool(check.detect())
            except Exception:  # noqa: BLE001 — a broken probe reads as missing
                ok = False
            if not ok:
                missing.append(check)
        return missing

    def _refresh_clips_row(self) -> None:
        """Put the button in the state the machine is actually in."""
        from arctis_sound_manager.settings import GeneralSettings

        try:
            enabled = bool(GeneralSettings.read_from_file().clips_enabled)
        except Exception:  # noqa: BLE001
            enabled = False

        missing = self._clips_missing()
        if enabled:
            from arctis_sound_manager.system_deps_checker import Severity

            broken = [c.name for c in missing if c.severity is Severity.BLOCKING]
            if broken:
                # On but unusable: an install that half-succeeded, or a package
                # removed from underneath the feature afterwards. Saying
                # "Installed" here is the lie that made the old switch feel
                # broken — offer the repair instead.
                self._clips_btn.setText(I18n.translate("ui", "clips_repair"))
                self._clips_status.setText(
                    I18n.translate("ui", "clips_enabled_but_missing").format(
                        ", ".join(broken)))
                return
            self._clips_btn.setText(I18n.translate("ui", "clips_uninstall"))
            self._clips_status.setText(I18n.translate("ui", "clips_installed"))
        else:
            self._clips_btn.setText(I18n.translate("ui", "clips_install"))
            self._clips_status.setText(
                I18n.translate("ui", "clips_will_install").format(len(missing))
                if missing else I18n.translate("ui", "clips_ready_to_enable"))

    def _on_clips_button(self) -> None:
        from arctis_sound_manager.settings import GeneralSettings

        try:
            enabled = bool(GeneralSettings.read_from_file().clips_enabled)
        except Exception:  # noqa: BLE001
            enabled = False

        from arctis_sound_manager.system_deps_checker import Severity

        broken = any(c.severity is Severity.BLOCKING
                     for c in self._clips_missing())
        if enabled and not broken:
            self._uninstall_clips()
        else:
            # Enabled but missing something it cannot record without: the
            # button says Repair, and repairing is installing.
            self._install_clips()

    def _clips_pkexec(self, argvs: list[list[str]], busy_text: str) -> bool:
        """Run package commands as one elevated batch, and report the outcome.

        One `pkexec` for the whole batch so the password is asked once. Runs
        synchronously because the button has nothing useful to offer while it
        waits, and the result decides whether the feature is on.

        Only ever handed package-manager argv built from the Clips group —
        never an `_internal` remediation, which is how the old path ended up
        able to restart the audio stack.
        """
        import subprocess

        if not shutil.which("pkexec"):
            self._clips_status.setText(I18n.translate("ui", "clips_no_pkexec"))
            return False

        def _quote(args: list[str]) -> str:
            return " ".join(f"'{a}'" if " " in a else a for a in args)

        self._clips_btn.setEnabled(False)
        self._clips_status.setText(busy_text)
        QApplication.processEvents()
        try:
            proc = subprocess.run(
                ["pkexec", "sh", "-c", " && ".join(_quote(a) for a in argvs)],
                capture_output=True, text=True, timeout=900)
        except (OSError, subprocess.SubprocessError) as exc:
            log.warning("clip package command failed: %s", exc)
            self._clips_status.setText(str(exc))
            return False
        finally:
            self._clips_btn.setEnabled(True)

        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip().splitlines()
            self._clips_status.setText(detail[-1] if detail else
                                       I18n.translate("ui", "clips_pkg_failed"))
            return False
        return True

    def _install_clips(self) -> None:
        from arctis_sound_manager.settings import GeneralSettings
        from arctis_sound_manager.system_deps_checker import (
            Severity, install_command_for)

        missing = self._clips_missing()
        argvs = [cmd for cmd in (install_command_for(c) for c in missing) if cmd]

        if argvs and not self._clips_pkexec(
                argvs, I18n.translate("ui", "clips_installing")):
            self._refresh_clips_row()
            return

        # Re-probe rather than trust the exit code: a package manager can
        # succeed and still leave the thing undetectable (wrong package for the
        # distro, a plugin that needs a re-scan).
        still = [c for c in self._clips_missing()
                 if c.severity is Severity.BLOCKING]
        if still:
            names = ", ".join(c.name for c in still)
            msg = QMessageBox(self)
            msg.setWindowTitle(I18n.translate("ui", "clips"))
            msg.setText(I18n.translate("ui", "clips_deps_missing").format(names))
            msg.setInformativeText(I18n.translate("ui", "clips_deps_missing_hint"))
            msg.setIcon(QMessageBox.Icon.Warning)
            msg.exec()
            self._refresh_clips_row()
            return

        settings = GeneralSettings.read_from_file()
        settings.clips_enabled = True
        settings.write_to_file()
        self._refresh_clips_row()
        self._apply_clips_visibility()

    def _uninstall_clips(self) -> None:
        """Turn Clips off, and offer to remove the packages it brought in.

        Removing is offered separately from disabling, and defaults to no. Every
        one of these packages is shared with the rest of the desktop — ffmpeg
        and the GStreamer sets are used by video players, browsers and
        screenshot tools — so "I am done with clips" is not the same statement
        as "nothing else here needs ffmpeg". The commands do not force, so a
        package another program depends on makes the package manager refuse,
        and the feature still ends up off either way.
        """
        from arctis_sound_manager.settings import GeneralSettings
        from arctis_sound_manager.system_deps_checker import (
            clip_dep_checks, remove_command_for)

        argvs, packages = [], []
        for check in clip_dep_checks():
            cmd = remove_command_for(check)
            if cmd:
                argvs.append(cmd)
                packages.extend(a for a in cmd[3:] if not a.startswith("-"))

        answer = QMessageBox.StandardButton.No
        if argvs:
            box = QMessageBox(self)
            box.setWindowTitle(I18n.translate("ui", "clips_uninstall"))
            box.setText(I18n.translate("ui", "clips_remove_packages_q"))
            box.setInformativeText(
                I18n.translate("ui", "clips_remove_packages_hint").format(
                    ", ".join(sorted(set(packages)))))
            box.setIcon(QMessageBox.Icon.Question)
            box.setStandardButtons(QMessageBox.StandardButton.Yes
                                   | QMessageBox.StandardButton.No
                                   | QMessageBox.StandardButton.Cancel)
            box.setDefaultButton(QMessageBox.StandardButton.No)
            answer = box.exec()
            if answer == QMessageBox.StandardButton.Cancel:
                return

        settings = GeneralSettings.read_from_file()
        settings.clips_enabled = False
        settings.write_to_file()
        self._refresh_clips_row()
        self._apply_clips_visibility()

        if answer == QMessageBox.StandardButton.Yes:
            self._clips_pkexec(argvs, I18n.translate("ui", "clips_removing"))
            self._refresh_clips_row()

    def _apply_clips_visibility(self) -> None:
        """Ask the sidebar to show or hide the Clips entry.

        Looked up through the window rather than held as a reference, because
        this page is constructed before the window has finished wiring itself.
        A missing controller is not an error worth a dialog — the toggle has
        already been saved, and the sidebar will match it at the next start.
        """
        controller = getattr(self.window(), "main_app", None)
        if controller is not None:
            controller.apply_clips_visibility()

    def _on_lang_combo(self, index: int):
        if index < 0 or index >= len(self._lang_codes):
            return
        code = self._lang_codes[index]
        if code == I18n.current_lang():
            return
        I18n.get_instance().set_language(code)
        msg = QMessageBox(self)
        msg.setWindowTitle("Language / Langue / Idioma")
        msg.setText(I18n.translate("ui", "language_changed"))
        msg.setIcon(QMessageBox.Icon.Information)
        msg.exec()

    def _on_check_update(self) -> None:
        from arctis_sound_manager.update_checker import UpdateCheckWorker
        from arctis_sound_manager.utils import project_version

        self._check_update_btn.setEnabled(False)
        self._check_update_btn.setText(I18n.translate("ui", "checking_updates"))
        self._update_status_lbl.setText("")
        self._update_url = ""

        self._update_worker = UpdateCheckWorker(project_version(), force=True)
        self._update_worker.result.connect(self._on_check_update_result)
        self._update_worker.start()

    @Slot(str, str, str)
    def _on_check_update_result(self, version: str, url: str, wheel_url: str) -> None:
        self._check_update_btn.setEnabled(True)
        self._check_update_btn.setText(I18n.translate("ui", "check_for_updates"))

        if version:
            self._update_url = url
            self._update_wheel_url = wheel_url
            self._update_version = version
            self._update_status_lbl.setStyleSheet(
                f"color: {ACCENT}; font-size: 10pt; background: transparent; text-decoration: underline;"
            )
            self._update_status_lbl.setText(f"v{version} available — click to install")
            self._update_status_lbl.mousePressEvent = lambda _: self._do_install_update()
            self._update_status_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self._update_status_lbl.setStyleSheet(
                f"color: {TEXT_SECONDARY}; font-size: 10pt; background: transparent;"
            )
            self._update_status_lbl.setText(I18n.translate("ui", "up_to_date"))

        self.sig_update_result.emit(version, url, wheel_url)

    def _do_install_update(self) -> None:
        from arctis_sound_manager.update_checker import (
            InstallMethod, UpdateInstallWorker, build_terminal_cmd,
            detect_all_install_methods, package_manager_command,
        )
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import (
            QApplication, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
        )
        from arctis_sound_manager.gui.theme import BG_BUTTON_HOVER, BG_CARD, BG_MAIN, BORDER

        all_methods = detect_all_install_methods()
        if len(all_methods) > 1:
            from arctis_sound_manager.gui.install_dialogs import show_multi_install_warning
            show_multi_install_warning(self, all_methods)
            return

        method = all_methods[0] if all_methods else InstallMethod.PIP
        cmd = package_manager_command(method)

        if cmd:
            terminal_args = build_terminal_cmd(cmd)
            dlg = QDialog(self)
            dlg.setWindowTitle("Update available")
            dlg.setMinimumWidth(480)
            dlg.setStyleSheet(f"background-color: {BG_MAIN}; color: {TEXT_PRIMARY};")
            layout = QVBoxLayout(dlg)
            layout.setContentsMargins(24, 20, 24, 20)
            layout.setSpacing(12)

            msg = ("ASM was installed via your package manager.\n"
                   "Click \"Update now\" to open a terminal and run the update:"
                   if terminal_args else
                   "ASM was installed via your package manager.\n"
                   "Run this command in a terminal to update:")
            lbl = QLabel(msg)
            lbl.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 10pt; background: transparent;")
            lbl.setWordWrap(True)
            layout.addWidget(lbl)

            cmd_lbl = QLabel(cmd)
            cmd_lbl.setStyleSheet(
                f"background-color: {BG_CARD}; color: {TEXT_PRIMARY}; font-family: monospace; "
                f"font-size: 10pt; padding: 10px; border-radius: 6px; border: 1px solid {BORDER};"
            )
            cmd_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(cmd_lbl)

            btn_row = QHBoxLayout()
            btn_row.addStretch()
            if terminal_args:
                open_btn = QPushButton("Update now")
                open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                open_btn.setStyleSheet(
                    f"QPushButton {{ background-color: {ACCENT}; color: #fff; border: none; "
                    f"border-radius: 6px; padding: 8px 18px; font-size: 10pt; }}"
                    f"QPushButton:hover {{ background-color: {BG_BUTTON_HOVER}; }}"
                )
                def _open_terminal():
                    import subprocess as _sp
                    _sp.Popen(terminal_args)
                    dlg.accept()
                open_btn.clicked.connect(_open_terminal)
                btn_row.addWidget(open_btn)

            copy_btn = QPushButton("Copy command")
            copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            copy_btn.setStyleSheet(
                f"QPushButton {{ background-color: {'transparent' if terminal_args else ACCENT}; "
                f"color: {TEXT_PRIMARY if terminal_args else '#fff'}; "
                f"border: {'1px solid ' + BORDER if terminal_args else 'none'}; "
                f"border-radius: 6px; padding: 8px 18px; font-size: 10pt; }}"
                f"QPushButton:hover {{ background-color: {BG_BUTTON_HOVER}; color: {TEXT_PRIMARY}; }}"
            )
            def _copy():
                from PySide6.QtGui import QClipboard
                QApplication.clipboard().setText(cmd, QClipboard.Mode.Clipboard)
                copy_btn.setText("Copied!")
                copy_btn.setEnabled(False)
                # context=copy_btn: cancels the timer if the dialog is closed
                # before it fires, avoiding a shiboken use-after-free (issue #100).
                QTimer.singleShot(2000, copy_btn, lambda: (copy_btn.setText("Copy command"), copy_btn.setEnabled(True)))
            copy_btn.clicked.connect(_copy)
            btn_row.addWidget(copy_btn)

            close_btn = QPushButton("Close")
            close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            close_btn.setStyleSheet(
                f"QPushButton {{ background-color: {BG_BUTTON}; color: {TEXT_PRIMARY}; border: none; "
                f"border-radius: 6px; padding: 8px 18px; font-size: 10pt; }}"
                f"QPushButton:hover {{ background-color: {BG_BUTTON_HOVER}; }}"
            )
            close_btn.clicked.connect(dlg.accept)
            btn_row.addWidget(close_btn)
            layout.addLayout(btn_row)
            dlg.exec()
            return

        # pipx / pip — in-app wheel install
        if not self._update_wheel_url:
            QDesktopServices.openUrl(QUrl(self._update_url))
            return

        self._update_status_lbl.setText("Installing…")
        self._update_status_lbl.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 10pt; background: transparent;"
        )
        self._update_status_lbl.setCursor(Qt.CursorShape.ArrowCursor)
        self._update_status_lbl.mousePressEvent = None

        self._install_worker = UpdateInstallWorker(self._update_wheel_url)
        self._install_worker.finished.connect(self._on_install_finished)
        self._install_worker.start()

    @Slot(bool, str)
    def _on_install_finished(self, success: bool, error_msg: str) -> None:
        import os, sys
        if success:
            self._update_status_lbl.setText("Update installed — running setup…")
            from pathlib import Path
            (Path.home() / ".config" / "arctis_manager" / ".setup_done").unlink(missing_ok=True)
            from arctis_sound_manager.gui.first_run_dialog import FirstRunDialog
            FirstRunDialog(self).exec()
            from arctis_sound_manager import service_control as sc
            sc.restart("arctis-manager")
            os.execv(sys.executable, [sys.executable, "-m", "arctis_sound_manager.scripts.gui"])
        else:
            self._update_status_lbl.setStyleSheet(
                "color: #FF5555; font-size: 10pt; background: transparent;"
            )
            self._update_status_lbl.setText(f"Update failed: {error_msg}")
