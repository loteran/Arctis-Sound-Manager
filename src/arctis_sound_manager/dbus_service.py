import asyncio
import itertools
import json
import logging
from pathlib import Path

from dbus_next.aio.message_bus import MessageBus
from dbus_next.constants import RequestNameReply
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
        gs = self.core_engine.general_settings
        settings = {
            'general': gs.to_dict(),
            # True when kernel_detach hit EACCES on the current device — the
            # GUI uses this to surface UdevRulesDialog(mode="reload").
            'permission_error': bool(getattr(self.core_engine, 'permission_error', False)),
            'device': {},
            'dac': {k: getattr(gs, k) for k in (
                'oled_brightness', 'oled_screen_timeout', 'oled_scroll_speed', 'oled_custom_display',
                'oled_show_time', 'oled_show_battery', 'oled_show_profile',
                'oled_show_eq', 'oled_display_order',
                'oled_font_time', 'oled_font_battery', 'oled_font_profile',
                'oled_font_eq', 'oled_font_weather_temp',
                'weather_enabled', 'weather_location', 'weather_units', 'weather_city_display',
            )},
            'settings_config': {
                config.name: config.to_dict()
                for config in gs.settings_config
            },
            'dac_settings_config': {
                config.name: config.to_dict()
                for config in gs.dac_settings_config
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
            settings['has_dac'] = (
                self.core_engine.device_config.status is not None
                and 'gamedac' in self.core_engine.device_config.status.representation
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

        # Special case: font size settings (int, no ConfigSetting entry)
        _FONT_SIZE_KEYS = {
            'oled_font_time', 'oled_font_battery', 'oled_font_profile',
            'oled_font_eq', 'oled_font_weather_temp',
        }
        if setting in _FONT_SIZE_KEYS:
            if not isinstance(value, int) or not (7 <= value <= 30):
                return False
            setattr(self.core_engine.general_settings, setting, value)
            self.core_engine.general_settings.write_to_file()
            return True

        # Special case: list settings not covered by ConfigSetting
        if setting == 'oled_display_order':
            gs = self.core_engine.general_settings
            if not isinstance(value, list):
                return False
            gs.oled_display_order = value
            gs.write_to_file()
            return True

        general_settings_keys = self.core_engine.general_settings.to_dict().keys()
        if setting in general_settings_keys:
            gs = self.core_engine.general_settings
            config = next(
                (c for c in [*gs.settings_config, *gs.dac_settings_config] if c.name == setting),
                None,
            )
            if not config:
                self.logger.error(f'Unknown general setting configuration: {setting}')
                return False
            
            if config.default_value is not None and not isinstance(value, type(config.default_value)):
                self.logger.error(f'Value type mismatch: {type(config.default_value)} != {type(value)}')
                return False

            setattr(self.core_engine.general_settings, setting, value)
            self.core_engine.general_settings.write_to_file()

            if setting == 'oled_brightness' and self.core_engine.oled_manager is not None:
                self.core_engine.oled_manager.set_brightness(int(value))
            if setting == 'oled_custom_display' and self.core_engine.oled_manager is not None:
                self.core_engine.oled_manager.set_custom_display(bool(value))

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

    @method('ShowSplash')
    def show_splash(self) -> 'b': # type: ignore
        if self.core_engine.oled_manager is not None:
            self.core_engine.oled_manager._show_splash()
            return True
        return False

    @method('SetWeatherSettings')
    def set_weather_settings(self, enabled: 'b', location: 's', units: 's') -> 's': # type: ignore
        """Geocode *location* (if changed), persist all weather settings, return JSON result.

        Returns JSON: {"ok": true, "city": "Paris", "lat": 48.85, "lon": 2.35}
                  or  {"ok": false, "error": "City not found"}
        """
        from arctis_sound_manager.weather_service import WeatherService
        gs = self.core_engine.general_settings

        result: dict = {"ok": True, "city": gs.weather_city_display}

        # Geocode only when location string actually changed
        if location and location != gs.weather_location:
            svc = WeatherService()
            geo = svc.geocode(location)
            if geo is None:
                return json.dumps({"ok": False, "error": "City not found"})
            gs.weather_lat = geo.lat
            gs.weather_lon = geo.lon
            gs.weather_city_display = geo.display_name
            result["city"] = geo.display_name
            result["lat"] = geo.lat
            result["lon"] = geo.lon
            if self.core_engine.oled_manager:
                self.core_engine.oled_manager.invalidate_weather_cache()

        gs.weather_enabled = enabled
        gs.weather_location = location
        gs.weather_units = units
        gs.write_to_file()

        if self.core_engine.oled_manager:
            self.core_engine.oled_manager.invalidate_weather_cache()

        return json.dumps(result)

    @method('GetListOptions')
    def get_list_options(self, list_name: 's') -> 's': # type: ignore
        result = []
        if list_name in ('pulse_audio_devices', 'external_audio_devices'):
            sinks: list[TypedPulseSinkInfo] = self.core_engine.pa_audio_manager.sink_list_wrapper()
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

        # ── Bus connection ────────────────────────────────────────────────
        # The session bus may not be reachable yet at boot (running before
        # the user session is fully up) or at all (TTY-only / container).
        # Retry with a short backoff so transient races resolve themselves;
        # raise a clear error if it never comes up so systemd can decide
        # whether to restart us.
        bus = None
        last_err: Exception | None = None
        for attempt in range(1, 6):
            try:
                bus = await asyncio.wait_for(MessageBus().connect(), timeout=5.0)
                break
            except asyncio.TimeoutError:
                last_err = TimeoutError("MessageBus.connect() timed out after 5s")
            except Exception as e:
                last_err = e
            self.log.warning(
                f"D-Bus connect attempt {attempt}/5 failed: {last_err!r} — retrying in {attempt}s..."
            )
            await asyncio.sleep(attempt)
        if bus is None:
            raise RuntimeError(
                f"Could not connect to the D-Bus session bus after 5 attempts: {last_err!r}. "
                "Is DBUS_SESSION_BUS_ADDRESS set? On a TTY-only host the daemon "
                "needs `dbus-run-session` or a real graphical session."
            )

        for tpl in [
            (ArctisManagerDbusConfigService, DBUS_CONFIG_OBJECT_PATH),
            (ArctisManagerDbusSettingsService, DBUS_SETTINGS_OBJECT_PATH),
            (ArctisManagerDbusStatusService, DBUS_STATUS_OBJECT_PATH)
        ]:
            interface = tpl[0](self.core_engine)
            bus.export(tpl[1], interface)

        # ── Bus name acquisition ──────────────────────────────────────────
        # An old daemon left running (e.g. via systemd + manual launch) will
        # still own the well-known name. Detect EXISTS / IN_QUEUE explicitly
        # instead of silently registering as a queued listener that never
        # answers any GUI request.
        try:
            reply = await bus.request_name(DBUS_BUS_NAME)
        except Exception as e:
            raise RuntimeError(
                f"Could not request D-Bus name {DBUS_BUS_NAME!r}: {e!r}"
            ) from e

        if reply not in (RequestNameReply.PRIMARY_OWNER, RequestNameReply.ALREADY_OWNER):
            raise RuntimeError(
                f"D-Bus name {DBUS_BUS_NAME!r} is already taken (reply={reply.name}). "
                "Another asm-daemon is probably running — stop it with "
                "`systemctl --user stop arctis-manager.service` or `pkill -f asm-daemon` "
                "and retry."
            )
        self.log.info(f"D-Bus name {DBUS_BUS_NAME!r} acquired ({reply.name}).")

    async def wait_for_stop(self) -> None:
        while not getattr(self, '_stopping', False):
            await asyncio.sleep(1)
        
        self.core_engine.stop()
        self.core_engine.teardown()

    def stop(self):
        self.log.info("Stopping D-Bus service...")
        self._stopping = True
