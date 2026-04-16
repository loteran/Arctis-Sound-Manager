from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from arctis_sound_manager.config import ConfigSetting, SettingType
from arctis_sound_manager.constants import SETTINGS_FOLDER
from arctis_sound_manager.utils import JsonSerializable, ObservableDict


class DeviceSettings(JsonSerializable):
    vendor_id: int
    product_id: int

    settings: ObservableDict[str, int]

    def __init__(self, vendor_id: int, product_id: int):
        self.vendor_id = vendor_id
        self.product_id = product_id
        self.settings = ObservableDict()
        # -1 = not yet detected; loaded/overwritten by read_from_file if a cache exists
        self.settings['dial_interface'] = -1

    def _settings_file(self) -> Path:
        settings_file = SETTINGS_FOLDER / f'{self.vendor_id:04x}_{self.product_id:04x}.yaml'

        return settings_file

    def read_from_file(self):
        settings_file = self._settings_file()

        if not settings_file.exists():
            return

        yaml = YAML(typ='safe')
        raw = yaml.load(settings_file) or {}

        for key in raw:
            # Clean old / invalid settings
            if key in self.settings:
                self.settings[key] = int(raw[key])

        # if raw:
        #     self.settings = ObservableDict(raw)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in ('vendor_id', 'product_id', 'settings'):
            super().__setattr__(name, value)

            return

        self.settings[name] = int(value)
    
    def get(self, name: str, default: int = 0) -> int:
        return self.settings.get(name, default)

    def get_dial_interface(self) -> int | None:
        """Returns the cached dial interface, or None if not yet detected."""
        value = self.settings.get('dial_interface', -1)
        return None if value == -1 else value

    def set_dial_interface(self, interface_id: int) -> None:
        """Cache the detected dial interface and persist it to disk."""
        self.settings['dial_interface'] = interface_id
        self.write_to_file()

    def write_to_file(self):
        settings_file = self._settings_file()
        settings_file.parent.mkdir(parents=True, exist_ok=True)
        
        yaml = YAML(typ='safe')
        yaml.dump(self.settings.to_dict(), settings_file)

    def to_dict(self) -> dict:
        return self.__dict__


class GeneralSettings(JsonSerializable):
    _js_exclude_fields = ['settings_config']

    # Automatically redirect on Media channel
    redirect_audio_on_connect: bool = False

    # When disconnecting, redirect to this device
    redirect_audio_on_disconnect: bool = False
    redirect_audio_on_disconnect_device: str|None = None

    # External output device (HDMI, sound card, etc.) shown on home page
    external_output_device: str|None = None

    settings_config: list[ConfigSetting] = [
        ConfigSetting('redirect_audio_on_connect', SettingType.TOGGLE, False, values={ 'on': True, 'off': False, 'off_label': 'off', 'on_label': 'on' }),
        ConfigSetting('redirect_audio_on_disconnect', SettingType.TOGGLE, False, values={ 'on': True, 'off': False, 'off_label': 'off', 'on_label': 'on' }),
        ConfigSetting('redirect_audio_on_disconnect_device', SettingType.SELECT, None, options_source='pulse_audio_devices', options_mapping={ 'value': 'id', 'label': 'description' }),
        ConfigSetting('external_output_device', SettingType.SELECT, None, options_source='external_audio_devices', options_mapping={ 'value': 'id', 'label': 'description' }),
    ]

    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            if key in self.__class__.__annotations__:
                setattr(self, key, value)

    @staticmethod
    def read_from_file() -> 'GeneralSettings':
        settings_file = SETTINGS_FOLDER / 'general_settings.yaml'

        if not settings_file.exists():
            return GeneralSettings()

        yaml = YAML(typ='safe')

        return GeneralSettings(**yaml.load(settings_file))
    
    def write_to_file(self):
        settings_file = SETTINGS_FOLDER / 'general_settings.yaml'
        settings_file.parent.mkdir(parents=True, exist_ok=True)
        
        yaml = YAML(typ='safe')
        yaml.dump(self.__dict__, settings_file)
