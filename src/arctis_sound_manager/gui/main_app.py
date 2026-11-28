# Copyright (C) 2022 Giacomo Furlan (elegos) — original work
# Copyright (C) 2026 loteran — modifications
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Main application window — ArctisSonar GUI visual style.
"""
import logging

from PySide6.QtCore import QCoreApplication, QObject, QSize, Qt, QTimer, QUrl, Slot
from pathlib import Path

from PySide6.QtGui import QDesktopServices, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
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
    CLIPS_ICON,
    EQUALIZER_ICON,
    GAMEDAC_ICON,
    HDMI_ICON,
    HEADPHONE_ICON,
    HELP_ICON,
    HOME_ICON,
    SETTINGS_ICON,
    SidebarButton,
)
from arctis_sound_manager.gui.dbus_wrapper import DbusWrapper
from arctis_sound_manager.gui.dac_page import DacPage
from arctis_sound_manager.gui.device_page import DevicePage
from arctis_sound_manager.gui.headset_page import HeadsetPage
from arctis_sound_manager.gui.clips_page import ClipsPage
from arctis_sound_manager.gui.help_page import HelpPage
from arctis_sound_manager.gui.equalizer_page import EqualizerPage
from arctis_sound_manager.gui.home_page import HomePage
from arctis_sound_manager.gui.main_app_proto_widget import QMainAppProtoWidget
from arctis_sound_manager.gui.theme import (
    ACCENT,
    APP_QSS,
    BG_MAIN,
    BORDER,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    THEMES,
    build_qss,
    set_active_theme,
    retheme_widgets,
    get_theme,
    set_preview_colors,
    PREVIEW_THEME_ID,
)
from arctis_sound_manager.gui.theme_editor_page import ThemeEditorPage
from arctis_sound_manager.gui.ui_utils import get_icon_pixmap
from arctis_sound_manager.i18n import I18n
from arctis_sound_manager.settings import GeneralSettings


# ── Main application window ───────────────────────────────────────────────────

# Page indices, shared by the sidebar buttons and the stack — the two must stay
# in step. They were bare numbers scattered across the file, so inserting a page
# silently sent the theme editor and its "back" action to the wrong widgets.
PAGE_HOME = 0
PAGE_EQUALIZER = 1
PAGE_HEADSET = 2
PAGE_DAC = 3
PAGE_CLIPS = 4
PAGE_SETTINGS = 5
PAGE_HELP = 6
PAGE_THEME_EDITOR = 7

# Pause between background page builds (see QMainApp._background_step).
_BACKGROUND_STEP_GAP_MS = 50


#: Sidebar icon sizes. Help is deliberately smaller — see the note on
#: top_pages_def in _build_window.
_SIDEBAR_ICON_SIZE = 44
_SIDEBAR_HELP_ICON_SIZE = 30

#: How far the sidebar buttons may shrink when the window is short. Below
#: this the label no longer fits under the icon, so the column overlaps.
_SIDEBAR_MIN_SCALE = 0.55


class _Sidebar(QWidget):
    """The sidebar column, fitting its buttons to the window's height.

    Seven full-size buttons and the bottom block need about 990px. A shorter
    window used to make the fixed-height buttons overlap and clip the ASM
    logo; now they shrink, icon included, until the column fits.
    """

    def __init__(self):
        super().__init__()
        self.buttons: list[SidebarButton] = []
        self.bottom: QWidget | None = None

    def minimumSizeHint(self) -> QSize:
        # The fixed-height buttons would otherwise make the full column the
        # minimum, and the window could never hand the sidebar less room for
        # it to shrink into.
        return QSize(super().minimumSizeHint().width(), 0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit_buttons()

    def fit_buttons(self) -> None:
        layout = self.layout()
        if not self.buttons or self.bottom is None or layout is None:
            return
        top, bottom = layout.contentsMargins().top(), layout.contentsMargins().bottom()
        # A hidden button (DAC without a DAC) takes no room and no gap.
        shown = [b for b in self.buttons if not b.isHidden()]
        # Gaps between the buttons, the stretch and the bottom block.
        gaps = layout.spacing() * (len(shown) + 1)
        room = self.height() - top - bottom - gaps - self.bottom.sizeHint().height()
        full = sum(b.full_height() for b in shown) or 1
        factor = max(_SIDEBAR_MIN_SCALE, min(1.0, room / full))
        for b in self.buttons:
            b.set_scale(factor)



class _SystemPaletteWatch(QObject):
    """Re-applies the "follow the system" theme when the desktop palette changes."""

    def __init__(self, controller):
        super().__init__()
        self._controller = controller
        self._pending = False

    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent, QTimer
        if (event.type() == QEvent.Type.ApplicationPaletteChange and not self._pending
                and obj is QCoreApplication.instance()):
            from arctis_sound_manager.gui.theme import SYSTEM_THEME_ID, active_theme_name
            if active_theme_name() == SYSTEM_THEME_ID:
                self._pending = True
                QTimer.singleShot(0, self._reapply)
        return False

    def _reapply(self):
        from arctis_sound_manager.gui.theme import SYSTEM_THEME_ID
        try:
            self._controller._apply_theme(SYSTEM_THEME_ID, save=False)
        finally:
            # Events this re-apply itself sent (the title bar hand-back) have
            # been delivered by now; a later desktop change starts afresh.
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, self._clear)

    def _clear(self):
        self._pending = False

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

        # Load general settings (needed for theme before building window)
        self._general_settings = GeneralSettings.read_from_file()

        # Activate saved theme in the theme module so c() is correct from the start
        set_active_theme(self._general_settings.theme)
        # Apply global dark stylesheet with saved theme
        app.setStyleSheet(build_qss(self._general_settings.theme))
        # Title bar and frame in the theme too, where the desktop allows (KDE).
        from arctis_sound_manager.gui import window_frame
        window_frame.apply(app)
        # "Follow the system": a new desktop colour scheme reaches the app as
        # a palette change; the theme is rebuilt from it.
        self._system_palette_watch = _SystemPaletteWatch(self)
        app.installEventFilter(self._system_palette_watch)
        # Before any page is built: skip re-applying unchanged widget sheets.
        from arctis_sound_manager.gui.theme import install_stylesheet_dedup
        install_stylesheet_dedup()

        # Settings that arrive before the window exists are held here and
        # applied once it does (see on_settings_received, #432).
        self._window_ready = False
        self._early_settings: dict | None = None

        # D-Bus wrapper
        self.dbus_wrapper = DbusWrapper()
        self.dbus_wrapper.sig_settings.connect(self.on_settings_received)
        self.dbus_wrapper.sig_status.connect(self.on_status_received)
        self._first_shown = False   # see _on_first_show
        # Once now, so the pages built in the background (often inside the
        # tray's prebuild, window hidden) already have their real values when
        # first shown. Polling starts and stops with visibility, as before.
        self.dbus_wrapper.request_settings(one_time=True)
        self.dbus_wrapper.request_status(one_time=True)

        # Build window
        self.main_window = self._build_window()

        # Initialise theme-before-edit tracker
        self._theme_before_edit: str = "steelseries"

        # Wire D-Bus signals to the pages built so far. The others are wired
        # in _wire_page() when they are built (see _page()).
        self.dbus_wrapper.sig_status.connect(self._home_page.update_status)
        self.dbus_wrapper.sig_settings.connect(self._home_page.update_settings)

        # The one-time request above can be answered while the window is
        # still being built: a page that pumps the event loop during its
        # construction (the Clips install screen does) delivers the reply
        # early. Hand it on now that every page it touches exists.
        self._window_ready = True
        if self._early_settings is not None:
            early, self._early_settings = self._early_settings, None
            self.on_settings_received(early)
            self._home_page.update_settings(early)

        # DAC tab hidden by default until device confirms it has a DAC
        self._sidebar_buttons[PAGE_DAC].setVisible(False)

        # Start on home page
        self._switch_page(PAGE_HOME)

        # Check for updates (non-blocking background thread)
        from arctis_sound_manager.update_checker import UpdateCheckWorker
        from arctis_sound_manager.utils import project_version
        self._update_worker = UpdateCheckWorker(project_version())
        self._update_worker.result.connect(self._home_page.on_update_available)
        self._update_worker.start()

        # Check for new/updated translation files (non-blocking)
        from arctis_sound_manager.lang_updater import LangUpdateWorker
        self._lang_worker = LangUpdateWorker()
        self._lang_worker.langs_updated.connect(self._on_langs_updated)
        self._lang_worker.start()

        # Wire profile bar
        self._home_page.profile_bar.sig_apply.connect(self._on_apply_profile)
        self._home_page.profile_bar.sig_changed.connect(self._on_profiles_changed)

        self.destroyed.connect(self.sig_stop)
        self.main_window.visibilityChanged.connect(self._on_visibility_changed)

        # The other pages are built — and rendered once off-screen — in the
        # background straight after the window, one per step (see
        # _background_step). The window (Home) is up first; the Equalizer comes
        # early in the queue because it has to exist regardless: Home's output
        # buttons drive its output selectors, and its Output selector applies
        # automatic fallbacks.
        self._background_queue = [PAGE_HOME, PAGE_EQUALIZER, PAGE_HEADSET, PAGE_SETTINGS,
                                  PAGE_HELP, PAGE_CLIPS, PAGE_DAC]
        QTimer.singleShot(0, self._background_step)

    # ── Pages built on first use ──────────────────────────────────────────────
    #
    # Only Home (the start page) and Clips are built with the window. The rest
    # are built when first shown — or when something reaches for them, through
    # the properties below — then given the latest settings and status kept in
    # self.settings / self.status, and wired to the live updates from there.

    _LAZY_PAGES = {
        PAGE_EQUALIZER: EqualizerPage,
        PAGE_HEADSET: HeadsetPage,
        PAGE_DAC: DacPage,
        PAGE_SETTINGS: DevicePage,
        PAGE_HELP: HelpPage,
        PAGE_THEME_EDITOR: ThemeEditorPage,
    }

    def _built(self, index: int):
        """The page at *index* if it has been built, else None."""
        return self._built_pages.get(index)

    def _page(self, index: int):
        """The page at *index*, built (and wired, and fed) if it was not yet."""
        page = self._built_pages.get(index)
        if page is not None or index not in self._LAZY_PAGES:
            return page
        # Built inside the stack, not built and then inserted: moving a
        # finished page into the window made Qt re-style and re-lay-out its
        # whole tree (0.2-0.4 s for the Help page alone).
        page = self._LAZY_PAGES[index](self._stack)
        self._built_pages[index] = page
        placeholder = self._stack.widget(index)
        current = self._stack.currentIndex()
        self._stack.insertWidget(index, page)
        self._stack.removeWidget(placeholder)
        placeholder.deleteLater()
        self._stack.setCurrentIndex(current)
        self._wire_page(index, page)
        # What it would have received had it existed: settings first, as the
        # daemon sends them, then the status.
        if self.settings and hasattr(page, "update_settings"):
            page.update_settings(self.settings)
        if self.status and hasattr(page, "update_status"):
            page.update_status(self.status)
        return page

    def _wire_page(self, index: int, page) -> None:
        if hasattr(page, "update_settings"):
            self.dbus_wrapper.sig_settings.connect(page.update_settings)
        if hasattr(page, "update_status"):
            self.dbus_wrapper.sig_status.connect(page.update_status)

        if index == PAGE_EQUALIZER:
            self._apply_hardware_eq_settings(self.settings)
            sonar = page._sonar_page
            # A preset picked on a Channels card: show it on this page too
            self._home_page.sig_eq_preset_applied.connect(
                sonar.notify_external_preset_change)
            # The cards' output buttons drive this page's own selectors, so a
            # device picked on either page shows on both
            self._home_page.set_output_pickers({
                "game": sonar._game_output_selector,
                "chat": sonar._chat_output_selector,
                "media": sonar._media_output_selector,
                "aux": sonar._aux_output_selector,
                "output": sonar._output_selector,
            })
        elif index == PAGE_SETTINGS:
            page.sig_theme_changed.connect(self._apply_theme)
            page.sig_theme_create.connect(self._open_theme_editor_new)
            page.sig_theme_edit.connect(self._open_theme_editor_edit)
            page.sig_update_result.connect(self._home_page.on_update_available)
        elif index == PAGE_THEME_EDITOR:
            page.sig_preview.connect(self._on_theme_preview)
            page.sig_saved.connect(self._on_theme_saved)
            page.sig_cancelled.connect(self._on_theme_editor_cancelled)

    def _background_step(self) -> None:
        """Build and pre-render the next page in the queue, then yield.

        Building a page is only part of what its first visit cost: showing it
        then laid it out and painted it for the first time (0.25 s for Help).
        Rendering it once off-screen beforehand does that work here, so even
        the first visit only paints. One page per step, with a pause between,
        keeps an already open window responsive; inside the tray's background
        prebuild it is all done before the window is first opened.
        """
        if getattr(self, "_stopping", False) or not self._background_queue:
            return
        index = self._background_queue.pop(0)
        try:
            if index == PAGE_DAC and not self.settings.get('has_dac', False):
                pass                    # hidden without a DAC; built if it ever shows
            elif index in self._LAZY_PAGES:
                self._prerender(self._page(index))
            else:
                self._prerender(self._stack.widget(index))
        except Exception:  # noqa: BLE001 — a page that fails here is built on first visit
            self.logger.debug("background build of page %d failed", index, exc_info=True)
        QTimer.singleShot(_BACKGROUND_STEP_GAP_MS, self._background_step)

    def _prerender(self, page) -> None:
        """Lay out and paint *page* once, hidden, at the size it is shown at."""
        if page is None or page.isVisible():
            return              # the page on screen has done all this already
        layout = self.main_window.layout()
        if layout is not None:
            # Never shown yet (the tray's prebuild): the stack's real size is
            # only known once the window's layout has run.
            layout.activate()
        page.setGeometry(self._stack.contentsRect())
        page.grab()

    def _on_langs_updated(self, _codes) -> None:
        settings_page = self._built(PAGE_SETTINGS)
        if settings_page is not None:
            settings_page.rebuild_lang_combo()

    # Reached from elsewhere (the tray, profile and theme handling): built on
    # demand, so nothing can find one missing.
    @property
    def _equalizer_page(self):
        return self._page(PAGE_EQUALIZER)

    @property
    def _headset_page(self):
        return self._page(PAGE_HEADSET)

    @property
    def _dac_page(self):
        return self._page(PAGE_DAC)

    @property
    def _device_page(self):
        return self._page(PAGE_SETTINGS)

    @property
    def _help_page(self):
        return self._page(PAGE_HELP)

    @property
    def _theme_editor_page(self):
        return self._page(PAGE_THEME_EDITOR)

    # ── Theme ─────────────────────────────────────────────────────────────────

    def _apply_theme(self, theme_name: str, save: bool = True) -> None:
        # Update the active theme state first so c() returns the right colors
        # during all subsequent restyle calls.
        set_active_theme(theme_name)
        # Every inline stylesheet first, so nothing keeps the old theme; the
        # pages' apply_theme() below then rewrite what they compute themselves.
        retheme_widgets()
        t = get_theme(theme_name)
        self.app.setStyleSheet(build_qss(theme_name))
        for btn in self._sidebar_buttons:
            btn.update_colors(t)
        # Propagate to every page that implements apply_theme. Pages not built
        # yet need nothing: they read the active theme when they are built.
        for page in (
            self._home_page,
            *self._built_pages.values(),
            getattr(self, "_clips_page", None),
        ):
            if hasattr(page, "apply_theme"):
                page.apply_theme(t)
        # Propagate to the profile bar (lives in home_page, outside the stack layout)
        if hasattr(self._home_page, "profile_bar"):
            self._home_page.profile_bar.apply_theme(t)
        self._switch_page(self._stack.currentIndex())
        from arctis_sound_manager.gui import window_frame
        window_frame.apply(self.app)
        if save and theme_name != PREVIEW_THEME_ID:
            self._general_settings.theme = theme_name
            self._general_settings.write_to_file()

    # ── Visibility ────────────────────────────────────────────────────────────

    def _on_first_show(self) -> None:
        """What opening the window did when it was built on that first open.

        The window is now usually built at login, hidden (the tray's
        prebuild). The OLED splash and the Sonar stale-config check — whose
        fix restarts the filter-chain or PipeWire, cutting audio — belong to
        the user opening ASM, not to that background build.
        """
        self._first_shown = True
        DbusWrapper.show_splash()
        import threading
        from arctis_sound_manager.gui.sonar_page import _repair_stale_configs
        threading.Thread(target=_repair_stale_configs, name="asm-sonar-config-check",
                         daemon=True).start()

    def _on_visibility_changed(self, visible: bool):
        if visible and not self._first_shown:
            self._on_first_show()
        if visible:
            # Settings can now arrive with the window hidden (fetched when it
            # is built); a permission problem is reported when it is shown.
            self._check_permission_error(self.settings)
            self.logger.debug("App is visible — starting D-Bus polling")
            self.dbus_wrapper.request_settings()
            self.dbus_wrapper.request_status()
        else:
            self.logger.debug("App is hidden — stopping D-Bus polling")
            self.dbus_wrapper.stop()

    # ── Window construction ───────────────────────────────────────────────────

    def _build_window(self) -> QMainAppProtoWidget:
        window = QMainAppProtoWidget()
        # The pages are children of this widget, while the controller is a
        # QObject beside it — so a page that needs to ask the controller for
        # something (the Settings toggle asking the sidebar to show Clips) has
        # no parent chain to walk. One link from the window closes that gap;
        # a page reaches it with `self.window().main_app`.
        window.main_app = self
        window.setWindowFlags(Qt.WindowType.Window)
        window.setWindowTitle("Arctis Sound Manager")
        window.setWindowIcon(QIcon(get_icon_pixmap()))
        window.setMinimumSize(900, 650)

        available = window.screen().availableGeometry()
        window.resize(
            min(1400, available.width()),
            min(990, available.height()),
        )

        root_layout = QHBoxLayout(window)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # ── Sidebar ───────────────────────────────────────────────────────────
        sidebar = _Sidebar()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(150)
        sidebar_layout = QVBoxLayout(sidebar)
        # Seven pages plus the bottom block do not fit a 990px window at the
        # original 16px margins and 8px spacing: the column came out 26px
        # too tall and the ASM logo was clipped. A smaller Help icon gave
        # back 14 of those; the remaining 12 come from here rather than from
        # shrinking a second icon, because 2px between buttons and 4px at
        # the ends is not something anyone can see, and a mismatched icon is.
        sidebar_layout.setContentsMargins(15, 12, 15, 12)
        sidebar_layout.setSpacing(6)

        # Resolve current theme accent color for icon
        current_theme = THEMES.get(self._general_settings.theme, THEMES["steelseries"])
        current_accent = current_theme["ACCENT"]

        # Top navigation buttons: Channels, Equalizer, Headset, DAC, Clips,
        # Settings, Help. The fourth field is the icon size; only Help asks
        # for a smaller one. Adding Clips made a seventh full-height button,
        # and the column then ran past the bottom block — the ASM logo was
        # clipped. Help is the one that can afford it: it is the least
        # travelled of the seven and the last in the column.
        top_pages_def = [
            (HOME_ICON,      I18n.translate('ui', 'channels'),  current_accent, _SIDEBAR_ICON_SIZE),
            (EQUALIZER_ICON, I18n.translate('ui', 'equalizer'), current_accent, _SIDEBAR_ICON_SIZE),
            (HEADPHONE_ICON, I18n.translate('ui', 'devices'),   current_accent, _SIDEBAR_ICON_SIZE),
            (GAMEDAC_ICON,   I18n.translate('ui', 'dac'),       current_accent, _SIDEBAR_ICON_SIZE),
            (CLIPS_ICON,     I18n.translate('ui', 'clips'),     current_accent, _SIDEBAR_ICON_SIZE),
            (SETTINGS_ICON,  I18n.translate('ui', 'settings'),  current_accent, _SIDEBAR_ICON_SIZE),
            (HELP_ICON,      I18n.translate('ui', 'help'),      current_accent, _SIDEBAR_HELP_ICON_SIZE),
        ]

        self._sidebar_buttons: list[SidebarButton] = []
        for svg_path, label, color_active, icon_size in top_pages_def:
            btn = SidebarButton(
                svg_path=svg_path,
                label=label,
                icon_color_inactive=current_theme["TEXT_SECONDARY"],
                icon_color_active=color_active,
                parent=sidebar,
                icon_size=icon_size,
            )
            idx = len(self._sidebar_buttons)
            btn.clicked.connect(lambda checked=False, i=idx: self._switch_page(i))
            sidebar_layout.addWidget(btn, alignment=Qt.AlignmentFlag.AlignHCenter)
            self._sidebar_buttons.append(btn)

        sidebar_layout.addStretch(1)

        # Bottom block: logo + github + kofi + version, groupés serré
        _bottom = QWidget()
        _bottom.setStyleSheet("background: transparent;")
        _bottom_layout = QVBoxLayout(_bottom)
        _bottom_layout.setContentsMargins(0, 0, 0, 0)
        _bottom_layout.setSpacing(3)
        _bottom_layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        # ASM logo cliquable → repo GitHub
        _logo_path = Path(__file__).parent / 'images' / 'asm_logo.png'
        _logo_px = QPixmap(str(_logo_path))
        if not _logo_px.isNull():
            logo_lbl = QLabel()
            logo_lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            logo_lbl.setStyleSheet("background: transparent;")
            logo_lbl.setPixmap(_logo_px.scaledToWidth(55, Qt.TransformationMode.SmoothTransformation))
            logo_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
            logo_lbl.setToolTip("Arctis Sound Manager on GitHub")
            logo_lbl.mousePressEvent = lambda _: QDesktopServices.openUrl(QUrl("https://github.com/loteran/Arctis-Sound-Manager"))
            _bottom_layout.addWidget(logo_lbl, alignment=Qt.AlignmentFlag.AlignHCenter)

        # Support label
        support_lbl = QLabel("You like it ? Support me !")
        support_lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        support_lbl.setStyleSheet("color: #aaaaaa; font-size: 7pt; background: transparent;")
        _bottom_layout.addWidget(support_lbl, alignment=Qt.AlignmentFlag.AlignHCenter)

        # Ko-fi support button — bouton stylisé compact
        kofi_btn = QPushButton("☕ Ko-fi")
        kofi_btn.setObjectName("kofiBtn")
        kofi_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        kofi_btn.setToolTip("Support me on Ko-fi")
        kofi_btn.setStyleSheet("""
            QPushButton#kofiBtn {
                background-color: #FF5E5B;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 4px 10px;
                font-size: 9pt;
                font-weight: bold;
            }
            QPushButton#kofiBtn:hover { background-color: #e04f4c; }
        """)
        kofi_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://ko-fi.com/W7W31VXIVC")))
        _bottom_layout.addWidget(kofi_btn, alignment=Qt.AlignmentFlag.AlignHCenter)

        # Version label
        from arctis_sound_manager.utils import project_version
        ver_label = QLabel(f"v{project_version()}")
        ver_label.setObjectName("versionLabel")
        ver_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        _bottom_layout.addWidget(ver_label, alignment=Qt.AlignmentFlag.AlignHCenter)

        sidebar_layout.addWidget(_bottom)
        sidebar.buttons = self._sidebar_buttons
        self._sidebar = sidebar
        sidebar.bottom = _bottom

        root_layout.addWidget(sidebar)

        # ── Content area ──────────────────────────────────────────────────────
        content_wrapper = QWidget()
        content_layout = QVBoxLayout(content_wrapper)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        # Stacked pages
        self._stack = QStackedWidget()

        # Built with the stack as parent from the start (see _page()).
        self._home_page      = HomePage(self._stack)
        # The Video tab is always present. When Clips is not on, it shows the
        # install screen (what it does, which packages it needs, and a one-click
        # install) instead of the recorder.
        self._clips_page = self._build_clips_page()

        # Every sidebar index is also its stack index, so the pages built on
        # first use (_page()) hold their place with an empty widget until then.
        self._built_pages: dict = {}
        for index in range(PAGE_THEME_EDITOR + 1):
            if index == PAGE_HOME:
                self._stack.addWidget(self._home_page)
            elif index == PAGE_CLIPS:
                self._stack.addWidget(self._clips_page)
            else:
                self._stack.addWidget(QWidget())

        content_layout.addWidget(self._stack)
        root_layout.addWidget(content_wrapper, stretch=1)

        # After the stack exists, not with the sidebar buttons: this reads the
        # current page to move off Clips when the feature is off, and the
        # sidebar is built long before there is a stack to ask.
        self.apply_clips_visibility()

        return window

    # ── Page switching ────────────────────────────────────────────────────────

    def _switch_page(self, index: int):
        self._page(index)
        self._stack.setCurrentIndex(index)
        # The theme editor has no button of its own; it lights up Settings.
        active_idx = PAGE_SETTINGS if index == PAGE_THEME_EDITOR else index
        for i, btn in enumerate(self._sidebar_buttons):
            btn.set_active(i == active_idx)

    def apply_clips_visibility(self) -> None:
        """Make sure the Video tab is present, and showing the right face.

        The entry is never hidden. When Clips is off the tab *is* the
        explanation and the way to turn it on, so there is no state in which
        hiding it would be right — and hiding it was how the feature stayed
        invisible to everyone who did not already know it existed.

        The button is kept in place rather than removed for the same reason it
        always was: every sidebar index is also its stack index, so dropping an
        entry would silently renumber Settings and Help. Keeping the page costs
        nothing — ClipsPage builds no capture in its constructor and no clip
        module imports GStreamer at module level, which is what lets a machine
        without any of it still build this window.
        """
        buttons = getattr(self, "_sidebar_buttons", None) or []
        if PAGE_CLIPS < len(buttons):
            buttons[PAGE_CLIPS].setVisible(True)

        # Clips can be switched on or off from the tab itself while the window
        # is open, and the tab has to follow without a restart.
        self._sync_clips_page()

    def _build_clips_page(self):
        """The recorder when Clips is on and usable, the install screen when it
        is not, wired so either can hand over to the other."""
        from arctis_sound_manager.gui import clips_setup
        from arctis_sound_manager.gui.clips_install_page import ClipsInstallPage

        if clips_setup.clips_active():
            page = ClipsPage(self._stack)
            disabled = getattr(page, "clips_disabled", None)
            if disabled is not None:
                disabled.connect(self._sync_clips_page)
            return page

        page = ClipsInstallPage(self._stack)
        page.clips_installed.connect(self._sync_clips_page)
        return page

    def _sync_clips_page(self) -> None:
        """Put the face the tab is showing back in step with the feature.

        Called after an install, an uninstall, or the Settings toggle. Swapping
        in place is what keeps this from needing a restart; if the recorder
        cannot be built in this process — a fresh `gi` import failing mid-run
        after the packages landed — the install screen stays up and asks for a
        restart rather than taking the window down with it.
        """
        stack = getattr(self, "_stack", None)
        old = getattr(self, "_clips_page", None)
        if stack is None or old is None:
            return

        from arctis_sound_manager.gui import clips_setup
        from arctis_sound_manager.gui.clips_install_page import ClipsInstallPage

        wants_recorder = clips_setup.clips_active()
        showing_recorder = not isinstance(old, ClipsInstallPage)
        if wants_recorder == showing_recorder:
            return

        # Read before the swap: removing the old page renumbers the stack, so
        # asking afterwards cannot tell "the user was looking at Clips" from
        # "the indices moved under them".
        was_on_clips = stack.currentWidget() is old

        try:
            new_page = self._build_clips_page()
        except Exception as exc:  # noqa: BLE001
            self.logger.warning(
                "clips is on but the recorder could not be built in-process: %s", exc)
            status = getattr(old, "_status", None)
            if status is not None:
                status.setText(I18n.translate("ui", "clips_install_restart"))
            return

        # Hand back what the recorder holds outside this process before the
        # widget goes: the ScreenCast portal session and the compositor's global
        # shortcut both outlive a deleted QWidget, so switching Clips off and on
        # again used to leave the old session and keybinding behind — a second
        # capture then contends with a recorder nobody can see. deleteLater()
        # alone never reached shutdown().
        stop = getattr(old, "shutdown", None)
        if callable(stop):
            try:
                stop()
            except Exception:  # noqa: BLE001
                self.logger.debug("clips page did not shut down cleanly",
                                  exc_info=True)

        idx = stack.indexOf(old)
        if idx < 0:
            idx = PAGE_CLIPS
        stack.insertWidget(idx, new_page)
        stack.removeWidget(old)
        old.deleteLater()
        self._clips_page = new_page
        if hasattr(new_page, "apply_theme"):
            try:
                new_page.apply_theme()
            except Exception:  # noqa: BLE001
                pass
        # Turning it on from the tab lands on the recorder; turning it off
        # leaves whoever did it from Settings where they were.
        if was_on_clips or wants_recorder:
            self._switch_page(PAGE_CLIPS)

    def restart_on_new_code(self, reason: str) -> None:
        """Replace this process with one running the code now on disk.

        Reached through the tray (QSystrayApp.restart_on_new_code), from its
        staleness poll or from the package scriptlet knocking on the
        single-instance socket (`asm-gui --restart`). Restart
        whatever is open: the banner used to wait for a click, and a tray
        running yesterday's code next to daemons already on today's is
        exactly the half-upgraded state an upgrade exists to end — the
        capture and the shortcut in particular are the tray's, and stayed on
        the old code for the whole session. Release what must not be
        inherited across the exec — the capture's portal session, the
        encoder — and come back on the code now on disk, same pid, same
        tray slot.
        """
        self.logger.info("%s — restarting on the new code", reason)
        shutdown = getattr(getattr(self, "_clips_page", None), "shutdown", None)
        if shutdown is not None:
            try:
                shutdown()
            except Exception:  # noqa: BLE001
                self.logger.debug("clips page shutdown failed", exc_info=True)
        try:
            self.main_window.close()
        except Exception:  # noqa: BLE001
            pass
        from arctis_sound_manager.runtime_staleness import restart_gui
        restart_gui()

    # ── Theme editor ──────────────────────────────────────────────────────────

    def _open_theme_editor_new(self) -> None:
        self._theme_before_edit = self._general_settings.theme
        self._theme_editor_page.open_for_new(base_theme_id=self._general_settings.theme)
        self._switch_page(PAGE_THEME_EDITOR)

    def _open_theme_editor_edit(self, theme_id: str) -> None:
        self._theme_before_edit = self._general_settings.theme
        self._theme_editor_page.open_for_edit(theme_id)
        self._switch_page(PAGE_THEME_EDITOR)

    def _on_theme_preview(self, colors: dict) -> None:
        set_preview_colors(colors)
        self._apply_theme(PREVIEW_THEME_ID, save=False)

    def _on_theme_saved(self, theme_id: str) -> None:
        set_preview_colors(None)
        self._device_page.refresh_theme_combo(theme_id)
        self._apply_theme(theme_id)   # save=True → persiste
        self._switch_page(PAGE_SETTINGS)

    def _on_theme_editor_cancelled(self) -> None:
        set_preview_colors(None)
        self._apply_theme(self._theme_before_edit, save=False)
        self._switch_page(PAGE_SETTINGS)

    # ── Public API (called by systray_app) ────────────────────────────────────

    def start_sync(self):
        self.logger.info("Starting Main Window app.")
        self.main_window.show()
        self.app.exec()

    async def start(self):
        self.start_sync()

    # ── D-Bus signal handlers ─────────────────────────────────────────────────

    def on_settings_received(self, settings: dict):
        if not getattr(self, '_window_ready', True):
            # Answered during _build_window(): the sidebar and the page table
            # may not exist yet (#432). Kept for the end of __init__.
            self._early_settings = settings
            return
        if settings == self.settings:
            return
        self.settings = settings
        has_dac = settings.get('has_dac', False)
        self._sidebar_buttons[PAGE_DAC].setVisible(has_dac)
        self._sidebar.fit_buttons()
        if not has_dac and self._stack.currentIndex() == PAGE_DAC:
            self._switch_page(PAGE_HOME)

        self._apply_hardware_eq_settings(settings)
        self._check_permission_error(settings)

    def _check_permission_error(self, settings: dict) -> None:
        if not settings:
            return
        # Daemon flagged a USB EACCES on the currently-attached device. The
        # rules file might be valid (so the startup dialog at gui.py:142
        # didn't fire) but they weren't applied to this device because it
        # was plugged in before they took effect. Offer a one-click reload.
        if (settings.get('permission_error') and not getattr(self, '_perm_dialog_shown', False)
                and self.main_window.isVisible()):
            self._perm_dialog_shown = True
            from arctis_sound_manager.gui.udev_dialog import UdevRulesDialog
            from PySide6.QtWidgets import QDialog
            dlg = UdevRulesDialog(parent=self.main_window, mode='reload')
            # Kept on the instance so the branch below can dismiss it: the
            # daemon goes on retrying while this is open, so udev landing late
            # (or the user fixing it from another window) can make the dialog
            # obsolete while the user is still reading it. Asking someone to
            # act on a problem that no longer exists is its own bug.
            self._perm_dialog = dlg
            try:
                if dlg.exec() == QDialog.DialogCode.Accepted:
                    self.dbus_wrapper.reload_configs()
                    self._perm_dialog_shown = False
            finally:
                self._perm_dialog = None
        elif not settings.get('permission_error'):
            self._perm_dialog_shown = False
            dlg = getattr(self, '_perm_dialog', None)
            if dlg is not None:
                self.logger.info(
                    "USB access recovered while the permissions dialog was "
                    "open — closing it, there is nothing left to fix."
                )
                dlg.reject()

    def _apply_hardware_eq_settings(self, settings: dict) -> None:
        """Headsets with no on-device equaliser can't do anything with the
        custom band sliders; only offer the switch to those that can (#146).
        Also run when the Equalizer page is built, from the stored settings."""
        equalizer = self._built(PAGE_EQUALIZER)
        if equalizer is None or not settings:
            return
        if 'has_hardware_eq' in settings:
            equalizer.set_hardware_eq_available(bool(settings['has_hardware_eq']))
        if 'hardware_eq_range_db' in settings:
            equalizer.set_hardware_eq_range(int(settings['hardware_eq_range_db']))

    def on_status_received(self, status: dict):
        if status == self.status:
            return
        self.status = status

    @Slot(object)
    def _on_apply_profile(self, profile) -> None:
        from arctis_sound_manager.profile_manager import apply_profile
        apply_profile(profile)
        self._home_page.reload_channel_visibility()
        # Trigger EQ re-apply (single pipewire restart for all 3 channels)
        self._equalizer_page._sonar_page.apply_all_from_files()

    @Slot()
    def _on_profiles_changed(self) -> None:
        pass  # reserved for systray refresh

    @Slot()
    def sig_stop(self):
        if getattr(self, "_stopping", False):
            return
        self._stopping = True
        self.dbus_wrapper.stop()
        self.logger.debug("Received shutdown signal, shutting down.")
        # Release the clip capture before the interpreter starts tearing
        # objects down. Left to finalisation, the GStreamer pipeline and the
        # portal session died with the process — the GUI itself SEGV'd in
        # _gi on the way out, and the compositor's end of the screencast was
        # cut mid-frame, which is what took plasmashell down with it on
        # every Exit.
        shutdown = getattr(getattr(self, "_clips_page", None), "shutdown", None)
        if shutdown is not None:
            try:
                shutdown()
            except Exception:  # noqa: BLE001 — quitting must not depend on it
                self.logger.debug("clips page shutdown failed", exc_info=True)
        self.app.quit()
