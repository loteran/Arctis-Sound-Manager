# Copyright (C) 2022 Giacomo Furlan (elegos) — original work
# Copyright (C) 2026 loteran — modifications
# SPDX-License-Identifier: GPL-3.0-or-later

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
        # Names that came from the settings file rather than a profile default.
        self._user_chosen: set[str] = set()
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
                # Remember that this one came from the user rather than from a
                # profile default, so a value read back from the headset can
                # fill in the blanks without overriding a deliberate choice.
                self._user_chosen.add(key)

    def was_chosen_by_user(self, name: str) -> bool:
        """True if *name* was loaded from the settings file, not defaulted."""
        return name in self._user_chosen

        # if raw:
        #     self.settings = ObservableDict(raw)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in ('vendor_id', 'product_id', 'settings', '_user_chosen'):
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


# Transparent migration for HRIR ids renamed away from non-ASCII characters
# (issue #132: non-ASCII filenames in the release tarball broke `bsdtar`
# extraction on some locales). Old ids persisted in a user's
# general_settings.yaml before the rename must still resolve.
_HRIR_ID_MIGRATIONS = {
    'ssc_hù': 'ssc_hu',
    'ssc_hù+': 'ssc_hu+',
}


class GeneralSettings(JsonSerializable):
    _js_exclude_fields = ['settings_config', 'dac_settings_config']

    # Make the headset the default output as soon as it comes online, so apps
    # (including everything launched in Steam Game Mode) route to the Game
    # channel instead of staying on the TV/HDMI. Guarded by is_device_online()
    # in redirect_to_media_sink(), so it never targets a dead sink when the
    # headset is off. Defaults on to restore the pre-1.1.81 behaviour that
    # unconditionally forced Arctis_Game as default; opt out via the toggle
    # (issue #135).
    redirect_audio_on_connect: bool = True

    # When disconnecting, redirect to this device
    redirect_audio_on_disconnect: bool = False
    redirect_audio_on_disconnect_device: str|None = None

    # External output device (HDMI, sound card, etc.) shown on home page
    external_output_device: str|None = None

    # Run ASM without SteelSeries hardware (#189).
    #
    # The audio half of ASM — the four channels, the Sonar EQ, HeSuVi, the
    # router, Clips — only ever manipulates PipeWire sink names. It is the USB
    # HID conversation (battery, ANC, sidetone, ChatMix, OLED) that needs an
    # Arctis, and this mode simply does without it: the generic profile
    # declares no settings and no status, so those pages come up empty rather
    # than showing controls that would do nothing.
    #
    # Off by default and never inferred. A user with an Arctis must not land
    # here because their headset was asleep during a scan.
    generic_device_mode: bool = False

    # Which sink the channels come out of in generic mode. Replaces the
    # vendor-id discovery that finds an Arctis' own ALSA nodes — there is no
    # vendor id to look for, so the user names the device instead.
    generic_output_device: str|None = None

    # Optional: the capture device the microphone EQ chain reads from. Left
    # empty, the mic chain is simply not set up; a user routing only playback
    # should not have to pick a microphone to get their channels.
    generic_input_device: str|None = None

    # HRIR profile for HeSuVi spatial audio. Defaults to a bundled profile
    # (not None) so Spatial Audio works out of the box: the HeSuVi convolver
    # references ~/.local/share/pipewire/hrir_hesuvi/hrir.wav, and if no HRIR
    # was ever materialised that file is missing, the surround node never
    # loads, and enabling Spatial silences game/media (issue #100).
    hrir_id: str | None = "atmos"

    # Which microphone source feeds the Sonar Micro EQ capture (issue #131).
    # "__auto__" (default) = Arctis microphone, matches the issue #127
    # enforcement behaviour. "__manual__" = the watchdog stops enforcing the
    # link entirely, letting a manual qpwgraph routing stick. Any other value
    # is treated as the node.name of the source to pin the capture to.
    micro_input_source: str = "__auto__"

    # Auto mic-switch (community request): flip the Sonar Micro EQ input between
    # the headset mic and an alternate (e.g. desktop) mic on a device event.
    # micro_alt_source is the alternate source's id (empty = feature inert).
    micro_alt_source: str = ""
    # Trigger: 0 = off (manual only, via micro_input_source); 1 = on headset
    # connection/power (alternate when the headset is off/out of range, headset
    # mic when it's on); 2 = on headset mic mute (alternate when muted, headset
    # mic when not); 3 = either (alternate when the headset is off OR the mic is
    # muted, headset mic only when it's on AND unmuted). Stays inert on any
    # headset that doesn't report the matching status — a headset that never
    # reports mute makes mode 3 behave like mode 1. Manual switching via
    # micro_input_source still works everywhere.
    micro_autoswitch: int = 0
    # Clips is the one feature whose dependencies are not already on a desktop:
    # PyGObject, four GStreamer plugin sets and ffmpeg, none of which the mixer
    # or the EQ need. Making them hard requirements would charge every user who
    # only wants a headset mixer for a screen recorder they never open, so the
    # feature ships off and its packages are installed from the toggle that
    # turns it on (see _CLIP_DEP_NAMES in system_deps_checker).
    #
    # False rather than "on if the packages happen to be present": a capture
    # that starts recording because a dependency arrived with something else is
    # a surprise, and this one holds a rolling buffer of the screen.
    clips_enabled: bool = False

    # Arm the rolling buffer while a game is running, and let it go when the
    # game does. On, because a buffer that has to be armed by hand is armed
    # after the moment worth keeping — which is the one thing this feature
    # exists to catch. It only ever applies once Clips itself has been switched
    # on above, so nothing here records a screen the user did not ask it to;
    # the switch is for people who would rather decide each time.
    clips_autostart: bool = True

    # Stability mode: force PipeWire's quantum (buffer size) while ASM runs.
    # 0 = leave PipeWire alone (default).
    #
    # The HeSuVi surround chain runs 14 convolvers per sink, and with a large
    # HRIR that DSP can miss PipeWire's deadline under normal desktop load —
    # heard as random crackling, with nothing in the UI to explain it. A larger
    # quantum buys the convolver more time per cycle and makes the xruns stop
    # (measured on the reporter's machine: bursts every ~10-15 s at 1024, none
    # at 2048), at the cost of proportionally more latency (#183).
    #
    # Off by default, and deliberately so: clock.force-quantum is a *global*
    # PipeWire setting, so it applies to every application on the system, not
    # just ASM's chain. That is fine for media and unwelcome for competitive
    # play, which is exactly the trade-off the user has to be the one to make.
    pipewire_quantum: int = 0

    # OLED display brightness (0–10)
    oled_brightness: int = 8

    # OLED screen timeout in seconds (0 = never)
    oled_screen_timeout: int = 30
    oled_scroll_speed: int = 2
    oled_eq_scroll_speed: int = 2

    # Whether to push custom frames to the OLED (False = leave original DAC UI)
    oled_custom_display: bool = True

    # Which elements to show on the custom OLED display
    oled_show_time: bool = True
    oled_show_battery: bool = True
    oled_show_profile: bool = True
    oled_show_eq: bool = True
    oled_show_mic_status: bool = True
    oled_show_sonar_mode: bool = True
    oled_show_eq_chat: bool = False
    oled_show_weather_city: bool = True

    # Clock format for the OLED time element: True = 24-hour, False = 12-hour (AM/PM)
    oled_time_24h: bool = True

    # Display order for orderable elements below the time/battery row
    oled_display_order: list = None  # type: ignore — set per-instance in __init__

    # Font sizes per element (pixels, 7–30)
    oled_font_time: int = 20
    oled_font_battery: int = 16
    oled_font_mic: int = 12
    oled_font_profile: int = 8
    oled_font_eq: int = 8
    oled_font_eq_chat: int = 8
    oled_font_sonar_mode: int = 8
    oled_font_weather_temp: int = 20

    # Weather module
    weather_enabled: bool = False
    weather_location: str = ""
    weather_lat: float = 0.0
    weather_lon: float = 0.0
    weather_units: str = "celsius"   # "celsius" | "fahrenheit"
    weather_city_display: str = ""   # short name returned by geocoding

    # Draw the headset battery percentage next to the system-tray icon (#119)
    systray_show_battery: bool = True

    # Systray icon color: 0 = auto (follow desktop theme), 1 = white, 2 = black (#130)
    systray_icon_color: int = 0

    # UI theme
    theme: str = "steelseries"

    settings_config: list[ConfigSetting] = [
        ConfigSetting('redirect_audio_on_connect', SettingType.TOGGLE, True, values={ 'on': True, 'off': False, 'off_label': 'off', 'on_label': 'on' }),
        ConfigSetting('redirect_audio_on_disconnect', SettingType.TOGGLE, False, values={ 'on': True, 'off': False, 'off_label': 'off', 'on_label': 'on' }),
        ConfigSetting('redirect_audio_on_disconnect_device', SettingType.SELECT, None, options_source='pulse_audio_devices', options_mapping={ 'value': 'id', 'label': 'description' }),
        ConfigSetting('external_output_device', SettingType.SELECT, None, options_source='external_audio_devices', options_mapping={ 'value': 'id', 'label': 'description' }),
        ConfigSetting('generic_device_mode', SettingType.TOGGLE, False, values={ 'on': True, 'off': False, 'off_label': 'off', 'on_label': 'on' }),
        ConfigSetting('generic_output_device', SettingType.SELECT, None, options_source='external_audio_devices', options_mapping={ 'value': 'id', 'label': 'description' }),
        ConfigSetting('generic_input_device', SettingType.SELECT, None, options_source='pulse_audio_sources', options_mapping={ 'value': 'id', 'label': 'description' }),
        ConfigSetting('hrir_id', SettingType.SELECT, None, options_source='hrir_files', options_mapping={ 'value': 'id', 'label': 'name' }),
        ConfigSetting('micro_input_source', SettingType.SELECT, "__auto__", options_source='pulse_audio_sources', options_mapping={ 'value': 'id', 'label': 'name' }),
        ConfigSetting('micro_alt_source', SettingType.SELECT, "", options_source='pulse_audio_sources', options_mapping={ 'value': 'id', 'label': 'name' }),
        ConfigSetting('micro_autoswitch', SettingType.BUTTON_GROUP, 0, values_mapping={0: 'micro_autoswitch_off', 1: 'micro_autoswitch_connection', 2: 'micro_autoswitch_mute', 3: 'micro_autoswitch_both'}),
        ConfigSetting('pipewire_quantum', SettingType.BUTTON_GROUP, 0, values_mapping={0: 'pipewire_quantum_auto', 1024: 'pipewire_quantum_1024', 2048: 'pipewire_quantum_2048'}),
        ConfigSetting('systray_show_battery', SettingType.TOGGLE, True, values={ 'on': True, 'off': False, 'off_label': 'off', 'on_label': 'on' }),
        ConfigSetting('systray_icon_color', SettingType.BUTTON_GROUP, 0, values_mapping={0: 'systray_icon_color_auto', 1: 'systray_icon_color_white', 2: 'systray_icon_color_black'}),
    ]

    dac_settings_config: list[ConfigSetting] = [
        ConfigSetting('oled_custom_display', SettingType.TOGGLE, True, values={ 'on': True, 'off': False, 'off_label': 'off', 'on_label': 'on' }),
        ConfigSetting('oled_brightness', SettingType.SLIDER, 8, min=0, max=10, step=1),
        ConfigSetting('oled_screen_timeout', SettingType.SLIDER, 30, min=0, max=300, step=10, values_mapping={'0': 'never'}),
        ConfigSetting('oled_scroll_speed', SettingType.SLIDER, 2, min=0, max=5, step=1),
        ConfigSetting('oled_eq_scroll_speed', SettingType.SLIDER, 2, min=0, max=5, step=1),
        ConfigSetting('oled_show_time', SettingType.TOGGLE, True, values={ 'on': True, 'off': False, 'off_label': 'off', 'on_label': 'on' }),
        ConfigSetting('oled_time_24h', SettingType.TOGGLE, True, values={ 'on': True, 'off': False, 'off_label': 'off', 'on_label': 'on' }),
        ConfigSetting('oled_show_battery', SettingType.TOGGLE, True, values={ 'on': True, 'off': False, 'off_label': 'off', 'on_label': 'on' }),
        ConfigSetting('oled_show_profile', SettingType.TOGGLE, True, values={ 'on': True, 'off': False, 'off_label': 'off', 'on_label': 'on' }),
        ConfigSetting('oled_show_eq', SettingType.TOGGLE, True, values={ 'on': True, 'off': False, 'off_label': 'off', 'on_label': 'on' }),
        ConfigSetting('oled_show_mic_status', SettingType.TOGGLE, True, values={ 'on': True, 'off': False, 'off_label': 'off', 'on_label': 'on' }),
        ConfigSetting('oled_show_sonar_mode', SettingType.TOGGLE, True, values={ 'on': True, 'off': False, 'off_label': 'off', 'on_label': 'on' }),
        ConfigSetting('oled_show_eq_chat', SettingType.TOGGLE, False, values={ 'on': True, 'off': False, 'off_label': 'off', 'on_label': 'on' }),
        ConfigSetting('oled_show_weather_city', SettingType.TOGGLE, True, values={ 'on': True, 'off': False, 'off_label': 'off', 'on_label': 'on' }),
    ]

    _DEFAULT_DISPLAY_ORDER = ['sonar_mode', 'profile', 'eq', 'eq_chat', 'weather']

    def __init__(self, **kwargs):
        self.oled_display_order = list(self._DEFAULT_DISPLAY_ORDER)
        for key, value in kwargs.items():
            if key in self.__class__.__annotations__:
                setattr(self, key, value)
        # Append any new default items missing from a saved order (version migration)
        for key in self._DEFAULT_DISPLAY_ORDER:
            if key not in self.oled_display_order:
                self.oled_display_order.append(key)
        # Remove obsolete keys no longer in the default order
        self.oled_display_order = [k for k in self.oled_display_order if k in self._DEFAULT_DISPLAY_ORDER]

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

        if data.get('hrir_id') in _HRIR_ID_MIGRATIONS:
            data['hrir_id'] = _HRIR_ID_MIGRATIONS[data['hrir_id']]

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
