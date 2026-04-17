import asyncio
import itertools
import json
import logging
from pathlib import Path

from dbus_next.aio.message_bus import MessageBus
from dbus_next.service import ServiceInterface, method

from arctis_sound_manager.config import parsed_status
from arctis_sound_manager.constants import (DBUS_BUS_NAME,
                                            DBUS_CONFIG_INTERFACE_NAME,
                                            DBUS_CONFIG_OBJECT_PATH,
                                            DBUS_SETTINGS_INTERFACE_NAME,
                                            DBUS_SETTINGS_OBJECT_PATH,
                                            DBUS_STATUS_INTERFACE_NAME,
                                            DBUS_STATUS_OBJECT_PATH)
from arctis_sound_manager.core import CoreEngine
from arctis_sound_manager.pactl import TypedPulseSinkInfo
from arctis_sound_manager import device_state


class ArctisManagerDbusConfigService(ServiceInterface):
    def __init__(self, core: CoreEngine):
        super().__init__(DBUS_CONFIG_INTERFACE_NAME)
        self.core_engine = core

    @method('ReloadConfigs')
    def reload_configs(self) -> 'b': # type: ignore
        self.core_engine.reload_device_configurations()

        return True

class ArctisManagerDbusStatusService(ServiceInterface):
    def __init__(self, core: CoreEngine):
        super().__init__(DBUS_STATUS_INTERFACE_NAME)
        self.core_engine = core

    @method('GetStatus')
    def get_status(self) -> 's': # type: ignore
        status, config = self.core_engine.device_status, self.core_engine.device_config
        if not status or not config or not config.status:
            return json.dumps({})

        result = {}
        raw_status = parsed_status(self.core_engine.device_status, self.core_engine.device_config)
        for category, status_list in config.status.representation.items():
            result[category] = {}
            for status in status_list:
                if status in raw_status:
                    result[category][status] = {
                        'value': raw_status[status],
                        'type': 'label' if isinstance(raw_status[status], str) else config.status_parse[status].type.value
                    }
            if not result[category]:
                del result[category]

        return json.dumps(result)


