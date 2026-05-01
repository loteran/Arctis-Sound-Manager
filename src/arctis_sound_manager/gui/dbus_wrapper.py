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
                                            DBUS_CONFIG_INTERFACE_NAME,
                                            DBUS_CONFIG_OBJECT_PATH,
                                            DBUS_SETTINGS_INTERFACE_NAME,
                                            DBUS_SETTINGS_OBJECT_PATH,
                                            DBUS_STATUS_INTERFACE_NAME,
                                            DBUS_STATUS_OBJECT_PATH)


class DbusWrapper(QObject):
    sig_status = Signal(object)
    sig_settings = Signal(object)
    # Emitted with True the first time a request succeeds, and with False when
    # a streak of consecutive failures suggests the daemon is no longer
    # responding. Lets the UI surface a banner instead of just stalling.
    sig_daemon_alive = Signal(bool)

    logger = logging.getLogger('DbusWrapper')
    _executor = ThreadPoolExecutor(max_workers=4)
    _CONNECT_TIMEOUT_SECONDS = 5.0
    _CALL_TIMEOUT_SECONDS = 5.0
    _DEAD_AFTER_FAILURES = 3

    def __init__(self, parent: QObject|None = None):
        super().__init__(parent)
        self._consecutive_failures = 0
        self._last_alive_state: bool | None = None

    def _record_success(self):
        self._consecutive_failures = 0
        if self._last_alive_state is not True:
            self._last_alive_state = True
            self.sig_daemon_alive.emit(True)

    def _record_failure(self):
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._DEAD_AFTER_FAILURES and self._last_alive_state is not False:
            self._last_alive_state = False
            self.sig_daemon_alive.emit(False)

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
    def show_splash() -> None:
        DbusWrapper._executor.submit(DbusWrapper.show_splash_thread)

    @staticmethod
    def show_splash_thread():
        asyncio.run(DbusWrapper.show_splash_async())

    @staticmethod
    async def show_splash_async():
        dbus_bus = None
        try:
            dbus_bus = await MessageBus().connect()
            await dbus_bus.call(Message(
                destination=DBUS_BUS_NAME,
                path=DBUS_SETTINGS_OBJECT_PATH,
                interface=DBUS_SETTINGS_INTERFACE_NAME,
                member='ShowSplash',
                message_type=MessageType.METHOD_CALL,
            ))
        except Exception as e:
            DbusWrapper.logger.debug('ShowSplash: %s', e)
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

    @staticmethod
    def reload_configs() -> None:
        """Tell the daemon to re-scan USB and re-configure virtual sinks."""
        DbusWrapper._executor.submit(DbusWrapper._reload_configs_thread)

    @staticmethod
    def _reload_configs_thread() -> None:
        asyncio.run(DbusWrapper._reload_configs_async())

    @staticmethod
    async def _reload_configs_async() -> None:
        dbus_bus = None
        try:
            dbus_bus = await MessageBus().connect()
            await dbus_bus.call(Message(
                destination=DBUS_BUS_NAME,
                path=DBUS_CONFIG_OBJECT_PATH,
                interface=DBUS_CONFIG_INTERFACE_NAME,
                member='ReloadConfigs',
                message_type=MessageType.METHOD_CALL,
            ))
        except Exception as e:
            DbusWrapper.logger.error('Error in reload_configs: %s', e)
        finally:
            if dbus_bus is not None:
                dbus_bus.disconnect()

    @staticmethod
    def set_weather_settings(enabled: bool, location: str, units: str, callback) -> None:
        """Call SetWeatherSettings D-Bus method and invoke callback(result_dict)."""
        DbusWrapper._executor.submit(
            DbusWrapper._set_weather_thread, enabled=enabled, location=location,
            units=units, callback=callback,
        )

    @staticmethod
    def _set_weather_thread(enabled: bool, location: str, units: str, callback) -> None:
        asyncio.run(DbusWrapper._set_weather_async(enabled, location, units, callback))

    @staticmethod
    async def _set_weather_async(enabled: bool, location: str, units: str, callback) -> None:
        dbus_bus = None
        try:
            dbus_bus = await MessageBus().connect()
            reply = await dbus_bus.call(Message(
                destination=DBUS_BUS_NAME,
                path=DBUS_SETTINGS_OBJECT_PATH,
                interface=DBUS_SETTINGS_INTERFACE_NAME,
                member='SetWeatherSettings',
                message_type=MessageType.METHOD_CALL,
                signature='bss',
                body=[enabled, location, units],
            ))
            result = json.loads(reply.body[0]) if reply.body else {"ok": False}
            callback(result)
        except Exception as e:
            DbusWrapper.logger.error('Error in set_weather_settings: %s', e)
            callback({"ok": False, "error": str(e)})
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
                # connect() can hang indefinitely when the session bus address
                # points to a stale socket (graphical session crashed), so cap
                # both the connect and the actual call.
                dbus_bus = await asyncio.wait_for(
                    MessageBus().connect(), timeout=self._CONNECT_TIMEOUT_SECONDS,
                )
                reply = await asyncio.wait_for(
                    dbus_bus.call(Message(
                        destination=destination,
                        path=path,
                        interface=interface,
                        member=member,
                        message_type=MessageType.METHOD_CALL
                    )),
                    timeout=self._CALL_TIMEOUT_SECONDS,
                )

                if reply is None:
                    self.logger.error('Error getting settings: no reply')
                    self._record_failure()
                elif reply.message_type == MessageType.ERROR:
                    self.logger.error('Error getting settings: %s', reply.body)
                    self._record_failure()
                else:
                    sig.emit(json.loads(reply.body[0]) or {})
                    self._record_success()
            except asyncio.TimeoutError:
                self.logger.warning(
                    f'D-Bus {member} timed out after {self._CALL_TIMEOUT_SECONDS}s — daemon unresponsive?'
                )
                self._record_failure()
            except Exception as e:
                self.logger.error('Error in dbus_request: %s', e)
                self._record_failure()
            finally:
                if dbus_bus is not None:
                    try:
                        dbus_bus.disconnect()
                    except Exception:
                        pass

            if freq == 0:
                return

            sleep(freq)

    @staticmethod
    def reload_configs() -> None:
        """Tell the daemon to re-scan USB and re-configure virtual sinks."""
        DbusWrapper._executor.submit(DbusWrapper._reload_configs_thread)

    @staticmethod
    def _reload_configs_thread() -> None:
        asyncio.run(DbusWrapper._reload_configs_async())

    @staticmethod
    async def _reload_configs_async() -> None:
        dbus_bus = None
        try:
            dbus_bus = await MessageBus().connect()
            await dbus_bus.call(Message(
                destination=DBUS_BUS_NAME,
                path=DBUS_CONFIG_OBJECT_PATH,
                interface=DBUS_CONFIG_INTERFACE_NAME,
                member='ReloadConfigs',
                message_type=MessageType.METHOD_CALL,
            ))
        except Exception as e:
            DbusWrapper.logger.error('Error in reload_configs: %s', e)
        finally:
            if dbus_bus is not None:
                dbus_bus.disconnect()
