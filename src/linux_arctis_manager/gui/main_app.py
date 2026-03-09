"""
Main application window — ArctisSonar GUI visual style.
"""
import logging

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from linux_arctis_manager.gui.base_app import QBaseDesktopApp
from linux_arctis_manager.gui.components import (
    HEADPHONE_ICON,
    HOME_ICON,
    SETTINGS_ICON,
    SidebarButton,
)
from linux_arctis_manager.gui.dbus_wrapper import DbusWrapper
from linux_arctis_manager.gui.device_page import DevicePage
from linux_arctis_manager.gui.equalizer_page import EqualizerPage
from linux_arctis_manager.gui.home_page import HomePage
from linux_arctis_manager.gui.main_app_proto_widget import QMainAppProtoWidget
from linux_arctis_manager.gui.theme import (
    ACCENT,
    APP_QSS,
    BG_MAIN,
    BG_SIDEBAR,
    BORDER,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)
from linux_arctis_manager.gui.ui_utils import get_icon_pixmap


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
        self.dbus_wrapper.sig_status.connect(self._device_page.update_status)
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
            self.dbus_wrapper.request_status(one_time=True)
        else:
            self.logger.debug("App is hidden — stopping D-Bus polling")
            self.dbus_wrapper.stop()

    # ── Window construction ───────────────────────────────────────────────────

    def _build_window(self) -> QMainAppProtoWidget:
        window = QMainAppProtoWidget()
        window.setWindowFlags(Qt.WindowType.Window)
        window.setWindowTitle("Arctis Manager")
        window.setWindowIcon(QIcon(get_icon_pixmap()))
        window.setMinimumSize(900, 650)
        window.setStyleSheet(f"background-color: {BG_MAIN};")

        available = window.screen().availableGeometry()
        window.resize(
            min(1200, available.width()),
            min(750, available.height()),
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

        # Navigation buttons definition: (svg_path, label, icon_color_active)
        pages_def = [
            (HOME_ICON,      "Home",      ACCENT),
            (SETTINGS_ICON,  "Settings",  ACCENT),
            (HEADPHONE_ICON, "Equalizer", ACCENT),
        ]

        self._sidebar_buttons: list[SidebarButton] = []
        for svg_path, label, color_active in pages_def:
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

        self._home_page = HomePage()
        self._device_page = DevicePage()
        self._equalizer_page = EqualizerPage()

        self._stack.addWidget(self._home_page)      # index 0  → Home button
        self._stack.addWidget(self._device_page)    # index 1  → Settings button
        self._stack.addWidget(self._equalizer_page) # index 2  → Help button

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
