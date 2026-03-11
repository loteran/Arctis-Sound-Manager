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

from linux_arctis_manager.constants import (DBUS_BUS_NAME,
                                            DBUS_STATUS_INTERFACE_NAME,
                                            DBUS_STATUS_OBJECT_PATH)
from linux_arctis_manager.gui.base_app import QBaseDesktopApp
from linux_arctis_manager.gui.main_app import QMainApp
from linux_arctis_manager.gui.ui_utils import get_icon_pixmap
from linux_arctis_manager.i18n import I18n


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
        self.menu.aboutToShow.connect(self.start_polling)
        self.menu.aboutToHide.connect(self.stop_polling)
        
        self.new_status.connect(self.on_new_status)
        self.dbus_poll_thread = Thread(target=self.poll_dbus_thread, daemon=True)
        self.dbus_poll_thread.start()

        self.tray_icon.setContextMenu(self.menu)
    
    def start_polling(self):
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
    
    def on_new_status(self, status: dict[str, dict[str, dict[str, str|int]]]):
        if self.last_device_status == status:
            return

        self.last_device_status = status
        self.menu_setup()

    async def start(self):
        self.logger.info('Starting Systray app.')
        self.tray_icon.show()

        self.dbus_bus = await MessageBus().connect()

        self.app.exec()

    def menu_setup(self) -> None:
        self.menu.clear()
        self._menu_actions = {}

        self._menu_actions['open_app'] = QAction(I18n.translate('ui', 'open_app'))
        self._menu_actions['open_app'].triggered.connect(self.open_main_window)
        self.menu.addAction(self._menu_actions['open_app'])

        sections = 0
        for _, status_obj in self.last_device_status.items():
            if not status_obj:
                continue

            self.menu.addSeparator()
            sections += 1

            for status, status_o in status_obj.items():
                self._menu_actions['status_' + status] = QAction(
                    f"{I18n.translate('status', status)}: "
                    f"{I18n.translate('status_values', status_o['value'])}"
                    f"{'%' if status_o['type'] == 'percentage' else ''}"
                )
                self.menu.addAction(self._menu_actions['status_' + status])

        if sections:
            self.menu.addSeparator()

        self._menu_actions['toggle_sonar'] = QAction(self._sonar_toggle_label())
        self._menu_actions['toggle_sonar'].triggered.connect(self.toggle_sonar)
        self.menu.addAction(self._menu_actions['toggle_sonar'])

        self.menu.addSeparator()

        self._menu_actions['exit'] = QAction(I18n.translate('ui', 'exit'))
        self._menu_actions['exit'].triggered.connect(self.sig_stop)
        self.menu.addAction(self._menu_actions['exit'])
    
    def _sonar_state_file(self) -> Path:
        return Path.home() / '.config' / 'arctis_manager' / '.eq_mode'

    def _sonar_toggle_label(self) -> str:
        state_file = self._sonar_state_file()
        current = state_file.read_text().strip() if state_file.exists() else 'custom'
        if current == 'sonar':
            return 'EQ : Sonar → Custom'
        return 'EQ : Custom → Sonar'

    def toggle_sonar(self):
        script = Path.home() / '.config' / 'arctis_manager' / 'toggle_sonar.py'
        subprocess.Popen(['python3', str(script)])

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
        self.app.quit()
