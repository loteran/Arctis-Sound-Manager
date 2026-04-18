"""
Device / Settings page — ArctisSonar GUI visual style.
Matches the ref_settingsPage.png design.
"""
import os
import shutil
import subprocess
from pathlib import Path

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
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
from arctis_sound_manager.gui.theme import (
    ACCENT,
    BG_CARD,
    BG_BUTTON,
    BG_BUTTON_HOVER,
    BG_MAIN,
    BORDER,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)

_SERVICE = "arctis-manager.service"
_GUI_SERVICE = "arctis-gui.service"


def _autostart_enabled() -> bool:
    result = subprocess.run(
        ["systemctl", "--user", "is-enabled", _SERVICE],
        capture_output=True, text=True,
    )
    return result.stdout.strip() == "enabled"


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
    subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
    return gui_service_path


def _set_autostart(enabled: bool) -> None:
    action = "enable" if enabled else "disable"
    subprocess.run(
        ["systemctl", "--user", action, _SERVICE],
        capture_output=True,
    )
    gui_service_path = _ensure_gui_service() if enabled else (
        Path.home() / ".config" / "systemd" / "user" / _GUI_SERVICE
    )
    if gui_service_path and gui_service_path.exists():
        subprocess.run(
            ["systemctl", "--user", action, _GUI_SERVICE],
            capture_output=True,
        )


def _styled_button(text: str) -> QPushButton:
    btn = QPushButton(text)
    btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    btn.setFixedHeight(44)
    btn.setStyleSheet(
        f"""
        QPushButton {{
            background-color: {BG_BUTTON};
            color: {TEXT_PRIMARY};
            border: none;
            border-radius: 6px;
            font-size: 11pt;
            padding: 0 16px;
        }}
        QPushButton:hover {{
            background-color: {BG_BUTTON_HOVER};
        }}
        """
    )
    return btn


