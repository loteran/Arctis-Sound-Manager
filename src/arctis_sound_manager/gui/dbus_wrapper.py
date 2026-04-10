import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from time import sleep

from dbus_next.aio.message_bus import MessageBus
from dbus_next.constants import MessageType
from dbus_next.message import Message
from PySide6.QtCore import QObject, Signal, SignalInstance

from arctis_sound_manager.constants import (DBUS_BUS_NAME,
                                            DBUS_SETTINGS_INTERFACE_NAME,
                                            DBUS_SETTINGS_OBJECT_PATH,
                                            DBUS_STATUS_INTERFACE_NAME,
                                            DBUS_STATUS_OBJECT_PATH)


class DbusWrapper(QObject):
    sig_status = Signal(object)
    sig_settings = Signal(object)

    logger = logging.getLogger('DbusWrapper')
    _executor = ThreadPoolExecutor(max_workers=4)

    def __init__(self, parent: QObject|None = None):
        super().__init__(parent)

    def stop(self):
        self.logger.info("Stopping D-Bus wrapper...")
        self._stopping = True

    def request_status(self, one_time = False, frequency_seconds: int = 1) -> None:
        if hasattr(self, '_stopping'):
            del self._stopping
        self._executor.submit(self.request_status_thread, frequency_seconds=0 if one_time else frequency_seconds)

    def request_settings(self, one_time = False, frequency_seconds: int = 1) -> None:
        if hasattr(self, '_stopping'):
            del self._stopping
        self._executor.submit(self.request_settings_thread, frequency_seconds=0 if one_time else frequency_seconds)

    @staticmethod
    def request_list_options(list_name: str, qt_signal: SignalInstance):
        DbusWrapper._executor.submit(DbusWrapper.request_list_options_thread, list_name=list_name, qt_signal=qt_signal)

    @staticmethod
    def request_list_options_thread(list_name: str, qt_signal: SignalInstance):
        asyncio.run(DbusWrapper.request_list_options_async(list_name, qt_signal))

    @staticmethod
    async def request_list_options_async(list_name: str, qt_signal: SignalInstance):
        dbus_bus = None
        try:
            dbus_bus = await MessageBus().connect()
            reply = await dbus_bus.call(Message(
                destination=DBUS_BUS_NAME,
                path=DBUS_SETTINGS_OBJECT_PATH,
                interface=DBUS_SETTINGS_INTERFACE_NAME,
                member='GetListOptions',
                message_type=MessageType.METHOD_CALL,
                signature='s',
                body=[list_name],
            ))

            if reply is None:
                DbusWrapper.logger.error('Error getting settings: no reply')

            elif reply.message_type == MessageType.ERROR:
                DbusWrapper.logger.error('Error getting settings: %s', reply.body)

            else:
                obj = {'name': list_name, 'list': json.loads(reply.body[0]) or []}
                qt_signal.emit(obj)
        except Exception as e:
            DbusWrapper.logger.error('Error in request_list_options: %s', e)
        finally:
            if dbus_bus is not None:
                dbus_bus.disconnect()

    @staticmethod
    def send_eq_command(bands: list[int]) -> None:
        DbusWrapper._executor.submit(DbusWrapper.send_eq_command_thread, bands=bands)

    @staticmethod
    def send_eq_command_thread(bands: list[int]):
        asyncio.run(DbusWrapper.send_eq_command_async(bands))

    @staticmethod
    async def send_eq_command_async(bands: list[int]):
        dbus_bus = None
        try:
            dbus_bus = await MessageBus().connect()
            await dbus_bus.call(Message(
                destination=DBUS_BUS_NAME,
                path=DBUS_SETTINGS_OBJECT_PATH,
                interface=DBUS_SETTINGS_INTERFACE_NAME,
                member='SendEqCommand',
                message_type=MessageType.METHOD_CALL,
                signature='s',
                body=[json.dumps(bands)],
            ))
        except Exception as e:
            DbusWrapper.logger.error('Error in send_eq_command: %s', e)
        finally:
            if dbus_bus is not None:
                dbus_bus.disconnect()

    @staticmethod
    def get_eq_bands(qt_signal: SignalInstance) -> None:
        DbusWrapper._executor.submit(DbusWrapper.get_eq_bands_thread, qt_signal=qt_signal)

    @staticmethod
    def get_eq_bands_thread(qt_signal: SignalInstance):
        asyncio.run(DbusWrapper.get_eq_bands_async(qt_signal))

    @staticmethod
    async def get_eq_bands_async(qt_signal: SignalInstance):
        dbus_bus = None
        try:
            dbus_bus = await MessageBus().connect()
            reply = await dbus_bus.call(Message(
                destination=DBUS_BUS_NAME,
                path=DBUS_SETTINGS_OBJECT_PATH,
                interface=DBUS_SETTINGS_INTERFACE_NAME,
                member='GetEqBands',
                message_type=MessageType.METHOD_CALL,
            ))
            if reply and reply.message_type == MessageType.METHOD_RETURN:
                qt_signal.emit(json.loads(reply.body[0]))
        except Exception as e:
            DbusWrapper.logger.error('Error in get_eq_bands: %s', e)
        finally:
            if dbus_bus is not None:
                dbus_bus.disconnect()

    @staticmethod
    def change_setting(name: str, value: int|bool|str) -> None:
        DbusWrapper._executor.submit(DbusWrapper.change_setting_thread, name=name, value=value)

    @staticmethod
    def change_setting_thread(name: str, value: int|bool|str):
        asyncio.run(DbusWrapper.change_setting_async(name, value))

    @staticmethod
    async def change_setting_async(name: str, value: int|bool|str):
        dbus_bus = None
        try:
            dbus_bus = await MessageBus().connect()
            await dbus_bus.call(Message(
                destination=DBUS_BUS_NAME,
                path=DBUS_SETTINGS_OBJECT_PATH,
                interface=DBUS_SETTINGS_INTERFACE_NAME,
                member='SetSetting',
                message_type=MessageType.METHOD_CALL,
                signature='ss',
                body=[name, json.dumps(value)],
            ))
        except Exception as e:
            DbusWrapper.logger.error('Error in change_setting: %s', e)
        finally:
            if dbus_bus is not None:
                dbus_bus.disconnect()

    def request_status_thread(self, frequency_seconds: int):
        asyncio.run(self.dbus_request_async(
            self.sig_status,
            frequency_seconds,
            DBUS_BUS_NAME,
            DBUS_STATUS_OBJECT_PATH,
            DBUS_STATUS_INTERFACE_NAME,
            'GetStatus',
        ))

    def request_settings_thread(self, frequency_seconds: int):
        asyncio.run(self.dbus_request_async(
            self.sig_settings,
            frequency_seconds,
            DBUS_BUS_NAME,
            DBUS_SETTINGS_OBJECT_PATH,
            DBUS_SETTINGS_INTERFACE_NAME,
            'GetSettings',
        ))

    async def dbus_request_async(self, sig: SignalInstance, freq: int, destination: str, path: str, interface: str, member: str):
        while not hasattr(self, '_stopping'):
            dbus_bus = None
            try:
                dbus_bus = await MessageBus().connect()
                reply = await dbus_bus.call(Message(
                    destination=destination,
                    path=path,
                    interface=interface,
                    member=member,
                    message_type=MessageType.METHOD_CALL
                ))

                if reply is None:
                    self.logger.error('Error getting settings: no reply')

                elif reply.message_type == MessageType.ERROR:
                    self.logger.error('Error getting settings: %s', reply.body)

                else:
                    sig.emit(json.loads(reply.body[0]) or {})
            except Exception as e:
                self.logger.error('Error in dbus_request: %s', e)
            finally:
                if dbus_bus is not None:
                    dbus_bus.disconnect()

            if freq == 0:
                return

            sleep(freq)