class ArctisManagerDbusSettingsService(ServiceInterface):
    def __init__(self, core: CoreEngine):
        super().__init__(DBUS_SETTINGS_INTERFACE_NAME)
        self.core_engine = core
        self.logger = logging.getLogger('ArctisManagerDbusSettingsService')

    @method('GetSettings')
    def get_settings(self) -> 's': # type: ignore
        settings = {
            'general': self.core_engine.general_settings.to_dict(),
            'device': {},
            'settings_config': {
                config.name: config.to_dict()
                for config in self.core_engine.general_settings.settings_config
            },
        }

        if self.core_engine.device_config and self.core_engine.device_settings:
            settings.update({'device': self.core_engine.device_settings.settings})
            settings['settings_config'].update({
                config.name: config.to_dict()
                for config in list(itertools.chain.from_iterable(
                    self.core_engine.device_config.settings.values()
                ))
            })
            # Expose device identification for the GUI (headset page, telemetry)
            settings['device_name'] = device_state.get_device_name()
            settings['vendor_id']   = f"0x{self.core_engine.device_config.vendor_id:04x}"
            settings['product_id']  = (
                f"0x{self.core_engine.usb_device.idProduct:04x}"
                if self.core_engine.usb_device else ""
            )

        return json.dumps(settings)
    
    @method('SendEqCommand')
    def send_eq_command(self, bands_json: 's') -> 'b': # type: ignore
        try:
            bands = json.loads(bands_json)
            if not isinstance(bands, list) or len(bands) != 10:
                return False
            eq_file = Path.home() / '.config' / 'arctis_manager' / 'eq_bands.json'
            eq_file.parent.mkdir(parents=True, exist_ok=True)
            eq_file.write_text(json.dumps(bands))
            self.core_engine.send_eq_command(bands)
            return True
        except Exception as e:
            self.logger.error(f'SendEqCommand error: {e}')
            return False

    @method('GetEqBands')
    def get_eq_bands(self) -> 's': # type: ignore
        eq_file = Path.home() / '.config' / 'arctis_manager' / 'eq_bands.json'
        if eq_file.exists():
            return eq_file.read_text()
        return json.dumps([20] * 10)

    @method('SetSetting')
    def set_setting(self, setting: 's', value: 's') -> 'b': # type: ignore
        try:
            value = json.loads(value)
        except json.JSONDecodeError as e:
            self.logger.error(f'SetSetting: error while parsing JSON value ({value}): {e}')

            return False

        general_settings_keys = self.core_engine.general_settings.to_dict().keys()
        if setting in general_settings_keys:
            config = next((config for config in self.core_engine.general_settings.settings_config if config.name == setting), None)
            if not config:
                self.logger.error(f'Unknown general setting configuration: {setting}')
                return False
            
            if config.default_value is not None and not isinstance(value, type(config.default_value)):
                self.logger.error(f'Value type mismatch: {type(config.default_value)} != {type(value)}')
                return False

            setattr(self.core_engine.general_settings, setting, value)
            self.core_engine.general_settings.write_to_file()

            return True
        
        if self.core_engine.device_config and self.core_engine.device_settings:
            device_settings_keys = self.core_engine.device_settings.settings.keys()
            if setting in device_settings_keys:
                config = next((config for section in self.core_engine.device_config.settings.keys() for config in self.core_engine.device_config.settings[section] if config.name == setting), None)
                if not config:
                    self.logger.error(f'Unknown device setting configuration: {setting}')
                    return False
                
                if not isinstance(value, type(config.default_value)):
                    self.logger.error(f'Value type mismatch: {type(config.default_value)} != {type(value)}')
                    return False

                self.core_engine.device_settings.settings[setting] = value
                self.core_engine.device_settings.write_to_file()

                return True

        return False
    
    @method('GetListOptions')
    def get_list_options(self, list_name: 's') -> 's': # type: ignore
        result = []
        if list_name in ('pulse_audio_devices', 'external_audio_devices'):
            sinks: list[TypedPulseSinkInfo] = self.core_engine.pa_audio_manager.pulse.sink_list()
            for sink in sinks:
                node_name = sink.proplist.get('node.name', '')
                # For external_audio_devices, only show physical non-SteelSeries sinks
                if list_name == 'external_audio_devices':
                    if not node_name.startswith('alsa_output'):
                        continue
                    if sink.proplist.get('device.vendor.id', '') == '0x1038':
                        continue

                id = sink.proplist.get('node.nick', '')
                name = sink.proplist.get('node.description', sink.proplist.get('node.nick', ''))

                if id and name:
                    result.append({ 'id': id, 'name': name })

        return json.dumps(result)

class DbusManager:
    _instance: 'DbusManager|None' = None

    @staticmethod
    def getInstance() -> 'DbusManager':
        if DbusManager._instance is None:
            DbusManager._instance = DbusManager()

        return DbusManager._instance

    def __init__(self):
        self.log = logging.getLogger('DbusManager')
    
    def setup_sinks(self):
        pass
    
    async def start(self, core_engine: CoreEngine):
        self.log.info("Initializing service...")

        self.core_engine = core_engine

        bus = await MessageBus().connect()
        for tpl in [
            (ArctisManagerDbusConfigService, DBUS_CONFIG_OBJECT_PATH),
            (ArctisManagerDbusSettingsService, DBUS_SETTINGS_OBJECT_PATH),
            (ArctisManagerDbusStatusService, DBUS_STATUS_OBJECT_PATH)
        ]:
            interface = tpl[0](self.core_engine)
            bus.export(tpl[1], interface)

        await bus.request_name(DBUS_BUS_NAME)

    async def wait_for_stop(self) -> None:
        while not getattr(self, '_stopping', False):
            await asyncio.sleep(1)
        
        self.core_engine.stop()
        self.core_engine.teardown()

    def stop(self):
        self.log.info("Stopping D-Bus service...")
        self._stopping = True
