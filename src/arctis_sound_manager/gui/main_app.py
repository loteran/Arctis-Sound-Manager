"""
Main application window — ArctisSonar GUI visual style.
"""
import logging

from PySide6.QtCore import Qt, QUrl, Slot
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from arctis_sound_manager.gui.base_app import QBaseDesktopApp
from arctis_sound_manager.gui.components import (
    EQUALIZER_ICON,
    HEADPHONE_ICON,
    HELP_ICON,
    HOME_ICON,
    SETTINGS_ICON,
    SidebarButton,
)
from arctis_sound_manager.gui.dbus_wrapper import DbusWrapper
from arctis_sound_manager.gui.device_page import DevicePage
from arctis_sound_manager.gui.headset_page import HeadsetPage
from arctis_sound_manager.gui.help_page import HelpPage
from arctis_sound_manager.gui.equalizer_page import EqualizerPage
from arctis_sound_manager.gui.home_page import HomePage
from arctis_sound_manager.gui.main_app_proto_widget import QMainAppProtoWidget
from arctis_sound_manager.gui.theme import (
    ACCENT,
    APP_QSS,
    BG_MAIN,
    BG_SIDEBAR,
    BORDER,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)
from arctis_sound_manager.gui.ui_utils import get_icon_pixmap


# ── Main application window ───────────────────────────────────────────────────

