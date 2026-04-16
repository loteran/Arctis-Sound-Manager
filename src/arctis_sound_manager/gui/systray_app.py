import asyncio
import json
import locale
import logging
import subprocess
from logging import Logger
from pathlib import Path
from threading import Thread
from time import sleep

from dbus_next.aio.message_bus import MessageBus
from dbus_next.constants import MessageType
from dbus_next.message import Message
from PySide6.QtCore import Signal, Slot
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from arctis_sound_manager.constants import (DBUS_BUS_NAME,
                                            DBUS_STATUS_INTERFACE_NAME,
                                            DBUS_STATUS_OBJECT_PATH)
from arctis_sound_manager.gui.base_app import QBaseDesktopApp
from arctis_sound_manager.gui.main_app import QMainApp
from arctis_sound_manager.gui.ui_utils import get_icon_pixmap
from arctis_sound_manager.i18n import I18n


class QSystrayApp(QBaseDesktopApp):
    new_status = Signal(object)

    logger: Logger

    app: QApplication
    tray_icon: QSystemTrayIcon
    menu: QMenu
    dbus_bus: MessageBus

    last_device_status: dict[str, dict[str, dict[str, str|int]]]

    def __init__(self, app: QApplication, log_level: int):
        super().__init__(app)

        self.logger = logging.getLogger('SystrayApp')
        self.logger.setLevel(log_level)

        self.app = app

        pixmap = get_icon_pixmap()
        self.tray_icon = QSystemTrayIcon(QIcon(pixmap), parent=self.app)
        self.tray_icon.setToolTip('Arctis Sound Manager')

        lang_code, _ = locale.getdefaultlocale()
        lang_code = lang_code.split('_')[0] if lang_code else 'en'

        self.last_device_status = {}

        self.menu = QMenu()
        self.menu_setup()
        self.do_polling = False

        self.new_status.connect(self.on_new_status)
        self.dbus_poll_thread = Thread(target=self.poll_dbus_thread, daemon=True)
        self.dbus_poll_thread.start()

    def start_polling(self):
        # Rebuild the menu NOW, before the popup window is created by Qt.
        # This avoids calling menu.clear() while the popup Wayland surface is
        # already alive (which causes a use-after-free SIGSEGV in Qt Wayland).
        self.menu_setup()
        self.do_polling = True

    def stop_polling(self):
        self.do_polling = False

    def poll_dbus_thread(self):
        while not self.is_stopping():
            if self.do_polling:
                asyncio.run(self.dbus_poll())
                sleep(2)
            else:
                # Wait for do_polling faster
                sleep(.5)

    async def dbus_poll(self):
        self.logger.debug('Polling dbus...')

        dbus_bus = await MessageBus().connect()
        try:
            reply = await dbus_bus.call(Message(
                destination=DBUS_BUS_NAME,
                path=DBUS_STATUS_OBJECT_PATH,
                interface=DBUS_STATUS_INTERFACE_NAME,
                member='GetStatus',
                message_type=MessageType.METHOD_CALL
            ))

            if reply is None:
                self.logger.error('Error getting status: no reply')
                return

            if reply.message_type == MessageType.ERROR:
                self.logger.error('Error getting status: %s', reply.body)
                return

            self.new_status.emit(json.loads(reply.body[0]) or {})
        except Exception as e:
            self.logger.error('Error polling dbus: %s', e)
        finally:
            dbus_bus.disconnect()

    def on_new_status(self, status: dict[str, dict[str, dict[str, str|int]]]):
        if self.last_device_status == status:
            return

        self.last_device_status = status
        # Do NOT call menu_setup() here: the menu popup window may already be
        # visible (Wayland surface exists) and clear()-ing it while paint events
        # are queued causes a use-after-free SIGSEGV in QWaylandWindow.
        # menu_setup() is called in start_polling() instead, just before Qt
        # creates the popup surface.

    async def start(self):
        self.logger.info('Starting Systray app.')
        self.tray_icon.show()

        subprocess.run(
            ["systemctl", "--user", "start", "filter-chain"],
            capture_output=True,
        )

        self.dbus_bus = await MessageBus().connect()

        self.app.exec()

    def menu_setup(self) -> None:
        old_menu = self.menu
        self.menu = QMenu()
        self._menu_actions = {}

        # Open App
        self._menu_actions['open_app'] = QAction(I18n.translate('ui', 'open_app'))
        self._menu_actions['open_app'].triggered.connect(self.open_main_window)
        self.menu.addAction(self._menu_actions['open_app'])

        # Headset status (power only)
        for _, status_obj in self.last_device_status.items():
            if not status_obj:
                continue
            power = status_obj.get('headset_power_status')
            if power:
                self.menu.addSeparator()
                self._menu_actions['headset_status'] = QAction(
                    f"{I18n.translate('ui', 'headset_status')}: "
                    f"{I18n.translate('status_values', power['value'])}"
                )
                self.menu.addAction(self._menu_actions['headset_status'])

        # Profiles submenu
        from arctis_sound_manager.profile_manager import Profile, active_profile_name, apply_profile
        profiles = Profile.list_all()
        if profiles:
            self.menu.addSeparator()
            active = active_profile_name()
            for profile in profiles:
                marker = "● " if profile.name == active else "  "
                action = QAction(f"{marker}{profile.name}")
                action.triggered.connect(
                    lambda _=False, p=profile: self._on_tray_profile(p)
                )
                self.menu.addAction(action)

        self.menu.addSeparator()

        # Exit
        self._menu_actions['exit'] = QAction(I18n.translate('ui', 'exit'))
        self._menu_actions['exit'].triggered.connect(self.sig_stop)
        self.menu.addAction(self._menu_actions['exit'])

        self.menu.aboutToShow.connect(self.start_polling)
        self.menu.aboutToHide.connect(self.stop_polling)
        self.tray_icon.setContextMenu(self.menu)
        if old_menu is not None:
            old_menu.deleteLater()

    def _on_tray_profile(self, profile) -> None:
        from arctis_sound_manager.profile_manager import apply_profile
        apply_profile(profile)
        # Rebuild menu to update active marker
        self.menu_setup()
        # Note: EQ re-apply only happens when GUI is open

    def is_stopping(self):
        return hasattr(self, '_stopping') and self._stopping

    def open_main_window(self):
        if not hasattr(self, '_main_app'):
            self._main_app = QMainApp(self.app, self.logger.level)

        self._main_app.main_window.show()
        self._main_app.main_window.raise_()
        self._main_app.main_window.activateWindow()

    @Slot()
    def sig_stop(self):
        if self.is_stopping():
            return

        if hasattr(self, '_main_app'):
            self._main_app.sig_stop()

        self._stopping = True
        self.logger.debug('Received shutdown signal, shutting down.')

        # Stop all ASM services and schedule a pipewire restart
        # so the system behaves as if ASM was not installed.
        subprocess.run(
            ["systemctl", "--user", "stop",
             "arctis-manager.service", "arctis-video-router.service", "filter-chain"],
            capture_output=True, timeout=10,
        )
        # Deferred restart: the app exits first, then pipewire restarts
        # without ASM configs (filter-chain is already stopped).
        subprocess.Popen(
            ["bash", "-c",
             "sleep 1 && systemctl --user restart pipewire wireplumber pipewire-pulse"],
            start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

        self.app.quit()
