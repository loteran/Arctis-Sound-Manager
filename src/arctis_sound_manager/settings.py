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


def validate_config_setting_value(config: ConfigSetting, value: Any) -> bool:
    """True if *value* is legal for *config*, per its declared ``SettingType``.

    The domain comes from the setting's own declaration, never from
    ``type(config.default_value))``: every ``SELECT`` setting defaults to
    ``None`` (so that check used to be skipped entirely — CHA-8), and
    ``isinstance(True, int)`` is ``True`` (so a bool used to sail through an
    int-typed check — CHA-2, ``pipewire_quantum "true"`` reaching
    ``apply_force_quantum``). ``type(value) is int``/``is bool`` is used
    instead of ``isinstance`` specifically to keep bool and int apart.

    Called both at the D-Bus boundary (``SetSetting``) and when a settings
    file is read back from disk, so a hand-edited or restored YAML can't
    poison the daemon either.
    """
    if config.type is SettingType.TOGGLE:
        return type(value) is bool

    if config.type is SettingType.SELECT:
        # Free-form device identifiers (external_output_device and friends):
        # any string, or None/unset. The concrete option list is queried live
        # from PipeWire, so there is no static domain to check membership
        # against here.
        return value is None or isinstance(value, str)

    if config.type is SettingType.SLIDER:
        if type(value) is not int:
            return False
        lo = getattr(config, 'min', None)
        hi = getattr(config, 'max', None)
        if lo is not None and value < lo:
            return False
        if hi is not None and value > hi:
            return False
        return True

    if config.type is SettingType.BUTTON_GROUP:
        if type(value) is not int:
            return False
        values_mapping = getattr(config, 'values_mapping', None)
        if values_mapping:
            return value in values_mapping
        return True

    # config.type is None: config.py already rejected an unrecognised
    # `type:` string and logged a warning, hiding the setting from the UI.
    # No declared domain to check against, so refuse rather than accept an
    # unbounded value for a control nobody can even see.
    return False


