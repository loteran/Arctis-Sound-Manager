import logging
import time

import pulsectl

from arctis_sound_manager.constants import (PULSE_CHAT_NODE_NAME,
                                            PULSE_MEDIA_NODE_NAME,
                                            STEELSERIES_VENDOR_ID)

ONLY_PHYSICAL = 1
ONLY_VIRTUAL = 2
ALL_SINKS = 3

class TypedPulseSinkInfo(pulsectl.PulseSinkInfo):
    name: str


class PulseAudioManager:
    _instance: 'PulseAudioManager|None' = None

    @staticmethod
    def get_instance() -> 'PulseAudioManager':
        if PulseAudioManager._instance is None:
            PulseAudioManager._instance = PulseAudioManager()

        return PulseAudioManager._instance

    def __init__(self):
        self.pulse = pulsectl.Pulse('arctis-sound-manager')
        self.logger = logging.getLogger('PulseAudioManager')
    
    def sink_list_wrapper(self) -> list[TypedPulseSinkInfo]:
        retry_attempts = 15

        sinks: list[TypedPulseSinkInfo] = []
        while retry_attempts > 0:
            try:
                sinks = self.pulse.sink_list()
                break
            except pulsectl.PulseError as e:
                self.logger.error(f'Error while getting sink list: {e}')
                retry_attempts -= 1
                time.sleep(1)

        sinks: list[TypedPulseSinkInfo] = sinks if type(sinks) is list else [sinks] # pyright: ignore[reportAssignmentType]

        return sinks
    
    def get_arctis_sinks(self, mode: int = ALL_SINKS, vendor_id: int = STEELSERIES_VENDOR_ID, product_id: int|list[int]|None = None) -> list[TypedPulseSinkInfo]:
        sinks = self.sink_list_wrapper()

        def check_prod_id(product_id_attr: str) -> bool:
            if product_id is None:
                return True

            lst = product_id if type(product_id) is list else [product_id]

            return product_id_attr in [f'0x{pid:04x}' for pid in lst]

        physical = [s for s in sinks if s.proplist.get('device.vendor.id', '') == f'0x{vendor_id:04x}' and check_prod_id(s.proplist.get('device.product.id', ''))]
        virtual = [s for s in sinks if s.proplist.get('node.name', '') in (PULSE_MEDIA_NODE_NAME, PULSE_CHAT_NODE_NAME)]

        if mode == ONLY_PHYSICAL:
            sinks = physical
        elif mode == ONLY_VIRTUAL:
            sinks = virtual
        else:
            sinks = physical + virtual

        return sinks

    def redirect_audio(self, output_sink_node_name: str) -> None:
        self.logger.info(f'Redirecting audio to {output_sink_node_name}...')

        sink = next((s for s in self.sink_list_wrapper() if s.proplist.get('node.nick', '') == output_sink_node_name or s.proplist.get('node.name', '') == output_sink_node_name), None)
        if sink is None:
            self.logger.error(f'Failed to find sink {output_sink_node_name} to set it as default')
            return
        self.pulse.default_set(sink)
    
    def get_default_device(self) -> TypedPulseSinkInfo|None:
        server_info = self.pulse.server_info()
        default_sink_name: str|None = getattr(server_info, 'default_sink_name', None)

        if default_sink_name is None:
            return None
        
        sink = next((s for s in self.sink_list_wrapper() if s.proplist.get('node.name', '') == default_sink_name), None)

        return sink

    def set_mix(self, media_mix: int, chat_mix: int):
        if media_mix > 100:
            media_mix = 100
        if chat_mix > 100:
            chat_mix = 100

        sinks = self.get_arctis_sinks(ONLY_VIRTUAL)

        media = next((s for s in sinks if s.proplist.get('node.name', '') == PULSE_MEDIA_NODE_NAME), None)
        chat = next((s for s in sinks if s.proplist.get('node.name', '') == PULSE_CHAT_NODE_NAME), None)

        if media:
            self.pulse.volume_set_all_chans(media, media_mix / 100)
        if chat:
            self.pulse.volume_set_all_chans(chat, chat_mix / 100)