class DevicePage(QWidget):
    """
    Settings page with:
    - Title "Arctis Sound Manager" bold + subtitle "Device Settings"
    - "General Settings" section title (gray ~20pt)
    - Settings form rows (labels + controls)
    - Horizontal divider
    - "Devices" section with a card showing connected headset
    """

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
            f"QScrollArea {{ background-color: {BG_MAIN}; border: none; }}"
        )

        content = QWidget()
        content.setStyleSheet(f"background-color: {BG_MAIN};")
        content_layout = QVBoxLayout(content)
        content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        content_layout.setContentsMargins(36, 12, 36, 12)
        content_layout.setSpacing(0)

        # ── App title + language selector ─────────────────────────────────────
        title_row = QHBoxLayout()
        title_row.setSpacing(16)

        app_title = QLabel(I18n.translate("ui", "app_name"))
        app_title.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-size: 28pt; font-weight: bold; background: transparent;"
        )
        title_row.addWidget(app_title, stretch=1)

        lang_row = QHBoxLayout()
        lang_row.setSpacing(6)
        lang_row.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        lang_label = QLabel("Language:")
        lang_label.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 10pt; background: transparent;"
        )
        lang_row.addWidget(lang_label)

        self._lang_buttons: dict[str, QPushButton] = {}
        for code, display in [("en", "EN"), ("fr", "FR"), ("es", "ES")]:
            btn = QPushButton(display)
            btn.setFixedHeight(30)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, c=code: self._on_lang(c))
            lang_row.addWidget(btn)
            self._lang_buttons[code] = btn

        self._refresh_lang_buttons()
        title_row.addLayout(lang_row)
        content_layout.addLayout(title_row)
        content_layout.addSpacing(8)

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

        # ── DAC Settings section ───────────────────────────────────────────────
        self._dac_divider = DividerLine()
        content_layout.addWidget(self._dac_divider)
        content_layout.addSpacing(6)

        self._dac_title = SectionTitle(I18n.translate("ui", "dac_settings"))
        content_layout.addWidget(self._dac_title)
        content_layout.addSpacing(4)

        self._dac_widget = QSettingsWidget(content, "dac_settings", "dac")
        self._dac_widget.setStyleSheet(
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
        content_layout.addWidget(self._dac_widget)
        content_layout.addSpacing(6)

        self._dac_divider.setVisible(False)
        self._dac_title.setVisible(False)
        self._dac_widget.setVisible(False)

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
            init_state="right" if _autostart_enabled() else "left",
        )
        self._startup_toggle.checkStateChanged.connect(self._on_autostart_toggled)
        startup_row.addWidget(self._startup_toggle)
        startup_row.addStretch(1)
        content_layout.addLayout(startup_row)

        content_layout.addSpacing(16)
        content_layout.addWidget(DividerLine())
        content_layout.addSpacing(10)

        # ── Check for updates ──────────────────────────────────────────────────
        update_row = QHBoxLayout()
        self._check_update_btn = _styled_button(I18n.translate("ui", "check_for_updates"))
        self._check_update_btn.setFixedWidth(220)
        self._check_update_btn.clicked.connect(self._on_check_update)
        update_row.addWidget(self._check_update_btn)

        self._update_status_lbl = QLabel("")
        self._update_status_lbl.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 10pt; background: transparent;"
        )
        self._update_status_lbl.setWordWrap(True)
        update_row.addWidget(self._update_status_lbl, stretch=1)
        content_layout.addLayout(update_row)

        self._update_url: str = ""

        content_layout.addStretch(1)

        scroll.setWidget(content)
        outer.addWidget(scroll)

    # ── Signal forwarding ─────────────────────────────────────────────────────

    @Slot(object)
    def update_status(self, status: dict):
        self._anc_widget.update_status(status)

    @Slot(object)
    def update_settings(self, settings: dict):
        self._general_widget.update_settings(settings)
        self._device_widget.update_settings(settings)

        has_dac = bool('dac' in settings and settings['dac'])
        self._dac_divider.setVisible(has_dac)
        self._dac_title.setVisible(has_dac)
        self._dac_widget.setVisible(has_dac)
        if has_dac:
            self._dac_widget.update_settings(settings['dac'])

    # ── Language ───────────────────────────────────────────────────────────────

    def _refresh_lang_buttons(self):
        current = I18n.current_lang()
        for code, btn in self._lang_buttons.items():
            if code == current:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background-color: {ACCENT};
                        color: #ffffff;
                        border: 1px solid {ACCENT};
                        border-radius: 6px;
                        padding: 4px 14px;
                        font-size: 10pt;
                        font-weight: bold;
                    }}
                """)
            else:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background-color: {BG_BUTTON};
                        color: {TEXT_SECONDARY};
                        border: 1px solid {BORDER};
                        border-radius: 6px;
                        padding: 4px 14px;
                        font-size: 10pt;
                        font-weight: bold;
                    }}
                    QPushButton:hover {{
                        background-color: {BG_BUTTON_HOVER};
                        color: {TEXT_PRIMARY};
                    }}
                """)

    def _on_autostart_toggled(self, state: Qt.CheckState) -> None:
        _set_autostart(state == Qt.CheckState.Checked)

    def _on_lang(self, code: str):
        if code == I18n.current_lang():
            return
        I18n.get_instance().set_language(code)
        self._refresh_lang_buttons()
        _RESTART_MSG = {
            "en": "Language changed. The change will take effect on the next startup.",
            "fr": "Langue modifiée. Le changement sera pris en compte au prochain démarrage.",
            "es": "Idioma cambiado. El cambio se aplicará en el próximo inicio.",
        }
        msg = QMessageBox(self)
        msg.setWindowTitle("Language / Langue / Idioma")
        msg.setText(_RESTART_MSG.get(code, _RESTART_MSG["en"]))
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

    def _do_install_update(self) -> None:
        from arctis_sound_manager.update_checker import (
            PACKAGE_MANAGER_COMMANDS, UpdateInstallWorker,
            build_terminal_cmd, detect_install_method,
        )
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import (
            QApplication, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
        )
        from arctis_sound_manager.gui.theme import BG_BUTTON_HOVER, BG_CARD, BG_MAIN, BORDER

        method = detect_install_method()
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
                QTimer.singleShot(2000, lambda: (copy_btn.setText("Copy command"), copy_btn.setEnabled(True)))
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
        import os, subprocess, sys
        if success:
            self._update_status_lbl.setText("Update installed — restarting…")
            subprocess.run(["asm-cli", "desktop", "write"], capture_output=True)
            subprocess.Popen(["systemctl", "--user", "restart", "arctis-manager"])
            os.execv(sys.executable, [sys.executable, "-m", "arctis_sound_manager.scripts.gui"])
        else:
            self._update_status_lbl.setStyleSheet(
                f"color: #FF5555; font-size: 10pt; background: transparent;"
            )
            self._update_status_lbl.setText(f"Update failed: {error_msg}")