def _atomic_yaml_dump(data: dict, path: Path) -> None:
    """Write *data* as YAML to *path* without ever leaving it half-written.

    Serializes to a sibling tempfile, fsyncs, then renames over the target —
    the rename is atomic on the same filesystem, so a process killed
    mid-write can only ever leave the *previous* file in place, never a
    truncated one. Shared by ``GeneralSettings`` and ``DeviceSettings``;
    ``DeviceSettings`` used to do a bare ``yaml.dump()`` here, which is what
    produced the truncated files in CHA-5.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    yaml = YAML(typ='safe')
    tmp = path.with_suffix(path.suffix + '.tmp')
    try:
        with tmp.open('w', encoding='utf-8') as fh:
            yaml.dump(data, fh)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        tmp.replace(path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _load_yaml_with_backup(path: Path, *, logger: logging.Logger) -> dict | None:
    """Load a YAML mapping from *path*, never raising.

    Returns ``None`` if the file doesn't exist, can't be parsed (e.g.
    truncated mid-write), or its top level isn't a mapping (e.g. a bare
    scalar) — callers fall back to their own defaults in that case. Either
    corrupt-shape failure renames the offending file to ``<name>.broken``
    first, so nothing a user configured is silently lost. Shared by
    ``GeneralSettings`` (where this handling already existed) and
    ``DeviceSettings`` (which used to call ``yaml.load`` unguarded and abort
    every device event forever on a truncated file — CHA-5).
    """
    if not path.exists():
        return None

    yaml = YAML(typ='safe')
    try:
        data = yaml.load(path)
    except Exception as e:
        logger.warning(
            f"{path.name} is unreadable ({e!r}); backing up and using defaults."
        )
        try:
            path.rename(path.with_suffix(path.suffix + '.broken'))
        except OSError:
            pass
        return None

    if not isinstance(data, dict):
        logger.warning(
            f"{path.name} has unexpected shape ({type(data).__name__}); "
            "backing up and using defaults."
        )
        try:
            path.rename(path.with_suffix(path.suffix + '.broken'))
        except OSError:
            pass
        return None

    return data


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
        logger = logging.getLogger(__name__)

        raw = _load_yaml_with_backup(settings_file, logger=logger)
        if raw is None:
            return

        for key in raw:
            # Clean old / invalid settings
            if key not in self.settings:
                continue

            try:
                value = int(raw[key])
            except (TypeError, ValueError):
                # A single corrupted value (e.g. a string or a list where an
                # int belongs — CHA-5) must not abort the whole load: skip
                # just this key and keep whatever the rest of the file gave
                # us, rather than leaving the device permanently
                # unconfigured because of one bad byte.
                logger.warning(
                    f"{settings_file.name}: {key}={raw[key]!r} is not a "
                    "valid integer; skipping this key."
                )
                continue

            self.settings[key] = value
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
        # Atomic write (CHA-5): a bare yaml.dump() here used to leave a
        # truncated file behind if the process died mid-write, which then
        # made every subsequent read_from_file() raise and
        # configure_virtual_sinks() abort on every device event, forever.
        _atomic_yaml_dump(self.settings.to_dict(), self._settings_file())

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

    # Which physical Arctis unit the daemon should drive when more than one is
    # plugged in (issue #199): a GameBuds dongle left permanently in a port
    # "wins" over a Nova Pro Wireless base station purely by USB enumeration
    # order, with nothing the user can see or change short of unplugging one
    # of them. None (default) keeps that exact behaviour — first match in
    # profile load order. A non-None value is one of CoreEngine's
    # replug-stable device ids (vendor:product, plus a USB bus/port suffix
    # when available — see core.py's _device_identity) and is re-resolved
    # against whatever is actually connected on every scan, so a preferred
    # unit that gets unplugged falls back to the default order instead of
    # leaving the daemon without a headset.
    preferred_device: str|None = None

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
    #
    # This is a global setting (no device profile backs it directly), but
    # mode 2 is dead weight on any family whose profile doesn't map
    # `mic_status` — the GUI must not offer a button that can never fire
    # (HW-1: nova_7_discrete_battery.yaml / nova_7p_discrete_battery.yaml
    # dropped it because the vendor spec never defines those bytes). See
    # `option_requires_status` on the ConfigSetting below: the settings
    # widget disables (not hides) any button-group option whose declared
    # status key the active profile doesn't report, so mode 3 — which still
    # does something useful via the connection half — stays enabled and
    # simply degrades, exactly as documented above.
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
    # A fourth playback channel, off unless asked for (#209). Someone using
    # Media for video wants their music somewhere else, with its own EQ — but
    # a channel nobody asked for is one more entry in every application's
    # output list, and one more filter stage running for nothing.
    aux_enabled: bool = False

    clips_enabled: bool = False

    # Arm the rolling buffer while a game is running, and let it go when the
    # game does. Off, so that starting ASM never starts a capture: a session
    # that begins with the screen already being recorded is one nobody asked
    # for, and the surprise costs more than the clip it might have caught.
    # Turning it on is one checkbox on the Clips page, and it stays on for
    # whoever wants the buffer armed without thinking about it.
    clips_autostart: bool = False

    # Where clips are written. None means "wherever the desktop says videos
    # go" — see clip_library.clip_dir(), which owns the whole resolution
    # order. Stored as a plain string rather than a Path because this file is
    # YAML and round-trips primitives; the empty string is treated as unset,
    # so clearing the field in the UI (or by hand) returns to the default
    # rather than writing clips to the current working directory (#192).
    clips_directory: str | None = None

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
        # inert_without: this toggle acts only through the companion setting
        # named here. On its own it makes CoreEngine.redirect_audio_on_disconnect
        # return without doing anything, which is indistinguishable from a
        # broken feature — the report in discussion #48 is exactly that.
        ConfigSetting('redirect_audio_on_disconnect', SettingType.TOGGLE, False, values={ 'on': True, 'off': False, 'off_label': 'off', 'on_label': 'on' }, inert_without='redirect_audio_on_disconnect_device'),
        ConfigSetting('redirect_audio_on_disconnect_device', SettingType.SELECT, None, options_source='pulse_audio_devices', options_mapping={ 'value': 'id', 'label': 'description' }),
        ConfigSetting('external_output_device', SettingType.SELECT, None, options_source='external_audio_devices', options_mapping={ 'value': 'id', 'label': 'description' }),
        ConfigSetting('preferred_device', SettingType.SELECT, None, options_source='connected_arctis_devices', options_mapping={ 'value': 'id', 'label': 'name' }),
        ConfigSetting('generic_device_mode', SettingType.TOGGLE, False, values={ 'on': True, 'off': False, 'off_label': 'off', 'on_label': 'on' }),
        ConfigSetting('generic_output_device', SettingType.SELECT, None, options_source='external_audio_devices', options_mapping={ 'value': 'id', 'label': 'description' }),
        ConfigSetting('generic_input_device', SettingType.SELECT, None, options_source='pulse_audio_sources', options_mapping={ 'value': 'id', 'label': 'description' }),
        ConfigSetting('hrir_id', SettingType.SELECT, None, options_source='hrir_files', options_mapping={ 'value': 'id', 'label': 'name' }),
        ConfigSetting('micro_input_source', SettingType.SELECT, "__auto__", options_source='pulse_audio_sources', options_mapping={ 'value': 'id', 'label': 'name' }),
        ConfigSetting('micro_alt_source', SettingType.SELECT, "", options_source='pulse_audio_sources', options_mapping={ 'value': 'id', 'label': 'name' }),
        # option_requires_status: value -> live status key(s) the active device
        # profile must report for that button to have any effect (generic
        # mechanism, read by gui/settings_widget.py — not specific to this
        # setting). Only mode 2 ("mute") is fully inert without `mic_status`;
        # mode 3 keeps working through its connection half and is left
        # unconstrained on purpose (see the comment on `micro_autoswitch`
        # above).
        ConfigSetting('micro_autoswitch', SettingType.BUTTON_GROUP, 0, values_mapping={0: 'micro_autoswitch_off', 1: 'micro_autoswitch_connection', 2: 'micro_autoswitch_mute', 3: 'micro_autoswitch_both'}, option_requires_status={2: 'mic_status'}),
        ConfigSetting('pipewire_quantum', SettingType.BUTTON_GROUP, 0, values_mapping={0: 'pipewire_quantum_auto', 1024: 'pipewire_quantum_1024', 2048: 'pipewire_quantum_2048'}),
        ConfigSetting('systray_show_battery', SettingType.TOGGLE, True, values={ 'on': True, 'off': False, 'off_label': 'off', 'on_label': 'on' }),
        ConfigSetting('systray_icon_color', SettingType.BUTTON_GROUP, 0, values_mapping={0: 'systray_icon_color_auto', 1: 'systray_icon_color_white', 2: 'systray_icon_color_black'}),
        # On by default (issue #228): a preset library that grows on its own is
        # worth saying out loud once, otherwise people wonder whether the list
        # changed or they misremembered it. Off is for those who would rather a
        # tray app never opened a window at them. Turning it off never loses the
        # information: the names are logged either way, and the dialog only ever
        # showed the first fifteen anyway.
        ConfigSetting('preset_sync_announce', SettingType.TOGGLE, True, values={ 'on': True, 'off': False, 'off_label': 'off', 'on_label': 'on' }),
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
        logger = logging.getLogger(__name__)

        data = _load_yaml_with_backup(settings_file, logger=logger)
        if data is None:
            return GeneralSettings()

        if data.get('hrir_id') in _HRIR_ID_MIGRATIONS:
            data['hrir_id'] = _HRIR_ID_MIGRATIONS[data['hrir_id']]

        # Validate every value against its ConfigSetting's declared domain
        # rather than trust a hand-edited or restored file (CHA-2 / CHA-8):
        # an out-of-range pipewire_quantum, or a non-string SELECT value,
        # would otherwise sail straight through __init__'s setattr() and
        # get re-applied by CoreEngine.start() on every daemon start. A key
        # that fails validation is dropped so the class default takes over
        # for that one field, rather than aborting the whole load.
        all_configs = {
            c.name: c for c in [*GeneralSettings.settings_config, *GeneralSettings.dac_settings_config]
        }
        for key in list(data.keys()):
            config = all_configs.get(key)
            if config is None:
                continue
            if not validate_config_setting_value(config, data[key]):
                logger.warning(
                    f"general_settings.yaml: {key}={data[key]!r} is out of "
                    "domain; using default."
                )
                del data[key]

        # hrir_id carries no ConfigSetting-level domain worth checking above
        # (SELECT only enforces "a string, or None") — its real domain is the
        # bundled catalogue, checked here directly (CHA-12: a saved id
        # escaping hrir_assets/ via "../" made an arbitrary file "the HRIR"
        # and silenced Spatial Audio).
        if 'hrir_id' in data and data['hrir_id'] is not None:
            from arctis_sound_manager.hrir_catalog import is_valid_hrir_id
            if not is_valid_hrir_id(data['hrir_id']):
                logger.warning(
                    f"general_settings.yaml: hrir_id={data['hrir_id']!r} is "
                    "not in the HRIR catalogue; using default."
                )
                del data['hrir_id']

        return GeneralSettings(**data)

    def write_to_file(self):
        settings_file = SETTINGS_FOLDER / 'general_settings.yaml'

        # Atomic write: serialize to a sibling tempfile, fsync, then rename.
        # Prevents the on-disk file from ever being half-written if the
        # process is killed mid-flush (which used to make the next start
        # fall back to defaults — now it won't).
        _atomic_yaml_dump(self.__dict__, settings_file)
