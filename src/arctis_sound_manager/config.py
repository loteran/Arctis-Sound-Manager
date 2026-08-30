# Copyright (C) 2022 Giacomo Furlan (elegos) — original work
# Copyright (C) 2026 loteran — modifications
# SPDX-License-Identifier: GPL-3.0-or-later

import inspect
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Literal

from ruamel.yaml import YAML

from arctis_sound_manager import status_parser_fn
from arctis_sound_manager.constants import DEVICES_CONFIG_FOLDER
from arctis_sound_manager.utils import JsonSerializable


class PaddingPosition(Enum):
    START = 'start'
    END = 'end'

class SettingType(Enum):
    SLIDER = 'slider'
    TOGGLE = 'toggle'
    SELECT = 'select'
    BUTTON_GROUP = 'button_group'

class CommandTransport(Enum):
    INTERRUPT = 'interrupt'       # Interrupt OUT endpoint (default)
    CTRL_OUTPUT = 'ctrl_output'   # HID SET_REPORT, output type (wValue=0x0200)
    CTRL_FEATURE = 'ctrl_feature' # HID SET_REPORT, feature type (wValue=0x0300)

class StatusParseType(Enum):
    PERCENTAGE = 'percentage'
    ON_OFF = 'on_off'
    INT_STR_MAPPING = 'int_str_mapping'
    INT_INT_MAPPING = 'int_int_mapping'

@dataclass
class ConfigStatusParser:
    name: str
    type: StatusParseType
    init_kwargs: dict[str, Any]

class SettingsReadback:
    """A query whose reply carries the device's current value for some settings.

    `request` is the command to send, `starts_with` identifies the reply, and
    `mapping` gives the offset of each setting inside it — same indexing as a
    status response_mapping, i.e. without the report id.
    """

    def __init__(self, request: int, starts_with: int, mapping: dict[str, int]):
        self.request = int(request)
        self.starts_with = int(starts_with)
        self.mapping = {str(k): int(v) for k, v in mapping.items()}

    def values_from(self, response: list[int]) -> dict[str, int]:
        return {name: response[offset] for name, offset in self.mapping.items()
                if offset < len(response)}


class HardwareEqReadback:
    """Commands to read the on-device parametric EQ curve back (issue #146).

    `band_query` is `get_eq_preset_data`'s opcode (spec 0x32), `name_query` is
    `read_eq_preset_name`'s (spec 0xA6). Both take the same `connection_type`
    ASM's Custom EQ writes to (0x00 / wireless by default — see
    hardware_eq.CONNECTION_WIRELESS), so the readback targets exactly the
    slot the sliders wrote. Only declared for families whose spec was read
    directly and confirmed to carry these opcodes — do not add this block to
    a profile by analogy with a sibling family.
    """

    def __init__(self, band_query: int, name_query: int, connection_type: int = 0x00):
        self.band_query = int(band_query)
        self.name_query = int(name_query)
        self.connection_type = int(connection_type)


class ConfigStatusResponseMapping:
    starts_with: int

    def __init__(self, starts_with: int, **kwargs: int):
        self.starts_with = starts_with
        for key, value in kwargs.items():
            setattr(self, key, value)

    def get_status_values(self, raw_response: list[int]) -> dict[str, int]:
        response: dict[str, int] = {}
        for k, v in self.__dict__.items():
            if k == 'starts_with':
                continue
            if isinstance(v, bool):
                continue  # bool is an int subclass; a mapping never means this
            if isinstance(v, int):
                if v in range(len(raw_response)):
                    response[k] = raw_response[v]
            elif isinstance(v, dict):
                # A field derived from other offsets in the SAME frame, e.g.
                # {'max': [0x03, 0x04]}. Added for the Arctis GameBuds: two
                # earbuds each carry their own connect-status/battery byte,
                # and ASM's status_variable / online_status machinery is
                # single-key (see OnlineStatusConfig), so there is nowhere
                # else to fold "either earbud" into one reading. This mirrors
                # SteelSeries' own firmware exactly: GameBuds'
                # get-wireless-device-connection-status is
                # `(left == 3) or (right == 3)`, i.e. a max over the two
                # connect-status bytes with the "connected" value on top.
                # Runs once here, before status_parse ever sees the result,
                # so a derived key behaves like any other mapped field
                # downstream (on_off / percentage / representation).
                for op, offsets in v.items():
                    values = [raw_response[o] for o in offsets if o in range(len(raw_response))]
                    if not values:
                        continue
                    if op == 'max':
                        response[k] = max(values)
                    elif op == 'min':
                        response[k] = min(values)
                    else:
                        raise ValueError(f"Unknown response_mapping combinator: {op!r}")

        return response

