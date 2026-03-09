"""
Main application window — SteelSeries Stealth dark theme.
"""
import logging

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QColor, QIcon, QPalette
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

from linux_arctis_manager.gui.base_app import QBaseDesktopApp
from linux_arctis_manager.gui.dbus_wrapper import DbusWrapper
from linux_arctis_manager.gui.device_page import DevicePage
from linux_arctis_manager.gui.equalizer_page import EqualizerPage
from linux_arctis_manager.gui.home_page import HomePage
from linux_arctis_manager.gui.main_app_proto_widget import QMainAppProtoWidget
from linux_arctis_manager.gui.theme import (
    ACCENT,
    APP_QSS,
    BG_BUTTON,
    BG_BUTTON_HOVER,
    BG_MAIN,
    BG_SIDEBAR,
    BORDER,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)
from linux_arctis_manager.gui.ui_utils import get_icon_pixmap


# ── Sidebar button ────────────────────────────────────────────────────────────

_SIDEBAR_BTN_BASE = f"""
    QPushButton {{
        background-color: transparent;
        color: {TEXT_SECONDARY};
        border: none;
        border-radius: 10px;
        font-size: 10pt;
        text-align: center;
        padding: 0;
    }}
    QPushButton:hover {{
        background-color: {BG_BUTTON_HOVER};
        color: {TEXT_PRIMARY};
    }}
"""

_SIDEBAR_BTN_ACTIVE = f"""
    QPushButton {{
        background-color: {BG_BUTTON};
        color: {ACCENT};
        border: none;
        border-left: 3px solid {ACCENT};
        border-radius: 10px;
        font-size: 10pt;
        font-weight: bold;
        text-align: center;
        padding: 0;
    }}
"""


class SidebarButton(QPushButton):
    """
    A 120×130 px sidebar icon button that shows an emoji on top
    and a text label below, switching colour when active.
    """

    def __init__(self, emoji: str, label: str, parent: QWidget | None = None):
        # Build the button text with the emoji on a first line and the label below
        super().__init__(f"{emoji}\n{label}", parent)
        self.setFixedSize(120, 130)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setCheckable(False)
        self._active = False
        self.setStyleSheet(_SIDEBAR_BTN_BASE)

    def set_active(self, active: bool):
        self._active = active
        if active:
            self.setStyleSheet(_SIDEBAR_BTN_ACTIVE)
        else:
            self.setStyleSheet(_SIDEBAR_BTN_BASE)


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
        window.setMinimumSize(900, 600)
        window.setStyleSheet(f"background-color: {BG_MAIN};")

        available = window.screen().availableGeometry()
        window.resize(
            min(1100, available.width()),
            min(700, available.height()),
        )

        root_layout = QHBoxLayout(window)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # ── Sidebar ───────────────────────────────────────────────────────────
        sidebar = QWidget()
        sidebar.setFixedWidth(140)
        sidebar.setStyleSheet(
            f"background-color: {BG_SIDEBAR}; border-right: 1px solid {BORDER};"
        )
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(10, 16, 10, 16)
        sidebar_layout.setSpacing(8)
        sidebar_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # Logo / app name at the top of the sidebar
        logo_label = QLabel("🎧")
        logo_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        logo_label.setStyleSheet(
            f"font-size: 28pt; background: transparent; color: {ACCENT};"
        )
        sidebar_layout.addWidget(logo_label)

        app_name_label = QLabel("Arctis\nManager")
        app_name_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        app_name_label.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-size: 9pt; font-weight: bold; "
            f"background: transparent; margin-bottom: 16px;"
        )
        sidebar_layout.addWidget(app_name_label)

        # Separator
        sep = QWidget()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background-color: {BORDER};")
        sidebar_layout.addWidget(sep)
        sidebar_layout.addSpacing(8)

        # Navigation buttons
        pages_def = [
            ("🎮", "Home", 0),
            ("🎧", "Device", 1),
            ("🎚", "EQ", 2),
        ]
        self._sidebar_buttons: list[SidebarButton] = []
        for emoji, label, idx in pages_def:
            btn = SidebarButton(emoji, label, sidebar)
            btn.clicked.connect(lambda checked=False, i=idx: self._switch_page(i))
            sidebar_layout.addWidget(btn, alignment=Qt.AlignmentFlag.AlignHCenter)
            self._sidebar_buttons.append(btn)

        sidebar_layout.addStretch(1)

        # Version / footer text at bottom of sidebar
        footer = QLabel("v2.0")
        footer.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        footer.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 8pt; background: transparent;"
        )
        sidebar_layout.addWidget(footer)

        root_layout.addWidget(sidebar)

        # ── Content area ──────────────────────────────────────────────────────
        content_wrapper = QWidget()
        content_wrapper.setStyleSheet(f"background-color: {BG_MAIN};")
        content_layout = QVBoxLayout(content_wrapper)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        # Top bar
        topbar = QWidget()
        topbar.setFixedHeight(56)
        topbar.setStyleSheet(
            f"background-color: {BG_MAIN}; border-bottom: 1px solid {BORDER};"
        )
        topbar_layout = QHBoxLayout(topbar)
        topbar_layout.setContentsMargins(24, 0, 24, 0)

        self._page_title_label = QLabel("Home")
        self._page_title_label.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-size: 14pt; font-weight: bold; "
            f"background: transparent;"
        )
        topbar_layout.addWidget(self._page_title_label)
        topbar_layout.addStretch(1)

        brand_label = QLabel("Arctis Manager")
        brand_label.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 11pt; background: transparent;"
        )
        topbar_layout.addWidget(brand_label)

        content_layout.addWidget(topbar)

        # Stacked pages
        self._stack = QStackedWidget()
        self._stack.setStyleSheet(f"background-color: {BG_MAIN};")

        self._home_page = HomePage()
        self._device_page = DevicePage()
        self._equalizer_page = EqualizerPage()

        self._stack.addWidget(self._home_page)      # index 0
        self._stack.addWidget(self._device_page)    # index 1
        self._stack.addWidget(self._equalizer_page) # index 2

        content_layout.addWidget(self._stack)
        root_layout.addWidget(content_wrapper, stretch=1)

        self._page_titles = ["Home", "Device", "Equalizer"]

        return window

    # ── Page switching ────────────────────────────────────────────────────────

    def _switch_page(self, index: int):
        self._stack.setCurrentIndex(index)
        self._page_title_label.setText(self._page_titles[index])
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
