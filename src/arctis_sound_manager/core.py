import asyncio
import json
import logging
import threading
from pathlib import Path
from typing import Any, Coroutine, Literal, cast

import usb
from usb.core import Device

from arctis_sound_manager import device_state
from arctis_sound_manager.config import (CommandTransport,
                                         DeviceConfiguration,
                                         load_device_configurations,
                                         parsed_status)
from arctis_sound_manager.constants import (PULSE_CHAT_NODE_NAME,
                                            PULSE_MEDIA_NODE_NAME)
from arctis_sound_manager.pactl import ONLY_PHYSICAL, PulseAudioManager
from arctis_sound_manager.settings import DeviceSettings, GeneralSettings
from arctis_sound_manager.usb_devices_monitor import USBDevicesMonitor
from arctis_sound_manager.utils import ObservableDict
from arctis_sound_manager.oled_manager import OledManager


class TypedDevice(Device):
    idVendor: int
    idProduct: int


class CoreEngine:
    logger: logging.Logger
    device_configurations: list[DeviceConfiguration]
    pa_audio_manager: PulseAudioManager
    usb_devices_monitor: USBDevicesMonitor

    device_config: DeviceConfiguration | None = None
    usb_device: TypedDevice | None = None
    general_settings: GeneralSettings
    device_settings: DeviceSettings

    device_status: ObservableDict[str, int]|None = None
    oled_manager: 'OledManager | None' = None

    media_mix: int
    chat_mix: int
    _active_extra_dial_interfaces: list[int]

    def __init__(self) -> None:
        self.media_mix = 100
        self.chat_mix = 100
        self._active_extra_dial_interfaces = []
        self._device_lock = threading.RLock()
        self._usb_write_lock = threading.Lock()

        self.general_settings = GeneralSettings.read_from_file()

        self.logger = logging.getLogger('CoreEngine')
        self.pa_audio_manager = PulseAudioManager.get_instance()
        self.usb_devices_monitor = USBDevicesMonitor.get_instance()

        self.reload_device_configurations()
        self.usb_devices_monitor.register_on_connect(self.on_device_connected)
        self.usb_devices_monitor.register_on_disconnect(self.on_device_disconnected)
    
    def new_device_status(self) -> ObservableDict:
        device_status = ObservableDict()
        device_status.add_observer(self.on_device_status_changed)

        return device_status
    
    def start(self) -> Coroutine:
        self._stopping = False
        self.usb_devices_monitor.start()

        return self.loop()
    
    def stop(self):
        self.logger.info("Stopping CoreEngine...")
        self._stopping = True
        self.usb_devices_monitor.stop()

    def manage_mix_change(self):
        if not self.device_status or not self.device_config:
            return

        new_media_mix = self.device_status.get('media_mix', None)
        new_chat_mix = self.device_status.get('chat_mix', None)

        if new_media_mix is None or new_chat_mix is None:
            return
        
        new_media_mix = parsed_status({'media_mix': new_media_mix}, self.device_config).get('media_mix', self.media_mix)
        new_chat_mix = parsed_status({'chat_mix': new_chat_mix}, self.device_config).get('chat_mix', self.chat_mix)

        if new_media_mix != self.media_mix or new_chat_mix != self.chat_mix:
            self.media_mix = new_media_mix
            self.chat_mix = new_chat_mix
            self.pa_audio_manager.set_mix(self.media_mix, self.chat_mix)
    
    async def listen_endpoint_loop(self, interface_id: int):
        with self._device_lock:
            if self.usb_device is None:
                return
            usb_device = self.usb_device

        endpoint, max_packet_size = self.guess_interface_endpoint('in', interface_id)

        if not endpoint:
            self.logger.warning(f'Failed to find listen interface endpoint for device: {usb_device.idProduct:04x}:{usb_device.idVendor:04x}')
            return

        try:
            read_input: list[int] = list(await asyncio.to_thread(usb_device.read, endpoint, max_packet_size, 200))
            with self._device_lock:
                if self.device_config is None:
                    return

            if self.device_config.status is not None:
                self.logger.debug(f'Response: {read_input}')
                if read_input and read_input[0] == 0x07:
                    self.logger.debug(f'EVENT: {[hex(b) for b in read_input[:8]]}')

                for mapping in self.device_config.status.response_mapping:
                    starts_with = f'{mapping.starts_with:02x}'
                    if len(starts_with) % 2 != 0:
                        starts_with = f'0{starts_with}'
                    read_hex_str = ''.join(f'{byte:02x}' for byte in read_input)

                    if read_hex_str.startswith(starts_with):
                        device_status = mapping.get_status_values(read_input)
                        if self.device_status is None:
                            self.device_status = self.new_device_status()
                        self.device_status.update(device_status)

                        # If this packet arrived on an extra dial candidate interface, cache it
                        if interface_id not in self.device_config.listen_interface_indexes:
                            cached = self.device_settings.get_dial_interface()
                            if cached != interface_id:
                                self.logger.info(f"Dial interface detected on interface {interface_id}, caching")
                                self.device_settings.set_dial_interface(interface_id)
                                self._active_extra_dial_interfaces = [interface_id]

                self.manage_mix_change()

            await asyncio.sleep(0.1)
        except usb.core.USBError as e:
            if e.errno not in [16, 110]: # 16 (busy), 110 (timeout)
                self.logger.warning('USB error: %s', e)
        except AttributeError as e:
            # If the device disconnects, self.usb_device might be None and generate the error
            pass
        
    
    async def loop(self):
        listen_coroutines: list[asyncio.Task] = []
        while not self._stopping:
            if not self.usb_device:
                # Cancel any leftover tasks from a previous connection
                for task in listen_coroutines:
                    task.cancel()
                listen_coroutines = []
                await asyncio.sleep(0.1)
                continue

            if self.device_config is not None:
                all_listen = list(set(self.device_config.listen_interface_indexes + self._active_extra_dial_interfaces))
                listen_coroutines = [asyncio.create_task(self.listen_endpoint_loop(interface_id)) for interface_id in all_listen]

            self.request_device_status()

            await asyncio.gather(*listen_coroutines, return_exceptions=True)

        # Cleanup on stop
        for task in listen_coroutines:
            task.cancel()

    def on_device_connected(self, vendor_id: int, product_id: int) -> None:
        for device_config in self.device_configurations:
            if device_config.vendor_id == vendor_id and product_id in device_config.product_ids:
                self.configure_virtual_sinks()
                break
    
    def on_device_disconnected(self, vendor_id: int, product_id: int) -> None:
        # vendor_id and product_id are not available. Check if the current device is still plugged in.

        if self.usb_device is None or self.device_config is None:
            return

        current_usb_device = self._find_hid_device(self.device_config.vendor_id, self.device_config.product_ids)

        if current_usb_device is None:
            self.teardown()
    
    def _update_active_dial_interfaces(self) -> None:
        """Compute which extra interfaces (outside listen_interface_indexes) to scan for the dial.

        Uses the cached value from DeviceSettings if available, otherwise falls back to
        all dial_interface_candidates that are not already in listen_interface_indexes.
        """
        if not self.device_config:
            self._active_extra_dial_interfaces = []
            return

        # All declared dial interfaces that are not already covered by the status listener
        all_candidates = list(set(
            [self.device_config.dial_interface_index] + self.device_config.dial_interface_candidates
        ))
        extra_candidates = [i for i in all_candidates if i not in self.device_config.listen_interface_indexes]

        if not extra_candidates:
            self._active_extra_dial_interfaces = []
            return

        cached = self.device_settings.get_dial_interface()
        if cached is not None:
            # Use only the confirmed interface; skip scanning the others
            self._active_extra_dial_interfaces = [cached] if cached not in self.device_config.listen_interface_indexes else []
            self.logger.info(f"Dial interface loaded from cache: {cached}")
        else:
            # No cache yet — scan all candidates until the dial is turned
            self._active_extra_dial_interfaces = extra_candidates
            self.logger.info(f"Dial interface unknown, scanning candidates: {extra_candidates}")

    def reload_device_configurations(self) -> None:
        self.device_configurations = load_device_configurations()
        self.configure_virtual_sinks()
    
    def configure_virtual_sinks(self) -> None:
        usb_device: Device | Any | None = None
        device_config: DeviceConfiguration | None = None

        for device_config in self.device_configurations:
            usb_device = self._find_hid_device(device_config.vendor_id, device_config.product_ids)
            if usb_device is not None:
                break

        if not device_config or not usb_device:
            self.logger.warning("No supported device connected, skipping virtual sink setup")
            return
        
        if self.device_config is not None and self.device_config != device_config:
            # Reset the previous device first
            self.teardown()
        
        self.usb_device = cast(TypedDevice, usb_device)
        self.device_config = device_config
        self.device_status = self.new_device_status()
        self.device_settings = DeviceSettings(self.usb_device.idVendor, self.usb_device.idProduct)

        # Load defaults
        for _, section in self.device_config.settings.items():
            for setting in section:
                setattr(self.device_settings, setting.name, setting.default_value)
        # Load user settings
        self.device_settings.read_from_file()

        # Setup settings observer
        self.device_settings.settings.add_observer(self.on_setting_changed)

        # Compute which extra (non-status) interfaces to listen on for the dial
        self._update_active_dial_interfaces()

        if self.usb_device is not None:
            self.logger.info(f"Found device {self.usb_device.idProduct:04x}:{self.usb_device.idVendor:04x} ({self.device_config.name})")
            self.kernel_detach(self.usb_device, self.device_config)

        # Discover ALSA nodes for this device and update shared device state
        _DEFAULT_OUT = "alsa_output.usb-SteelSeries_Arctis_Nova_Pro_Wireless-00.analog-stereo"
        _DEFAULT_IN  = "alsa_input.usb-SteelSeries_Arctis_Nova_Pro_Wireless-00.mono-fallback"
        sinks = self.pa_audio_manager.get_arctis_sinks(
            ONLY_PHYSICAL,
            vendor_id=device_config.vendor_id,
            product_id=self.usb_device.idProduct if self.usb_device else None,
        )
        physical_out = sinks[0].name if sinks else _DEFAULT_OUT
        source = self.pa_audio_manager.get_physical_source(
            vendor_id=device_config.vendor_id,
            product_id=self.usb_device.idProduct if self.usb_device else None,
        )
        physical_in = source.name if source else _DEFAULT_IN
        device_state.set_current_device(
            physical_out=physical_out,
            physical_in=physical_in,
            spatial_engine=device_config.spatial_engine,
            device_name=device_config.name,
        )

        # Configure the device
        self.init_device()

        if self.oled_manager is not None:
            self.oled_manager.stop()
            self.oled_manager = None
        has_oled = (
            device_config.status is not None
            and 'gamedac' in device_config.status.representation
        )
        if has_oled:
            self.oled_manager = OledManager(self)
            self.oled_manager.start()

        self.redirect_to_media_sink()
    
    def init_device(self):
        self.logger.info("Initializing device...")
        if self.device_config and self.device_config.device_init:
            endpoint = self.get_command_endpoint_address()

            for bytes in self.device_config.device_init:
                self.send_command(self.translate_init_bytes(bytes), endpoint)

        self._apply_stored_eq()

    def _apply_stored_eq(self) -> None:
        eq_file = Path.home() / '.config' / 'arctis_manager' / 'eq_bands.json'
        if not eq_file.exists():
            return
        try:
            bands = json.loads(eq_file.read_text())
            if isinstance(bands, list) and len(bands) == 10:
                endpoint = self.get_command_endpoint_address()
                self.send_command([0x06, 0x33] + bands, endpoint)
                self.logger.info("Custom EQ applied from eq_bands.json")
        except Exception as e:
            self.logger.warning(f"Failed to apply stored EQ: {e}")

    def send_eq_command(self, bands: list[int]) -> None:
        endpoint = self.get_command_endpoint_address()
        self.send_command([0x06, 0x33] + bands, endpoint)
    
    def is_device_online(self) -> bool:
        if self.device_status is None or self.device_config is None:
            return False
        
        if (online_status_config := self.device_config.online_status) is None:
            return True
        
        parsed = parsed_status(self.device_status, self.device_config)

        return online_status_config is None or parsed.get(online_status_config.status_variable) == online_status_config.online_value
    
    def on_device_status_changed(self, key: str, value: int):
        if self.device_config and self.device_config.online_status and key == self.device_config.online_status.status_variable:
            if self.is_device_online():
                self.redirect_to_media_sink()
            else:
                self.redirect_audio_on_disconnect()

        if key == 'eq_band_value' and self.device_status is not None:
            band_index = self.device_status.get('eq_band_index')
            if band_index is not None:
                self._update_eq_band_file(band_index - 1, value)  # device uses 1-based index

    def _update_eq_band_file(self, index: int, raw_value: int) -> None:
        eq_file = Path.home() / '.config' / 'arctis_manager' / 'eq_bands.json'
        try:
            bands = json.loads(eq_file.read_text()) if eq_file.exists() else [20] * 10
            if 0 <= index <= 9:
                bands[index] = raw_value
                eq_file.write_text(json.dumps(bands))
                self.logger.info(f'EQ band {index} updated to raw={raw_value} ({(raw_value - 20) * 0.5:+.1f} dB)')
        except Exception as e:
            self.logger.warning(f'Failed to update EQ band file: {e}')
    
    def redirect_to_media_sink(self):
        if not self.general_settings.redirect_audio_on_connect or not self.is_device_online():
            return

        self.pa_audio_manager.redirect_audio(PULSE_MEDIA_NODE_NAME)

    def redirect_audio_on_disconnect(self):
        redirect_device = self.general_settings.redirect_audio_on_disconnect_device if self.general_settings.redirect_audio_on_disconnect else None
        current_default_device = self.pa_audio_manager.get_default_device()

        if current_default_device and redirect_device and current_default_device.name in [PULSE_MEDIA_NODE_NAME, PULSE_CHAT_NODE_NAME]:
            self.pa_audio_manager.redirect_audio(redirect_device)
    
    def translate_init_bytes(self, data: list[int|str]) -> list[int]:
        result: list[int] = []

        for byte in data:
            if isinstance(byte, int):
                result.append(byte)
            elif isinstance(byte, str):
                uri = byte.split('.')
                if uri[0] == 'settings':
                    result.append(self.device_settings.get(uri[1]))
                elif byte == 'status.request':
                    if self.device_config is None:
                        raise Exception(f'Device configuration is not available, skipping {byte}')
                    if self.device_config.status is None:
                        self.logger.warning(f'Device status configuration is not available, skipping {byte}')
                    else:
                        result.append(self.device_config.status.request)

        return result
    
    def _get_command_interface(self, config: DeviceConfiguration) -> int:
        """Returns the USB interface number used for commands."""
        return config.command_interface_index[0]

    def get_command_endpoint_address(self):
        if self.device_config is None:
            raise Exception('Device configuration is not available')
        if self.usb_device is None:
            raise Exception('USB device is not available')

        # ctrl_output and ctrl_feature use HID SET_REPORT via ctrl_transfer (no interrupt OUT)
        if self.device_config.command_transport != CommandTransport.INTERRUPT:
            return 0

        endpoint, _ = self.guess_interface_endpoint('out', self.device_config.command_interface_index[0], self.device_config.command_interface_index[1])
        if endpoint is None:
            raise Exception(f"Failed to find command interface endpoint for device: {self.usb_device.idProduct:04x}:{self.usb_device.idVendor:04x}")

        return endpoint
    
    def on_setting_changed(self, setting: str, value: int) -> None:
        if self.device_config is None:
            self.logger.warning('Attempted to change setting without a device configuration')
            return

        config = next((
            config
            for section in self.device_config.settings.keys()
            for config in self.device_config.settings[section] if config.name == setting
        ), None)

        if not config:
            self.logger.warning(f'Unknown setting: {setting}')
            return

        endpoint = self.get_command_endpoint_address()
        seq = config.get_update_sequence(value)
        self.logger.info(f'send_command: {setting}={value} → {[hex(b) for b in seq]} on endpoint {endpoint}')
        self.send_command(seq, endpoint)

    def send_command(self, command: list[int], endpoint: int) -> None:
        if self.device_config is None:
            raise Exception('Device configuration is not available')
    
        if self.usb_device is None:
            raise Exception('USB device is not available')

        command_str = ''.join(f'{byte:02x}' for byte in command)
        if len(command_str) % 2 != 0:
            command_str = f'0{command_str}'

        filler = f'{self.device_config.command_padding.filler:02x}'
        if len(filler) % 2 != 0:
            filler = f'0{filler}'
        
        if len(command_str) < self.device_config.command_padding.length * 2:
            command_str = f'{command_str}{filler * (self.device_config.command_padding.length - len(command_str) // 2)}'

        command_lst = [int.from_bytes([int(command_str[i:i+2], 16)], 'big') for i in range(0, len(command_str), 2)]

        try:
            with self._usb_write_lock:
                if endpoint != 0:
                    self.usb_device.write(endpoint, command_lst)
                else:
                    bmRequestType = usb.util.build_request_type(
                        direction=usb.util.CTRL_OUT,
                        type=usb.util.CTRL_TYPE_CLASS,
                        recipient=usb.util.CTRL_RECIPIENT_INTERFACE
                    )
                    wValue = 0x0300 if self.device_config.command_transport == CommandTransport.CTRL_FEATURE else 0x0200
                    wIndex = self.device_config.command_interface_index[0]
                    self.usb_device.ctrl_transfer(bmRequestType, 0x09, wValue, wIndex, command_lst)
        except usb.core.USBError as e:
            self.logger.warning(f"Error sending command: {e}")

    def _find_hid_device(self, vendor_id: int, product_ids: list[int]) -> 'TypedDevice | None':
        """Find the first USB device matching vendor_id/product_ids that exposes an HID interface."""
        USB_CLASS_HID = 3
        for product_id in product_ids:
            device = usb.core.find(idVendor=vendor_id, idProduct=product_id)
            if device is None:
                continue
            devices = [device] if isinstance(device, Device) else list(device)
            for dev in devices:
                try:
                    for cfg in dev:
                        for intf in cfg:
                            if intf.bInterfaceClass == USB_CLASS_HID:
                                return cast(TypedDevice, dev)
                except Exception:
                    continue
        return None

    def _all_used_interfaces(self, config: DeviceConfiguration) -> list[int]:
        """Returns all USB interfaces that may be used: command, status listeners, and all dial candidates."""
        return list(set([
            self._get_command_interface(config),
            *config.listen_interface_indexes,
            config.dial_interface_index,
            *config.dial_interface_candidates,
        ]))

    def kernel_detach(self, usb_device: TypedDevice, config: DeviceConfiguration) -> None:
        self.logger.info(f"Detaching kernel driver for device: {usb_device.idVendor:04x}:{usb_device.idProduct:04x} ({config.name})")

        for interface in self._all_used_interfaces(config):
            if usb_device.is_kernel_driver_active(interface):
                self.logger.info(f"Kernel driver active on interface {interface}, detaching...")
                usb_device.detach_kernel_driver(interface)

    def kernel_attach(self, usb_device: TypedDevice, config: DeviceConfiguration) -> None:
        self.logger.info(f"Re-attaching kernel driver for device: {usb_device.idProduct:04x}:{usb_device.idVendor:04x} ({config.name})")

        for interface in self._all_used_interfaces(config):
            if not usb_device.is_kernel_driver_active(interface):
                self.logger.info(f"Kernel driver inactive on interface {interface}, re-attaching...")
                usb_device.attach_kernel_driver(interface)
    
    def guess_interface_endpoint(self, direction: Literal['in', 'out'], interface_index: int, interface_alternate_setting: int = 0) -> tuple[int | None, int | None]:
        '''
        Returns the endpoint address and max packet size for the given interface index and alternate setting.
        '''
        if self.usb_device is None:
            return None, None

        directions = {'in': usb.util.ENDPOINT_IN, 'out': usb.util.ENDPOINT_OUT}

        interface: usb.core.Interface|None = next((
            config
            for config in self.usb_device.get_active_configuration()
            if config.bInterfaceNumber == interface_index and config.bAlternateSetting == interface_alternate_setting
        ), None)

        if interface is None:
            raise Exception(f"Failed to find interface for device: {self.usb_device.idProduct:04x}:{self.usb_device.idVendor:04x} (interface: {interface_index}, alternate setting: {interface_alternate_setting})")

        for endpoint in interface.endpoints():
            if usb.util.endpoint_direction(endpoint.bEndpointAddress) == directions[direction]:
                return endpoint.bEndpointAddress, endpoint.wMaxPacketSize

        return None, None

    def request_device_status(self):
        if not self.usb_device or not self.device_config or not self.device_config.status:
            return
        
        endpoint = self.get_command_endpoint_address()
        self.send_command([self.device_config.status.request], endpoint)

    def teardown(self) -> None:
        if self.usb_device:
            try:
                if self.device_config is not None:
                    usb.util.release_interface(self.usb_device, self._get_command_interface(self.device_config))
                if self.device_config and usb.core.find(idVendor=self.device_config.vendor_id):
                    self.kernel_attach(self.usb_device, self.device_config)
            except usb.core.USBError as e:
                self.logger.warning(f"Error re-attaching kernel driver: {e}")

        try:
            self.redirect_audio_on_disconnect()
        except Exception as e:
            self.logger.warning(f"Error redirecting audio on disconnect: {e}")

        device_state.clear()

        if self.oled_manager is not None:
            self.oled_manager.stop()
            self.oled_manager = None

        with self._device_lock:
            self.usb_device = None
            self.device_config = None
            self.device_status = None
            self._active_extra_dial_interfaces = []