class ConfigSetting(JsonSerializable):
    name: str
    type: SettingType | None
    default_value: int|str|None
    update_sequence: list[int|Literal['value']]

    _js_exclude_fields = ['name', 'update_sequence']

    def __init__(self, name: str, type: SettingType|str, default_value: int|str|None, update_sequence: list[int|Literal['value']] | None = None, **kwargs: Any):
        self.name = name
        if isinstance(type, SettingType):
            self.type = type
        else:
            try:
                self.type = SettingType(type)
            except ValueError:
                logging.getLogger(__name__).warning(
                    f"ConfigSetting '{name}': unknown type '{type}', setting will be hidden."
                )
                self.type = None  # type: ignore
        self.default_value = default_value
        self.update_sequence = update_sequence if update_sequence is not None else []

        for key, value in kwargs.items():
            setattr(self, key, value)
    
    def get_kwargs(self) -> dict[str, Any]:
        return { k: v for k, v in self.__dict__.items() if k not in ['name', 'type', 'default_value', 'update_sequence'] }

    def to_dict(self) -> dict[str, Any]:
        return { **super().to_dict(), **self.get_kwargs() }
    
    def get_update_sequence(self, value: int) -> list[int]:
        result = []
        for b in self.update_sequence:
            if isinstance(b, int):
                result.append(b)
            elif b == 'value':
                result.append(value)
            else:
                raise Exception(f"Invalid update sequence value: {b}")

        return result
    
    def __getattribute__(self, name: str) -> Any:
        return super().__getattribute__(name)

@dataclass
class ConfigPadding:
    length: int
    position: PaddingPosition
    filler: int

    def __post_init__(self):
        self.position = PaddingPosition(self.position)

@dataclass
class OledConfig:
    """Per-device OLED transport parameters parsed from the ``oled:`` YAML section.

    Devices that do not have an OLED screen leave this field ``None`` on
    ``DeviceConfiguration``.  When the section is present but individual
    keys are missing, the defaults match the original Nova Pro Wireless
    hard-coded values so older YAMLs keep identical behaviour.
    """
    interface: int = 4         # HID interface number for OLED packets
    report_id: int = 0x06      # First byte of every HID report
    wvalue: int = 0x0300       # SET_REPORT wValue (0x0300 feature / 0x0200 output)
    width: int = 128           # Screen width in pixels
    height: int = 64           # Screen height in pixels


@dataclass
class ConfigStatus:
    request: int
    response_mapping: list[ConfigStatusResponseMapping]
    representation: dict[str, list[str]]

    def __post_init__(self):
        raw_mappings: list[dict[str, int]] = self.response_mapping # pyright: ignore[reportAssignmentType]

        self.response_mapping = [ConfigStatusResponseMapping(
            starts_with=mapping.get('starts_with', 0),
            **{k: v for k, v in mapping.items() if k != 'starts_with'},
        ) for mapping in raw_mappings]

@dataclass
class OnlineStatusConfig:
    status_variable: str
    online_value: Any