class QMainApp(QBaseDesktopApp):
    app: QApplication
    main_window: QMainAppProtoWidget

    def __init__(self, app: QApplication, log_level: int):
        super().__init__(parent=app)

        self.logger = logging.getLogger("QMainApp")
        self.logger.setLevel(log_level)

        self.app = app
        self.settings: dict = {}
        self.status: dict = {}

        # Apply global dark stylesheet
        app.setStyleSheet(APP_QSS)

        # D-Bus wrapper
        self.dbus_wrapper = DbusWrapper()
        self.dbus_wrapper.sig_settings.connect(self.on_settings_received)
        self.dbus_wrapper.sig_status.connect(self.on_status_received)

        # Build window
        self.main_window = self._build_window()

        # Wire D-Bus signals to pages
        self.dbus_wrapper.sig_status.connect(self._home_page.update_status)
        self.dbus_wrapper.sig_status.connect(self._headset_page.update_status)
        self.dbus_wrapper.sig_status.connect(self._device_page.update_status)
        self.dbus_wrapper.sig_settings.connect(self._home_page.update_settings)
        self.dbus_wrapper.sig_settings.connect(self._headset_page.update_settings)
        self.dbus_wrapper.sig_settings.connect(self._device_page.update_settings)

        # Start on home page
        self._switch_page(0)

        self.destroyed.connect(self.sig_stop)
        self.main_window.visibilityChanged.connect(self._on_visibility_changed)

    # ── Visibility ────────────────────────────────────────────────────────────

    def _on_visibility_changed(self, visible: bool):
        if visible:
            self.logger.debug("App is visible — starting D-Bus polling")
            self.dbus_wrapper.request_settings(one_time=True)
            self.dbus_wrapper.request_status()
        else:
            self.logger.debug("App is hidden — stopping D-Bus polling")
            self.dbus_wrapper.stop()

    # ── Window construction ───────────────────────────────────────────────────

    def _build_window(self) -> QMainAppProtoWidget:
        window = QMainAppProtoWidget()
        window.setWindowFlags(Qt.WindowType.Window)
        window.setWindowTitle("Arctis Sound Manager")
        window.setWindowIcon(QIcon(get_icon_pixmap()))
        window.setMinimumSize(900, 650)
        window.setStyleSheet(f"background-color: {BG_MAIN};")

        available = window.screen().availableGeometry()
        window.resize(
            min(1400, available.width()),
            min(990, available.height()),
        )

        root_layout = QHBoxLayout(window)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # ── Sidebar ───────────────────────────────────────────────────────────
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(150)
        sidebar.setStyleSheet(
            f"QWidget#sidebar {{ background-color: {BG_SIDEBAR}; border-right: 1px solid {BORDER}; }}"
        )
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(15, 16, 15, 16)
        sidebar_layout.setSpacing(8)

        # Top navigation buttons: Home, Equalizer, Headset, Settings
        top_pages_def = [
            (HOME_ICON,      "Home",      ACCENT),
            (EQUALIZER_ICON, "Equalizer", ACCENT),
            (HEADPHONE_ICON, "Headset/DAC<br><span style='font-size:8pt'>Infos</span>", ACCENT),
            (SETTINGS_ICON,  "Settings",  ACCENT),
        ]

        self._sidebar_buttons: list[SidebarButton] = []
        for svg_path, label, color_active in top_pages_def:
            btn = SidebarButton(
                svg_path=svg_path,
                label=label,
                icon_color_inactive=TEXT_SECONDARY,
                icon_color_active=color_active,
                parent=sidebar,
            )
            idx = len(self._sidebar_buttons)
            btn.clicked.connect(lambda checked=False, i=idx: self._switch_page(i))
            sidebar_layout.addWidget(btn, alignment=Qt.AlignmentFlag.AlignHCenter)
            self._sidebar_buttons.append(btn)

        sidebar_layout.addStretch(1)

        # Help button at the bottom
        help_btn = SidebarButton(
            svg_path=HELP_ICON,
            label="Help",
            icon_color_inactive=TEXT_SECONDARY,
            icon_color_active=ACCENT,
            parent=sidebar,
        )
        help_idx = len(self._sidebar_buttons)
        help_btn.clicked.connect(lambda checked=False, i=help_idx: self._switch_page(i))
        sidebar_layout.addWidget(help_btn, alignment=Qt.AlignmentFlag.AlignHCenter)
        self._sidebar_buttons.append(help_btn)

        sidebar_layout.addSpacing(help_btn.sizeHint().height())

        # GitHub link
        gh_btn = QPushButton("GitHub Repo")
        gh_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none; color: {TEXT_SECONDARY}; "
            f"font-size: 8pt; text-decoration: underline; padding: 4px 0; }}"
            f"QPushButton:hover {{ color: {ACCENT}; }}"
        )
        gh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        gh_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl("https://github.com/loteran/Arctis-Sound-Manager"))
        )
        sidebar_layout.addWidget(gh_btn, alignment=Qt.AlignmentFlag.AlignHCenter)

        # Version label
        from arctis_sound_manager.utils import project_version
        ver_label = QLabel(f"v{project_version()}")
        ver_label.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 8pt; background: transparent; padding: 2px 0;"
        )
        ver_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        sidebar_layout.addWidget(ver_label, alignment=Qt.AlignmentFlag.AlignHCenter)

        root_layout.addWidget(sidebar)

        # ── Content area ──────────────────────────────────────────────────────
        content_wrapper = QWidget()
        content_wrapper.setStyleSheet(f"background-color: {BG_MAIN};")
        content_layout = QVBoxLayout(content_wrapper)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        # Stacked pages
        self._stack = QStackedWidget()
        self._stack.setStyleSheet(f"background-color: {BG_MAIN};")

        self._home_page      = HomePage()
        self._equalizer_page = EqualizerPage()
        self._headset_page   = HeadsetPage()
        self._device_page    = DevicePage()
        self._help_page      = HelpPage()

        self._stack.addWidget(self._home_page)      # index 0 → Home
        self._stack.addWidget(self._equalizer_page) # index 1 → Equalizer
        self._stack.addWidget(self._headset_page)   # index 2 → Headset
        self._stack.addWidget(self._device_page)    # index 3 → Settings
        self._stack.addWidget(self._help_page)      # index 4 → Help

        content_layout.addWidget(self._stack)
        root_layout.addWidget(content_wrapper, stretch=1)

        return window

    # ── Page switching ────────────────────────────────────────────────────────

    def _switch_page(self, index: int):
        self._stack.setCurrentIndex(index)
        for i, btn in enumerate(self._sidebar_buttons):
            btn.set_active(i == index)

    # ── Public API (called by systray_app) ────────────────────────────────────

    def start_sync(self):
        self.logger.info("Starting Main Window app.")
        self.main_window.show()
        self.app.exec()

    async def start(self):
        self.start_sync()

    # ── D-Bus signal handlers ─────────────────────────────────────────────────

    def on_settings_received(self, settings: dict):
        if settings == self.settings:
            return
        self.settings = settings

    def on_status_received(self, status: dict):
        if status == self.status:
            return
        self.status = status

    @Slot()
    def sig_stop(self):
        if getattr(self, "_stopping", False):
            return
        self._stopping = True
        self.dbus_wrapper.stop()
        self.logger.debug("Received shutdown signal, shutting down.")
        self.app.quit()
