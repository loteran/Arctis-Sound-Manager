import logging
import os
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
    _js_exclude_fields = ['settings_config', 'dac_settings_config']

    # Automatically redirect on Media channel
    redirect_audio_on_connect: bool = False

    # When disconnecting, redirect to this device
    redirect_audio_on_disconnect: bool = False
    redirect_audio_on_disconnect_device: str|None = None

    # External output device (HDMI, sound card, etc.) shown on home page
    external_output_device: str|None = None

    # OLED display brightness (0–10)
    oled_brightness: int = 8

    # OLED screen timeout in seconds (0 = never)
    oled_screen_timeout: int = 30
    oled_scroll_speed: int = 2

    # Whether to push custom frames to the OLED (False = leave original DAC UI)
    oled_custom_display: bool = True

    # Which elements to show on the custom OLED display
    oled_show_time: bool = True
    oled_show_battery: bool = True
    oled_show_profile: bool = True
    oled_show_eq: bool = True

    # Display order for orderable elements below the time/battery row
    oled_display_order: list = None  # type: ignore — set per-instance in __init__

    # Font sizes per element (pixels, 7–30)
    oled_font_time: int = 20
    oled_font_battery: int = 16
    oled_font_profile: int = 8
    oled_font_eq: int = 8
    oled_font_weather_temp: int = 20

    # Weather module
    weather_enabled: bool = False
    weather_location: str = ""
    weather_lat: float = 0.0
    weather_lon: float = 0.0
    weather_units: str = "celsius"   # "celsius" | "fahrenheit"
    weather_city_display: str = ""   # short name returned by geocoding

    settings_config: list[ConfigSetting] = [
        ConfigSetting('redirect_audio_on_connect', SettingType.TOGGLE, False, values={ 'on': True, 'off': False, 'off_label': 'off', 'on_label': 'on' }),
        ConfigSetting('redirect_audio_on_disconnect', SettingType.TOGGLE, False, values={ 'on': True, 'off': False, 'off_label': 'off', 'on_label': 'on' }),
        ConfigSetting('redirect_audio_on_disconnect_device', SettingType.SELECT, None, options_source='pulse_audio_devices', options_mapping={ 'value': 'id', 'label': 'description' }),
        ConfigSetting('external_output_device', SettingType.SELECT, None, options_source='external_audio_devices', options_mapping={ 'value': 'id', 'label': 'description' }),
    ]

    dac_settings_config: list[ConfigSetting] = [
        ConfigSetting('oled_custom_display', SettingType.TOGGLE, True, values={ 'on': True, 'off': False, 'off_label': 'off', 'on_label': 'on' }),
        ConfigSetting('oled_brightness', SettingType.SLIDER, 8, min=0, max=10, step=1),
        ConfigSetting('oled_screen_timeout', SettingType.SLIDER, 30, min=0, max=300, step=10),
        ConfigSetting('oled_scroll_speed', SettingType.SLIDER, 2, min=0, max=5, step=1),
        ConfigSetting('oled_show_time', SettingType.TOGGLE, True, values={ 'on': True, 'off': False, 'off_label': 'off', 'on_label': 'on' }),
        ConfigSetting('oled_show_battery', SettingType.TOGGLE, True, values={ 'on': True, 'off': False, 'off_label': 'off', 'on_label': 'on' }),
        ConfigSetting('oled_show_profile', SettingType.TOGGLE, True, values={ 'on': True, 'off': False, 'off_label': 'off', 'on_label': 'on' }),
        ConfigSetting('oled_show_eq', SettingType.TOGGLE, True, values={ 'on': True, 'off': False, 'off_label': 'off', 'on_label': 'on' }),
    ]

    _DEFAULT_DISPLAY_ORDER = ['profile', 'eq', 'weather']

    def __init__(self, **kwargs):
        self.oled_display_order = list(self._DEFAULT_DISPLAY_ORDER)
        for key, value in kwargs.items():
            if key in self.__class__.__annotations__:
                setattr(self, key, value)

    @staticmethod
    def read_from_file() -> 'GeneralSettings':
        settings_file = SETTINGS_FOLDER / 'general_settings.yaml'

        if not settings_file.exists():
            return GeneralSettings()

        yaml = YAML(typ='safe')

        try:
            data = yaml.load(settings_file)
        except Exception as e:
            # YAML corrupt / partial write from a previous crash. Backup the
            # broken file (so the user can recover anything custom) and fall
            # back to defaults instead of crashing the daemon at startup.
            logging.getLogger(__name__).warning(
                f"general_settings.yaml is unreadable ({e!r}); backing up and using defaults."
            )
            try:
                settings_file.rename(settings_file.with_suffix('.yaml.broken'))
            except OSError:
                pass
            return GeneralSettings()

        if not isinstance(data, dict):
            logging.getLogger(__name__).warning(
                f"general_settings.yaml has unexpected shape ({type(data).__name__}); using defaults."
            )
            return GeneralSettings()

        return GeneralSettings(**data)

    def write_to_file(self):
        settings_file = SETTINGS_FOLDER / 'general_settings.yaml'
        settings_file.parent.mkdir(parents=True, exist_ok=True)

        # Atomic write: serialize to a sibling tempfile, fsync, then rename.
        # Prevents the on-disk file from ever being half-written if the
        # process is killed mid-flush (which used to make the next start
        # fall back to defaults — now it won't).
        yaml = YAML(typ='safe')
        tmp = settings_file.with_suffix('.yaml.tmp')
        try:
            with tmp.open('w', encoding='utf-8') as fh:
                yaml.dump(self.__dict__, fh)
                fh.flush()
                try:
                    os.fsync(fh.fileno())
                except OSError:
                    pass
            tmp.replace(settings_file)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