class DeviceConfiguration:
    name: str
    vendor_id: int
    product_ids: list[int]
    generic: bool  # no USB device behind this profile at all (#189)
    command_interface_index: list[int]
    command_transport: CommandTransport
    # HID report id carried by control-transfer commands (ctrl_output / ctrl_feature).
    # None ⇒ unnumbered reports: SET_REPORT wValue low byte stays 0 (legacy behaviour
    # for the Nova 7 family etc.). Set it (e.g. 0x06) for devices whose commands are
    # prefixed with a real report id and whose firmware validates the wValue strictly
    # — the Nova Pro Wired GameDAC rejects a mismatched wValue. (issue #76)
    command_report_id: int | None
    listen_interface_indexes: list[int]
    dial_interface_index: int
    dial_interface_candidates: list[int]
    command_padding: ConfigPadding
    device_init: list[list[int|str]] | None
    status: ConfigStatus | None
    status_parse: dict[str, ConfigStatusParser]
    online_status: OnlineStatusConfig | None
    settings: dict[str, list[ConfigSetting]]
    oled: OledConfig | None  # None for screenless devices
    spatial_engine: str
    alsa_headroom: int | None  # None unless the device YAML declares a quirk (issue #105)
    # Leading bytes of the on-device 10-band EQ command, e.g. [0x06, 0x33].
    # None ⇒ this headset has no EQ ASM knows how to drive, and the custom EQ
    # must not be offered: the command used to be hardcoded to the Nova Pro
    # Wireless' report id and opcode and was sent to every device, where it was
    # silently ignored (#146).
    hardware_eq_command: list[int] | None
    # Name of an encoder in hardware_eq.ENCODERS for devices whose EQ takes a
    # parametric payload over several frames instead of plain gain bytes,
    # the opcodes that encoder needs, and the firmware value meaning 0 dB.
    hardware_eq_format: str | None
    hardware_eq_options: dict[str, int]
    hardware_eq_zero: int
    # Command selecting which stored preset the headset actually applies, for
    # families where writing the curve does not activate it, and the id of
    # their Custom slot. None ⇒ writing the curve is enough.
    hardware_eq_preset_select: list[int] | None
    hardware_eq_custom_preset_id: int
    hardware_eq_flat_preset_id: int
    # Opcodes to read the stored curve back from the headset, for diagnosing
    # "the sliders don't seem to do anything" reports (#146): None unless the
    # profile declares `hardware_eq.readback`, which only happens for
    # families whose spec was checked directly for these opcodes.
    hardware_eq_readback: HardwareEqReadback | None

    def __init__(self, raw_configuration: dict[str, Any]):
        raw_config: dict[str, Any] | None = raw_configuration.get('device', None)
        if raw_config is None:
            raise ValueError("Invalid configuration: missing 'device' section")

        self.name = raw_config.get('name', '')
        # Optional. Overrides the device name in the virtual channels'
        # descriptions only — the device keeps its own name everywhere else
        # (the GUI header, the D-Bus settings, the bug report). See #208.
        self.channel_label = raw_config.get('channel_label', '')
        self.vendor_id = raw_config.get('vendor_id', 0)
        self.product_ids = raw_config.get('product_ids', [])
        # True for the profile that stands in for 'no SteelSeries hardware'.
        # See the validation block below and _setup_generic_device (#189).
        self.generic = bool(raw_config.get('generic', False))
        self.command_interface_index = raw_config.get('command_interface_index', (-1, -1))
        # The HID usage page the vendor interface declares, from SteelSeries'
        # own (sync-interface <page> …). Their specifications address an
        # interface by page rather than by number, which is why every
        # command_interface_index here was written by hand — and why several
        # were wrong. Optional: without it the resolver still prefers any
        # vendor-defined page over the consumer-control one (#213).
        self.hid_usage_page = raw_config.get('hid_usage_page', None)
        # Optional {product_id: reason}. PIDs this hardware is known to
        # enumerate as while being deliberately not driven — a mode switch in
        # a non-SteelSeries position, typically. They are NOT part of
        # product_ids (nothing here can talk to them); they exist so an
        # unmatched PID can be reported as the known situation it is instead
        # of as an unsupported headset (#218).
        self.known_unsupported_product_ids: dict[int, str] = {
            int(pid): str(reason)
            for pid, reason in (raw_config.get('known_unsupported_product_ids') or {}).items()
        }
        self.command_transport = CommandTransport(raw_config.get('command_transport', 'interrupt'))
        self.command_report_id = raw_config.get('command_report_id', None)
        self.listen_interface_indexes = raw_config.get('listen_interface_indexes', [])
        self.dial_interface_candidates = raw_config.get('dial_interface_candidates', [])
        # Default dial interface = first listen interface if not specified
        self.dial_interface_index = raw_config.get('dial_interface_index', self.listen_interface_indexes[0] if self.listen_interface_indexes else 0)

        online_status = raw_config.get('online_status', None)
        self.online_status = OnlineStatusConfig(**online_status) if online_status else None

        # Commands that read a setting's current value back from the headset,
        # so a fresh install adopts what the device already has instead of
        # overwriting it with profile defaults.
        self.settings_readback = [
            SettingsReadback(**entry)
            for entry in (raw_config.get('settings_readback', None) or [])
        ]

        hardware_eq = raw_config.get('hardware_eq', None) or {}
        self.hardware_eq_command = (
            [int(b) for b in hardware_eq['command']]
            if 'command' in hardware_eq else None
        )
        # Writing the curve and *activating* it are two different commands on
        # some families. The Nova Pro Wireless and the Nova Pro Wired GameDAC
        # declare `selected_eq_preset` (0x2E) alongside `custom_eq` (0x33):
        # [0] Flat [1] Bass Boost [2] Focus [3] Smiley [4] Custom, then the game
        # presets. Writing gains fills the Custom slot — and the DAC screen
        # duly draws them — but the headset keeps applying whichever preset is
        # selected, which ASM sets to Flat at init. The sliders moved, the
        # screen followed, the sound did not.
        #
        # `preset_select` is deliberately not named `*_command`: keys ending
        # that way are forwarded as keyword arguments to the parametric
        # encoders below.
        self.hardware_eq_preset_select = (
            [int(b) for b in hardware_eq['preset_select']]
            if 'preset_select' in hardware_eq else None
        )
        self.hardware_eq_custom_preset_id = int(
            hardware_eq.get('custom_preset_id', 0x04))
        self.hardware_eq_flat_preset_id = int(
            hardware_eq.get('flat_preset_id', 0x00))
        # Families whose EQ takes a full parametric description rather than a
        # flat run of gains name an encoder from hardware_eq.py here, plus the
        # opcodes that encoder needs.
        self.hardware_eq_format = hardware_eq.get('format', None)
        self.hardware_eq_options = {
            key: int(value) for key, value in hardware_eq.items()
            if (key.endswith('_command') or key == 'frame_delay_ms')
            and value is not None
        }
        if 'bands_carry_connection' in hardware_eq:
            self.hardware_eq_options['bands_carry_connection'] = bool(
                hardware_eq['bands_carry_connection'])
        # Firmware value standing for 0 dB. GG derives the byte as
        # 2 * (zero_db_offset + gain), so this is that offset doubled: 20 for
        # the ±10 dB families, 24 for the ±12 dB ones (Nova 4). ASM's sliders
        # are always 0-40 with 20 = 0 dB, and get shifted onto the device's own
        # scale — without this a Nova 4 would sit 2 dB low across the board.
        self.hardware_eq_zero = int(hardware_eq.get('zero_at', 20))
        readback = hardware_eq.get('readback', None)
        self.hardware_eq_readback = HardwareEqReadback(**readback) if readback else None

        if not self.name:
            raise ValueError("Invalid configuration: 'device.name' must be specified and non-empty")

        # A generic profile describes no USB device at all (#189): ASM drives
        # the audio graph and never opens a HID handle. Every check below asks
        # whether a device can be found and spoken to, so none of them applies.
        # Opting out is explicit rather than inferred from empty fields — those
        # same checks exist to catch a headset profile someone left half
        # written, and that must keep failing loudly.
        if self.generic:
            if self.product_ids:
                raise ValueError(
                    "Invalid configuration: a 'device.generic' profile must not declare "
                    "product_ids — it would then be selected by the USB scan")
        else:
            if self.vendor_id == 0:
                raise ValueError("Invalid configuration: 'device.vendor_id' must be specified and non-zero")
            if not self.product_ids:
                raise ValueError("Invalid configuration: 'device.product_ids' must be a non-empty list")
            if not self.command_interface_index[0] >= 0 or not self.command_interface_index[1] >= 0:
                raise ValueError("Invalid configuration: 'device.command_interface_index' must represent [bInterfaceNumber and bAlternateSetting]")
            if not self.listen_interface_indexes:
                raise ValueError("Invalid configuration: 'device.listen_interface_indexes' must be a non-empty list")
            if any(i < 0 for i in self.listen_interface_indexes):
                raise ValueError("Invalid configuration: 'device.listen_interface_indexes' must contain only non-negative integers")

        raw_padding = raw_config.get('command_padding', {})
        if raw_padding:
            self.command_padding = ConfigPadding(**raw_padding)
        else:
            raise ValueError("Invalid configuration: 'device.command_padding' must be specified")

        raw_device_init = raw_config.get('device_init', None)
        if raw_device_init is not None:
            self.device_init = raw_device_init
        else:
            self.device_init = None

        raw_status = raw_config.get('status', {})
        if raw_status:
            self.status = ConfigStatus(
                request=raw_status.get('request', 0),
                response_mapping=raw_status.get('response_mapping', []),
                representation=raw_status.get('representation', {}),
            )
        else:
            self.status = None
        
        raw_status_parse: dict[str, dict[str, Any]] = raw_config.get('status_parse', {})
        self.status_parse = {}
        for status_name, status_raw_values in raw_status_parse.items():
            parser_type = StatusParseType(status_raw_values.get('type', ''))
            init_kwargs = dict(status_raw_values.items())
            del init_kwargs['type']

            self.status_parse[status_name] = ConfigStatusParser(
                name=status_name,
                type=parser_type,
                init_kwargs=init_kwargs,
            )

        raw_audio = raw_config.get('audio', {})
        self.spatial_engine: str = raw_audio.get('spatial_engine', 'hesuvi')

        # Optional per-device WirePlumber ALSA headroom quirk (issue #105 —
        # Nova Pro Wireless USB SYNC endpoint crackle). None for devices that
        # don't declare it (the common case): pw_quirks.apply_alsa_headroom_quirk
        # then removes any stale fragment instead of writing one.
        self.alsa_headroom: int | None = raw_config.get('alsa_headroom', None)

        raw_oled = raw_config.get('oled', None)
        if raw_oled is not None:
            self.oled: OledConfig | None = OledConfig(
                interface=raw_oled.get('interface', 4),
                report_id=raw_oled.get('report_id', 0x06),
                wvalue=raw_oled.get('wvalue', 0x0300),
                width=raw_oled.get('width', 128),
                height=raw_oled.get('height', 64),
            )
        else:
            self.oled = None

        _cfg_logger = logging.getLogger(__name__)
        raw_settings: dict[str, dict[str, Any]] = raw_config.get('settings', {})
        self.settings = {}
        for setting_section, settings in raw_settings.items():
            self.settings[setting_section] = []
            for setting_name, setting_values in settings.items():
                raw_type = setting_values.get('type', '')
                try:
                    setting_type = SettingType(raw_type)
                except ValueError:
                    _cfg_logger.warning(
                        f"Device YAML: setting '{setting_name}' has unknown type '{raw_type}', skipping."
                    )
                    continue
                setting_default_value = setting_values.get('default', None)

                self.settings[setting_section].append(ConfigSetting(
                    name=setting_name,
                    type=setting_type,
                    default_value=setting_default_value,
                    **{k: v for k, v in setting_values.items() if k not in ['default', 'type']},
                ))

