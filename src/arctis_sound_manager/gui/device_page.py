# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Device / Settings page — ArctisSonar GUI visual style.
Matches the ref_settingsPage.png design.
"""
import shutil
from pathlib import Path

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
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
    reload_user_themes, export_theme_to_file, import_theme_from_file,
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
        anc_title = SectionTitle(I18n.translate("ui", "noise_cancelling"))
        content_layout.addWidget(anc_title)
        content_layout.addSpacing(4)

        self._anc_widget = QAncWidget(content)
        self._anc_widget.setStyleSheet(f"""
            QWidget {{ background-color: {BG_MAIN}; color: {TEXT_PRIMARY}; }}
            QLabel  {{ background-color: transparent; color: {TEXT_PRIMARY}; font-size: 11pt; }}
        """)
        content_layout.addWidget(self._anc_widget)
        content_layout.addSpacing(6)
        content_layout.addWidget(DividerLine())
        content_layout.addSpacing(6)

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
        startup_row = QHBoxLayout()
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
        content_layout.addLayout(startup_row)

        content_layout.addSpacing(16)

        # ── Telemetry toggle ───────────────────────────────────────────────────
        from arctis_sound_manager.telemetry import get_consent, set_consent

        telemetry_row = QHBoxLayout()
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
        content_layout.addLayout(telemetry_row)

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
        from PySide6.QtWidgets import QFileDialog
        from pathlib import Path
        tid = self._theme_combo.currentData()
        if not tid:
            return
        default = str(Path.home() / f"{tid}.ini")
        path, _ = QFileDialog.getSaveFileName(
            self,
            I18n.translate("ui", "theme_export_dialog"),
            default,
            I18n.translate("ui", "theme_ini_filter"),
        )
        if path:
            export_theme_to_file(tid, Path(path))

    def _on_theme_import(self) -> None:
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        from pathlib import Path
        path, _ = QFileDialog.getOpenFileName(
            self,
            I18n.translate("ui", "theme_import_dialog"),
            str(Path.home()),
            I18n.translate("ui", "theme_ini_filter"),
        )
        if not path:
            return
        try:
            new_id = import_theme_from_file(Path(path))
            self.refresh_theme_combo(new_id)
            self.sig_theme_changed.emit(new_id)
        except ValueError:
            QMessageBox.warning(self, "", I18n.translate("ui", "theme_import_failed"))

    # ── Signal forwarding ─────────────────────────────────────────────────────

    @Slot(object)
    def update_status(self, status: dict):
        self._anc_widget.update_status(status)

    @Slot(object)
    def update_settings(self, settings: dict):
        self._general_widget.update_settings(settings)
        self._device_widget.update_settings(settings)

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
            InstallMethod, PACKAGE_MANAGER_COMMANDS, UpdateInstallWorker,
            build_terminal_cmd, detect_all_install_methods,
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
        cmd = PACKAGE_MANAGER_COMMANDS.get(method)

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
