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

    # PipeWire/PulseAudio sometimes isn't ready when the daemon starts
    # (boot race, user session not yet up). Retry with exponential-ish
    # backoff before giving up on the constructor.
    _CONNECT_MAX_ATTEMPTS = 12
    _CONNECT_INITIAL_DELAY_S = 0.5
    _CONNECT_MAX_DELAY_S = 4.0

    def __init__(self):
        self.logger = logging.getLogger('PulseAudioManager')
        self.pulse = self._connect_with_retry()

    def _connect_with_retry(self) -> 'pulsectl.Pulse':
        delay = self._CONNECT_INITIAL_DELAY_S
        last_err: Exception | None = None
        for attempt in range(1, self._CONNECT_MAX_ATTEMPTS + 1):
            try:
                client = pulsectl.Pulse('arctis-sound-manager')
                if attempt > 1:
                    self.logger.info(f'Connected to PulseAudio/PipeWire on attempt {attempt}.')
                return client
            except Exception as e:
                last_err = e
                self.logger.warning(
                    f'PulseAudio connect attempt {attempt}/{self._CONNECT_MAX_ATTEMPTS} '
                    f'failed: {e!r}; retrying in {delay:.1f}s.'
                )
                time.sleep(delay)
                delay = min(delay * 1.5, self._CONNECT_MAX_DELAY_S)
        raise RuntimeError(
            f'Could not connect to PulseAudio/PipeWire after '
            f'{self._CONNECT_MAX_ATTEMPTS} attempts: {last_err!r}. '
            'Is pipewire-pulse / pulseaudio running for this user?'
        )

    def _reconnect(self):
        try:
            self.pulse.disconnect()
        except Exception:
            pass
        self.pulse = self._connect_with_retry()

    def sink_list_wrapper(self) -> list[TypedPulseSinkInfo]:
        max_attempts = 15

        sinks: list[TypedPulseSinkInfo] = []
        for attempt in range(max_attempts):
            try:
                sinks = self.pulse.sink_list()
                break
            except Exception as e:
                if attempt >= 2:
                    self.logger.error(f'Error while getting sink list (attempt {attempt + 1}): {e}')
                else:
                    self.logger.debug(f'Sink list not ready yet (attempt {attempt + 1}), retrying...')
                self._reconnect()
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
        try:
            server_info = self.pulse.server_info()
        except Exception:
            return None
        default_sink_name: str|None = getattr(server_info, 'default_sink_name', None)

        if default_sink_name is None:
            return None
        
        sink = next((s for s in self.sink_list_wrapper() if s.proplist.get('node.name', '') == default_sink_name), None)

        return sink

    def get_physical_source(self, vendor_id: int = STEELSERIES_VENDOR_ID, product_id: int | list[int] | None = None) -> 'pulsectl.PulseSourceInfo | None':
        """Return the first physical ALSA source (microphone) matching the given device IDs."""
        retry_attempts = 15
        sources: list = []
        while retry_attempts > 0:
            try:
                sources = self.pulse.source_list()
                break
            except Exception as e:
                self.logger.error(f'Error getting source list: {e}')
                retry_attempts -= 1
                self._reconnect()
                time.sleep(1)

        def check_prod_id(product_id_attr: str) -> bool:
            if product_id is None:
                return True
            lst = product_id if type(product_id) is list else [product_id]
            return product_id_attr in [f'0x{pid:04x}' for pid in lst]

        physical = [s for s in sources
                    if s.proplist.get('device.vendor.id', '') == f'0x{vendor_id:04x}'
                    and check_prod_id(s.proplist.get('device.product.id', ''))]
        return physical[0] if physical else None

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