def load_device_configurations() -> list[DeviceConfiguration]:
    result = []
    yaml = YAML(typ='safe')

    logger = logging.getLogger('Configuration')
    logger.info('Loading device configurations...')
    logger.info('Searching configuration files in:')
    for config_path in DEVICES_CONFIG_FOLDER:
        logger.info(f'\t- {config_path}')

    # Track which (family_name) first claimed a PID so we can warn on real
    # cross-family conflicts. Same-family duplicates across paths (HOME shadowing
    # SRC) are by-design and shouldn't warn.
    seen_pids: dict[int, tuple[str, str]] = {}  # pid -> (family_name, file.name)
    for config_path in DEVICES_CONFIG_FOLDER:
        if not config_path.exists() or not config_path.is_dir():
            continue

        for file in sorted(config_path.glob('*.yaml')):
            try:
                config_yaml = yaml.load(file)
                config = DeviceConfiguration(config_yaml)
            except Exception as e:
                # A single malformed YAML (typo in a user override under
                # ~/.config/arctis_manager/devices, partial download, etc.)
                # used to crash the whole daemon. Skip the offending file
                # and surface it loudly so the rest of the headsets still work.
                logger.error(f'Skipping invalid device YAML {file}: {e!r}')
                continue

            real_duplicates = [
                pid for pid in config.product_ids
                if pid in seen_pids and seen_pids[pid][0] != config.name
            ]
            if real_duplicates:
                first_owner = seen_pids[real_duplicates[0]]
                logger.warning(
                    f'{file.name}: PIDs {[f"0x{pid:04x}" for pid in real_duplicates]} '
                    f'already claimed by family {first_owner[0]!r} ({first_owner[1]}) — runtime selection is order-dependent.'
                )
            for pid in config.product_ids:
                seen_pids.setdefault(pid, (config.name, file.name))

            logger.info(f'Found: {config.name} (0x{config.vendor_id:04x}, {[f"0x{pid:04x}" for pid in config.product_ids]}) from {file}')

            result.append(config)

    return result

status_parsers: list[Callable[..., Any]] = []
for name, obj in inspect.getmembers(status_parser_fn, inspect.isfunction):
    if hasattr(obj, '_status_type'):
        status_parsers.append(obj)

def parsed_status(raw_status: dict[str, int]|None, device_config: DeviceConfiguration|None) -> dict[str, Any]:
    if raw_status is None or device_config is None:
        return {}

    result = {}
    for key, raw_value in raw_status.items():
        status_parse_config = next((csp for sp, csp in device_config.status_parse.items() if sp == key), None)
        if status_parse_config is None:
            result[key] = raw_value
            continue
        parser = next((p for p in status_parsers if getattr(p, '_status_type') == status_parse_config.type.value), None)
        if parser is None:
            result[key] = raw_value
            continue
        result[key] = parser(value=raw_value, **status_parse_config.init_kwargs)
    
    return result
