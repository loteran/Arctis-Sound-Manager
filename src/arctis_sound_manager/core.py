# Copyright (C) 2022 Giacomo Furlan (elegos) — original work
# Copyright (C) 2026 loteran — modifications
# SPDX-License-Identifier: GPL-3.0-or-later

import asyncio
import json
import logging
import threading
import time
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
                                            PULSE_MEDIA_NODE_NAME,
                                            STEELSERIES_VENDOR_ID)
from arctis_sound_manager.loopback_manager import LoopbackManager, make_specs
from arctis_sound_manager.pactl import ONLY_PHYSICAL, PulseAudioManager
from arctis_sound_manager.settings import DeviceSettings, GeneralSettings
from arctis_sound_manager.usb_devices_monitor import USBDevicesMonitor
from arctis_sound_manager.utils import ObservableDict
from arctis_sound_manager.oled_manager import OledManager

# How often (in seconds) the loop retries detection when the device was not
# ready during the initial scan. A device present at boot fires no udev 'add'
# event, so without this retry it would never be picked up until a replug or
# USB autosuspend resume. (issue #76)
RESCAN_INTERVAL_S: float = 3.0


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

    # Set to True when kernel_detach hits EACCES on a USB interface — read by
    # the GUI (via D-Bus GetSettings) to surface UdevRulesDialog(mode="reload").
    # Cleared automatically on the next successful kernel_detach pass.
    permission_error: bool = False

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

        # Set to True when kernel_detach hits EACCES on a USB interface
        # (udev rules missing or not yet applied to the connected device).
        # Read by the GUI to surface a "Fix permissions" action.
        self.permission_error: bool = False

        self.general_settings = GeneralSettings.read_from_file()

        self.logger = logging.getLogger('CoreEngine')
        self.pa_audio_manager = PulseAudioManager.get_instance()
        self.usb_devices_monitor = USBDevicesMonitor.get_instance()

        # Dynamic loopback manager: owns the pw-loopback child processes for
        # Arctis_Game / Arctis_Chat / Arctis_Media virtual sinks.
        self.loopback_manager = LoopbackManager()

        # Readiness tracking for the periodic re-scan (issue #76).
        # A device present at boot fires no udev 'add' event; these flags let
        # loop() retry detection until the USB/ALSA stack is fully ready.
        self._device_ready: bool = False
        self._detect_lock = threading.Lock()   # serialises detection attempts
        self._rescan_in_flight: bool = False
        self._logged_no_device: bool = False   # throttle the "no device" warning
        self._warned_no_out_endpoint: bool = False  # log once per device attach
        self._last_recreate_loopbacks: float = 0.0  # debounce rapid D-Bus calls

        self.reload_device_configurations()
        self.usb_devices_monitor.register_on_connect(self.on_device_connected)
        self.usb_devices_monitor.register_on_disconnect(self.on_device_disconnected)
    
    def new_device_status(self) -> ObservableDict:
        device_status = ObservableDict()
        device_status.add_observer(self.on_device_status_changed)

        return device_status

    # ── Loopback helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _read_eq_mode_is_sonar() -> bool:
        """Return True if the EQ mode file indicates Sonar mode.

        The mode file lives at ``~/.config/arctis_manager/.eq_mode``.
        If the file is absent or contains anything other than ``"sonar"``,
        simple (non-Sonar) mode is assumed.
        """
        state_file = Path.home() / ".config" / "arctis_manager" / ".eq_mode"
        try:
            return state_file.exists() and state_file.read_text().strip() == "sonar"
        except OSError:
            return False

    def setup_loopbacks(self) -> None:
        """Create or recreate the Arctis virtual loopbacks for the current mode.

        Reads the EQ mode from disk, resolves physical output nodes from
        ``device_state``, builds ``LoopbackSpec`` objects via ``make_specs``,
        and calls ``LoopbackManager.recreate_all`` to tear down any existing
        loopbacks and launch fresh ``pw-loopback`` processes.

        No-op (with a log message) when no device is registered in
        ``device_state`` — the loopbacks will be created when the device is
        detected and ``configure_virtual_sinks`` is called.
        """
        if not device_state.is_device_set():
            self.logger.info("setup_loopbacks: no device registered, skipping loopback creation")
            return
        sonar = self._read_eq_mode_is_sonar()
        physical_game = device_state.get_physical_out_game()
        physical_chat = device_state.get_physical_out_chat()
        dev_name = device_state.get_device_name()
        specs = make_specs(
            sonar=sonar,
            physical_game=physical_game,
            physical_chat=physical_chat,
            device_name=dev_name,
        )
        try:
            self.loopback_manager.recreate_all(specs)
            self.logger.info(
                "setup_loopbacks: loopbacks recreated (sonar=%s, game=%s, chat=%s)",
                sonar, physical_game, physical_chat,
            )
        except Exception as exc:
            self.logger.error("setup_loopbacks: failed to recreate loopbacks: %r", exc)

    _RECREATE_DEBOUNCE_S = 5.0

    def recreate_loopbacks(self) -> None:
        """Public entry point called by the D-Bus ``RecreateLoopbacks`` method.

        Re-reads the EQ mode from disk so mode switches (Sonar ↔ simple) are
        picked up, then recreates all loopbacks.  Wrapped in try/except so a
        failure never crashes the daemon.

        Debounced: if filter-chain.service keeps crashing and restarting (which
        destroys then recreates the Sonar EQ nodes every few seconds), this
        method would otherwise be called on every loopback exit, causing a
        recreation storm.  Calls within _RECREATE_DEBOUNCE_S of the previous
        call are dropped and logged at DEBUG level.
        """
        now = time.monotonic()
        elapsed = now - self._last_recreate_loopbacks
        if elapsed < self._RECREATE_DEBOUNCE_S:
            self.logger.debug(
                "recreate_loopbacks: debounced (%.1f s since last call, min %.1f s)",
                elapsed, self._RECREATE_DEBOUNCE_S,
            )
            return
        self._last_recreate_loopbacks = now
        self.logger.info("recreate_loopbacks: requested via D-Bus")
        try:
            self.setup_loopbacks()
        except Exception as exc:
            self.logger.error("recreate_loopbacks: unexpected error: %r", exc)

    def recreate_loopbacks_game_media(self) -> None:
        """Recreate only Game and Media loopbacks, leaving Chat intact.

        After a filter-chain restart (EQ preset / profile change), Chat
        (always 2ch) auto-reconnects to effect_input.sonar-chat-eq without
        being recreated.  Keeping Arctis_Chat alive prevents Discord and other
        Electron apps from losing the sink from their device list — they
        enumerate devices once and do not detect sinks that reappear.

        Uses the same debounce as recreate_loopbacks.
        """
        now = time.monotonic()
        elapsed = now - self._last_recreate_loopbacks
        if elapsed < self._RECREATE_DEBOUNCE_S:
            self.logger.debug(
                "recreate_loopbacks_game_media: debounced (%.1f s since last call)",
                elapsed,
            )
            return
        self._last_recreate_loopbacks = now
        self.logger.info("recreate_loopbacks_game_media: requested via D-Bus")
        try:
            if not device_state.is_device_set():
                self.logger.info("recreate_loopbacks_game_media: no device, skipping")
                return
            sonar = self._read_eq_mode_is_sonar()
            physical_game = device_state.get_physical_out_game()
            physical_chat = device_state.get_physical_out_chat()
            dev_name = device_state.get_device_name()
            specs = make_specs(
                sonar=sonar,
                physical_game=physical_game,
                physical_chat=physical_chat,
                device_name=dev_name,
            )
            for spec in specs:
                if spec.channel == "chat":
                    continue  # keep Arctis_Chat alive — Discord-safe
                self.loopback_manager.recreate(spec)

            self.logger.info(
                "recreate_loopbacks_game_media: game+media recreated, chat preserved"
            )
        except Exception as exc:
            self.logger.error("recreate_loopbacks_game_media: unexpected error: %r", exc)

    async def _loopback_watchdog(self) -> None:
        """Periodically check for dead or mislinked loopback processes.

        Runs as an asyncio ``Task`` alongside ``core_loop``.  Every 5 seconds,
        if a device is currently connected (``device_state.is_device_set()``),
        performs two passes:

        1. **Dead-process pass** — calls :meth:`LoopbackManager.restart_dead`
           to revive any crashed ``pw-loopback`` processes.
        2. **Mislink pass** — for every loopback that was *not* just restarted
           and is still running, queries PipeWire (via
           :func:`~arctis_sound_manager.pw_utils.loopback_link_target`) to see
           which sink it is actually wired to.  If the actual link target differs
           from the expected ``spec.target``, the loopback is recreated so that
           WirePlumber re-applies ``node.target``.  Orphan loopbacks (not yet
           linked to anything, ``actual is None``) are given a grace period of
           ``_ORPHAN_GRACE_TICKS`` consecutive ticks (15 s at the default
           interval) before being recreated: a single-tick ``None`` is a normal
           transient PipeWire state and must not trigger a spurious recreate.

        The coroutine exits cleanly when ``self._stopping`` is set (by
        :meth:`stop`) or when the task is cancelled (by the daemon shutdown
        handler).  Errors are always caught and logged — this coroutine must
        never crash the daemon.
        """
        from arctis_sound_manager.pw_utils import loopback_link_target

        _WATCHDOG_INTERVAL: float = 5.0
        # Number of consecutive ticks a loopback may be None-linked before we
        # treat it as a permanent orphan and recreate it.  One tick = 5 s, so
        # 3 ticks = 15 s of grace before action.  Transient None states (one
        # PipeWire graph cycle) are ignored entirely.
        _ORPHAN_GRACE_TICKS: int = 3
        # Per-channel count of consecutive ticks where loopback_link_target
        # returned None.  Reset to 0 whenever the link becomes non-None or the
        # loopback is restarted.
        _none_ticks: dict[str, int] = {}
        try:
            while not self._stopping:
                await asyncio.sleep(_WATCHDOG_INTERVAL)
                if self._stopping:
                    break
                if not device_state.is_device_set():
                    # No device — no loopbacks to watch.
                    continue
                try:
                    restarted = self.loopback_manager.restart_dead()
                    if restarted:
                        self.logger.warning(
                            "_loopback_watchdog: restarted dead loopback(s): %s",
                            restarted,
                        )
                        for ch in restarted:
                            _none_ticks.pop(ch, None)
                except Exception as exc:
                    self.logger.error(
                        "_loopback_watchdog: unexpected error in restart_dead: %r", exc
                    )
                    continue

                # Mislink detection pass: verify that each running loopback is
                # wired to the expected node.target.  Skip channels that were
                # just restarted — give them one watchdog tick to get linked.
                try:
                    for channel, spec in self.loopback_manager.specs().items():
                        if channel in restarted:
                            # Just recreated — node.target will bind next cycle.
                            continue
                        if not self.loopback_manager.is_running(channel):
                            continue
                        actual = loopback_link_target(spec.playback_name)
                        if actual is None:
                            # Orphan: may be a transient one-tick PipeWire state.
                            # Accumulate ticks; only act after _ORPHAN_GRACE_TICKS
                            # consecutive None results (= permanent orphan).
                            count = _none_ticks.get(channel, 0) + 1
                            _none_ticks[channel] = count
                            if count < _ORPHAN_GRACE_TICKS:
                                continue
                            self.logger.warning(
                                "_loopback_watchdog: loopback '%s' orphaned for "
                                "%d ticks — recreating",
                                channel, count,
                            )
                            _none_ticks.pop(channel, None)
                        else:
                            _none_ticks.pop(channel, None)
                            if actual == spec.target:
                                continue
                            self.logger.warning(
                                "_loopback_watchdog: loopback '%s' mislinked to %s "
                                "(want %s) — recreating",
                                channel, actual, spec.target,
                            )
                        self.loopback_manager.recreate(spec)
                        if channel == "chat":
                            # After recreating Chat, move PA streams that
                            # had routing overrides back to Arctis_Chat so
                            # Discord audio resumes without a manual restart.
                            from arctis_sound_manager.pw_utils import (
                                reapply_routing_overrides,
                            )
                            await asyncio.get_running_loop().run_in_executor(
                                None, reapply_routing_overrides
                            )
                except Exception as exc:
                    self.logger.error(
                        "_loopback_watchdog: unexpected error in mislink check: %r", exc
                    )
        except asyncio.CancelledError:
            raise

    def start(self) -> Coroutine:
        self._stopping = False
        self.usb_devices_monitor.start()

        return self.loop()
    
    def stop(self):
        self.logger.info("Stopping CoreEngine...")
        self._stopping = True
        self.usb_devices_monitor.stop()
        # Stop all pw-loopback child processes so they don't become orphans
        # when the daemon exits via SIGTERM/SIGINT.  Without this, every
        # `systemctl --user restart arctis-manager` leaves orphan processes
        # that conflict with the next startup (duplicate node.name).
        # Called synchronously from an asyncio signal handler, so we keep it
        # fast and best-effort — never raise.
        try:
            self.loopback_manager.stop_all()
        except Exception as exc:
            self.logger.warning("stop(): error stopping loopbacks: %r", exc)

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
            if e.errno in (16, 110):  # EBUSY / ETIMEDOUT — back off to avoid spam
                await asyncio.sleep(1.0)
            elif e.errno in (19, 2):  # ENODEV / ENOENT — dongle present, RF link gone
                self._enodev_count = getattr(self, '_enodev_count', 0) + 1
                if self._enodev_count == 1 or self._enodev_count % 50 == 0:
                    self.logger.warning('USB device unreachable (errno %d ×%d): %s',
                                        e.errno, self._enodev_count, e)
                await asyncio.sleep(1.0)
                if self._enodev_count >= 10:
                    self.logger.info('Device unreachable for >10 s, releasing handle to allow RF re-association')
                    self._enodev_count = 0
                    self.on_device_disconnected(0, 0)
            else:
                self._enodev_count = 0
                self.logger.warning('USB error: %s', e)
                await asyncio.sleep(0.5)
        except AttributeError:
            # self.usb_device can be None mid-disconnect
            pass
        
    
    async def loop(self):
        listen_coroutines: list[asyncio.Task] = []
        poll_task: asyncio.Task | None = None
        last_rescan: float = 0.0
        while not self._stopping:
            if not self._device_ready:
                # Cancel any leftover tasks from a previous connection
                for task in listen_coroutines:
                    task.cancel()
                listen_coroutines = []
                if poll_task is not None:
                    poll_task.cancel()
                    poll_task = None

                # Periodically retry detection for devices present at boot.
                # Such devices fire no udev 'add' event, so without this retry
                # they would only appear after a replug or USB autosuspend
                # resume. (issue #76)
                event_loop = asyncio.get_event_loop()
                now = event_loop.time()
                if not self._rescan_in_flight and (now - last_rescan) >= RESCAN_INTERVAL_S:
                    last_rescan = now
                    self._rescan_in_flight = True
                    event_loop.run_in_executor(None, self._rescan_for_device)

                await asyncio.sleep(0.1)
                continue

            if self.device_config is not None:
                all_listen = list(set(self.device_config.listen_interface_indexes + self._active_extra_dial_interfaces))
                listen_coroutines = [asyncio.create_task(self.listen_endpoint_loop(interface_id)) for interface_id in all_listen]

                if poll_task is None or poll_task.done():
                    poll_task = asyncio.create_task(self._status_poll_loop())

            await asyncio.gather(*listen_coroutines, return_exceptions=True)

        # Cleanup on stop
        for task in listen_coroutines:
            task.cancel()
        if poll_task is not None:
            poll_task.cancel()

    def _rescan_for_device(self) -> None:
        """Re-attempt detection for a device present at boot but not yet ready.

        A device already plugged in at startup fires no udev 'add' event, so
        without this retry it would never be picked up until a manual replug or
        a USB wake event. Called from loop() via run_in_executor. (issue #76)
        """
        try:
            if not self._device_ready:
                self.configure_virtual_sinks()
        except Exception as e:
            self.logger.warning("Periodic device re-scan failed: %r", e)
        finally:
            self._rescan_in_flight = False

    def on_device_connected(self, vendor_id: int, product_id: int) -> None:
        for device_config in self.device_configurations:
            if device_config.vendor_id == vendor_id and product_id in device_config.product_ids:
                self.configure_virtual_sinks()
                return

        # Reached only when the connected device matches no YAML — surface this
        # loudly so unsupported PIDs are easy to spot in journalctl / bug
        # reports. Limited to the SteelSeries vendor to avoid noise from the
        # rest of the bus when running under the polling backend.
        if vendor_id == 0x1038:
            self.logger.warning(
                f"USB device {vendor_id:04x}:{product_id:04x} appeared but no device YAML matches. "
                "If this is a SteelSeries Arctis headset, please open an issue with this PID so support can be added."
            )
    
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
        with self._detect_lock:
            usb_device: Device | Any | None = None
            device_config: DeviceConfiguration | None = None

            for device_config in self.device_configurations:
                usb_device = self._find_hid_device(device_config.vendor_id, device_config.product_ids)
                if usb_device is not None:
                    break

            if not device_config or not usb_device:
                # Log only on the first miss to avoid spamming every re-scan cycle.
                if not self._logged_no_device:
                    self.logger.warning("No supported device connected, skipping virtual sink setup")
                    self._logged_no_device = True
                return

            # Device found — reset the log-throttle flag so a future disconnect logs again.
            self._logged_no_device = False

            if self.device_config is not None and self.device_config != device_config:
                # Different device — full teardown of the previous one.
                self.teardown()
            elif self.usb_device is not None:
                # Same device re-enumerated (the Nova Pro Wireless does this on boot,
                # wake and replug). Release the stale libusb handle before claiming a
                # fresh one — otherwise the old handle keeps the interface claimed and
                # every later transfer fails with EBUSY (Resource busy), killing the
                # OLED display and all device commands.
                self._release_usb_handle()

            with self._device_lock:
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
                if not self.kernel_detach(self.usb_device, self.device_config):
                    # USB permission error — message already logged with remediation
                    # steps. Bail out so the daemon stays alive instead of crashing.
                    return

            # Discover ALSA nodes for this device and update shared device state
            physical_out_game, physical_out_chat, physical_in = self._discover_physical_nodes(
                device_config.vendor_id,
                self.usb_device.idProduct if self.usb_device else None,
            )

            if physical_out_game is None and physical_out_chat is None:
                self.logger.error(
                    "No physical ALSA sink found for %s (0x%04x:0x%04x) after retries. "
                    "Virtual sinks will NOT be configured — audio routing skipped. "
                    "Check that PipeWire exposes the device: "
                    "`pactl list sinks short | grep -i arctis`. "
                    "If missing, replug the dongle and restart asm-daemon.",
                    device_config.name,
                    device_config.vendor_id,
                    self.usb_device.idProduct if self.usb_device else 0,
                )
                return

            fallback = physical_out_game or physical_out_chat or ""
            device_state.set_current_device(
                physical_out_game=physical_out_game or fallback,
                physical_out_chat=physical_out_chat or fallback,
                physical_in=physical_in or fallback,
                spatial_engine=device_config.spatial_engine,
                device_name=device_config.name,
            )

            # Repair stale PipeWire configs at daemon startup (issue #23).
            #
            # Without this call, the static `10-arctis-virtual-sinks.conf` shipped
            # by `asm-setup` lacks a `node.target` for the Game/Chat sinks, so
            # WirePlumber connects them straight to the physical output and audio
            # bypasses the Sonar EQ + HeSuVi surround chain entirely. The check
            # was previously only run when the user opened the Sonar page in the
            # GUI — users running headless (or never opening that page) saw the
            # bug forever.
            #
            # As of the dynamic-loopbacks migration: check_and_fix_stale_configs
            # now removes the legacy 10-arctis-virtual-sinks.conf and signals a
            # one-shot PipeWire restart.  After the restart the daemon creates the
            # loopbacks dynamically via setup_loopbacks() below.
            try:
                from arctis_sound_manager.sonar_to_pipewire import check_and_fix_stale_configs
                fixed, needs_pw_restart = check_and_fix_stale_configs()
                if fixed:
                    from arctis_sound_manager import service_control as sc
                    if needs_pw_restart:
                        self.logger.info("Stale PipeWire configs migrated — restarting PipeWire")
                        sc.restart("pipewire", "wireplumber", "pipewire-pulse", timeout=20)
                        sc.restart("filter-chain", timeout=20)
                    else:
                        self.logger.info("Stale Sonar configs fixed — restarting filter-chain")
                        sc.restart("filter-chain", timeout=15)
            except Exception as exc:
                # Never let a config-repair failure block device init.
                self.logger.warning(f"check_and_fix_stale_configs failed: {exc!r}")

            # Create dynamic loopbacks (Arctis_Game / Arctis_Chat / Arctis_Media).
            # device_state is already populated above, so make_specs can resolve
            # targets.  The EQ nodes (effect_input.sonar-*-eq) are created by
            # filter-chain; node.target by name binds when the node appears, so
            # the ordering here is tolerant of filter-chain not being up yet.
            self.setup_loopbacks()
            self.pa_audio_manager.set_default_source("effect_output.sonar-micro-eq")

            # Configure the device
            self.init_device()

            if self.oled_manager is not None:
                self.oled_manager.stop()
                self.oled_manager = None
            has_oled = (
                device_config.status is not None
                and 'gamedac' in device_config.status.representation
                and device_config.oled is not None
            )
            if has_oled:
                self.oled_manager = OledManager(self)
                self.oled_manager.start()

            self.redirect_to_media_sink()
            # Reached only when the full pipeline was configured without an early
            # return; mark the device as ready so loop() stops re-scanning.
            self._device_ready = True

    def _discover_physical_nodes(
        self,
        vendor_id: int,
        product_id: int | None,
        attempts: int = 8,
        delay: float = 0.5,
    ) -> tuple[str | None, str | None, str | None]:
        """Resolve the physical ALSA sink/source names for the attached device.

        PipeWire can take a couple of seconds to enumerate a freshly-attached USB
        audio card, so the lookup is retried. On some PipeWire builds the ALSA
        proxy nodes don't expose `device.product.id`; in that case we fall back
        to matching on vendor_id alone (any Arctis sink).

        Devices with two ALSA PCMs (e.g. Arctis 7 Pro Audio firmware) expose
        pro-output-0 (mono, chat/sidetone) and pro-output-1 (stereo, game).
        `get_arctis_sinks_classified()` separates them; single-output devices
        return the same sink for both roles.

        Returns (game_sink_name, chat_sink_name, source_name) — any can be None.
        """
        # Try all PIDs from the device config — HID and audio PIDs often differ
        # (e.g. Arctis Pro Wireless: HID=0x1290, audio=0x1294).
        all_pids: list[int] | None = (
            self.device_config.product_ids if self.device_config else None
        )
        for attempt in range(attempts):
            game_sink, chat_sink = self.pa_audio_manager.get_arctis_sinks_classified(
                vendor_id=vendor_id, product_id=all_pids or product_id,
            )
            source = self.pa_audio_manager.get_physical_source(
                vendor_id=vendor_id, product_id=all_pids or product_id,
            )
            if game_sink or chat_sink:
                return (
                    game_sink.name if game_sink else None,
                    chat_sink.name if chat_sink else None,
                    source.name if source else None,
                )
            if attempt < attempts - 1:
                time.sleep(delay)

        # Vendor-only fallback: some PipeWire builds don't populate device.product.id
        # on ALSA proxy nodes. Matching any SteelSeries sink is better than a
        # hardcoded wrong default.
        game_sink, chat_sink = self.pa_audio_manager.get_arctis_sinks_classified(
            vendor_id=vendor_id, product_id=None,
        )
        if game_sink or chat_sink:
            self.logger.warning(
                "No sink matched PID 0x%04x exactly — falling back to "
                "vendor-only match: game=%s chat=%s",
                product_id or 0,
                game_sink.name if game_sink else None,
                chat_sink.name if chat_sink else None,
            )
            source = self.pa_audio_manager.get_physical_source(
                vendor_id=vendor_id, product_id=None,
            )
            return (
                game_sink.name if game_sink else None,
                chat_sink.name if chat_sink else None,
                source.name if source else None,
            )

        return None, None, None

    def init_device(self):
        self.logger.info("Initializing device...")
        if self.device_config and self.device_config.device_init:
            endpoint = self.get_command_endpoint_address()
            total = len(self.device_config.device_init)

            for index, bytes in enumerate(self.device_config.device_init, start=1):
                # One retry on USBError — most failures here are transient
                # (kernel driver re-attached itself between detach and write,
                # device still warming up after enumeration). Persistent
                # failures continue with the remaining commands so partial
                # state at least powers something rather than nothing.
                for attempt in (1, 2):
                    try:
                        self.send_command(self.translate_init_bytes(bytes), endpoint)
                        break
                    except usb.core.USBError as e:
                        if attempt == 1:
                            self.logger.warning(
                                f"init_device cmd {index}/{total} failed ({e!r}); retrying once."
                            )
                            continue
                        self.logger.error(
                            f"init_device cmd {index}/{total} still failing after retry: {e!r}. "
                            "Device may be left in a partially-configured state."
                        )

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
        actual = parsed.get(online_status_config.status_variable)
        expected = online_status_config.online_value

        # The `on_off` parser returns 'on'/'off' but 8 device YAMLs declare
        # online_value: 'online'. Without this aliasing Nova 5, Nova 7,
        # Arctis 7+, Arctis 9 and Arctis 1 Wireless always report offline.
        _ON = {'on', 'online'}
        _OFF = {'off', 'offline'}
        if isinstance(actual, str) and isinstance(expected, str):
            al, el = actual.lower(), expected.lower()
            if el in _ON:
                return al in _ON
            if el in _OFF:
                return al in _OFF

        return actual == expected
    
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

    # Sink name fragments that mean "audio is going through the Arctis headset".
    # Includes all three virtual loopbacks, the full Sonar EQ pipeline and the
    # raw SteelSeries ALSA node. If the current default matches any fragment
    # we fall back to the user-configured disconnect device.
    _ARCTIS_OWNED_SINK_FRAGMENTS = (
        'Arctis_Game', 'Arctis_Chat', 'Arctis_Media',
        'effect_input.sonar-game-eq',
        'effect_input.sonar-chat-eq',
        'effect_input.sonar-media-eq',
        'effect_input.sonar-output-eq',
        'effect_input.virtual-surround-7.1-hesuvi',
    )

    def redirect_audio_on_disconnect(self):
        if not self.general_settings.redirect_audio_on_disconnect:
            return
        redirect_device = self.general_settings.redirect_audio_on_disconnect_device
        if not redirect_device:
            return

        current_default_device = self.pa_audio_manager.get_default_device()
        if current_default_device is None:
            self.pa_audio_manager.redirect_audio(redirect_device)
            return

        current_name = current_default_device.name or ''
        is_steelseries_alsa = (
            current_name.startswith('alsa_output')
            and int(current_default_device.proplist.get('device.vendor.id', '0') or '0', 16) == STEELSERIES_VENDOR_ID
        )
        is_arctis_owned = any(
            frag in current_name for frag in self._ARCTIS_OWNED_SINK_FRAGMENTS
        )

        if is_steelseries_alsa or is_arctis_owned:
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
            # Some units (e.g. certain Arctis 7X firmwares, issue #59) expose the
            # command interface with an interrupt IN endpoint only — no OUT. The
            # correct path then is HID SET_REPORT over the control endpoint, which
            # send_command() already handles for endpoint 0 (wValue 0x0200, output).
            # Fall back instead of crashing the whole daemon.
            if not self._warned_no_out_endpoint:
                self._warned_no_out_endpoint = True
                self.logger.warning(
                    f"No interrupt OUT endpoint on command interface "
                    f"{self.device_config.command_interface_index[0]} for "
                    f"{self.usb_device.idVendor:04x}:{self.usb_device.idProduct:04x}; "
                    f"falling back to HID SET_REPORT (control transfer)."
                )
            else:
                self.logger.debug(
                    "No interrupt OUT endpoint on command interface %d for %04x:%04x (SET_REPORT fallback active).",
                    self.device_config.command_interface_index[0],
                    self.usb_device.idVendor, self.usb_device.idProduct,
                )
            return 0

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
        seq = self._resolve_update_sequence(config, value)
        self.logger.info(f'send_command: {setting}={value} → {[hex(b) for b in seq]} on endpoint {endpoint}')
        self.send_command(seq, endpoint)

    def _resolve_update_sequence(self, config, value: int) -> list[int]:
        result = []
        for b in config.update_sequence:
            if isinstance(b, int):
                result.append(b)
            elif b == 'value':
                result.append(value)
            elif isinstance(b, str) and b.startswith('settings.'):
                setting_name = b.split('.', 1)[1]
                result.append(self.device_settings.get(setting_name, 0))
            else:
                raise Exception(f"Invalid update sequence value: {b}")
        return result

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
                    # wValue = (report_type << 8) | report_id, per HID SET_REPORT.
                    # report_id defaults to 0 (unnumbered reports — unchanged for the
                    # Nova 7 family etc.); devices with a real report-id prefix declare
                    # command_report_id so the wValue low byte matches. The Nova Pro
                    # Wired GameDAC rejects a mismatched wValue → commands silently
                    # fail (e.g. high-gain never applied, hence near-inaudible output
                    # until cranked to ~95%). (issue #76)
                    report_type = 0x03 if self.device_config.command_transport == CommandTransport.CTRL_FEATURE else 0x02
                    report_id = self.device_config.command_report_id or 0
                    wValue = (report_type << 8) | (report_id & 0xFF)
                    wIndex = self.device_config.command_interface_index[0]
                    self.usb_device.ctrl_transfer(bmRequestType, 0x09, wValue, wIndex, command_lst)
        except usb.core.USBError as e:
            if getattr(e, "errno", None) == 16:  # EBUSY — throttle log
                self._usb_busy_count = getattr(self, "_usb_busy_count", 0) + 1
                if self._usb_busy_count == 1 or self._usb_busy_count % 10 == 0:
                    self.logger.warning("Error sending command (EBUSY ×%d): %s",
                                        self._usb_busy_count, e)
            else:
                self._usb_busy_count = 0
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

    def kernel_detach(self, usb_device: TypedDevice, config: DeviceConfiguration) -> bool:
        """Detach the kernel driver from every interface ASM uses.

        Returns True on success, False on USB permission/access errors so the
        caller can bail out cleanly instead of letting the daemon crash.
        """
        self.logger.info(f"Detaching kernel driver for device: {usb_device.idVendor:04x}:{usb_device.idProduct:04x} ({config.name})")

        had_eacces = False
        for interface in self._all_used_interfaces(config):
            try:
                if usb_device.is_kernel_driver_active(interface):
                    self.logger.info(f"Kernel driver active on interface {interface}, detaching...")
                    usb_device.detach_kernel_driver(interface)
            except usb.core.USBError as e:
                # Per-interface failure: device disconnected mid-detach, EACCES
                # (udev rules not applied to this device), or already claimed.
                # Log with remediation steps for EACCES, continue the loop so
                # the rest of the device still claims.
                if getattr(e, "errno", None) == 13:
                    had_eacces = True
                    self.logger.error(
                        "USB access denied while detaching the kernel driver for %s "
                        "(0x%04x:0x%04x) on interface %d. udev rules are missing or "
                        "have not been applied to the currently-attached device. "
                        "Try one of: 1) replug the dongle, "
                        "2) `sudo asm-cli udev reload-rules`, "
                        "3) reinstall ASM via your distro package so the rules go "
                        "to /etc/udev/rules.d/. The GUI will offer a one-click fix "
                        "if it's open. The daemon will keep running.",
                        config.name, usb_device.idVendor, usb_device.idProduct, interface,
                    )
                else:
                    self.logger.warning(
                        f"Could not detach kernel driver on interface {interface}: {e!r}. "
                        "Continuing with remaining interfaces."
                    )
        # Surface the EACCES state to the GUI; clear it once a clean pass happens.
        self.permission_error = had_eacces
        return not had_eacces

    def kernel_attach(self, usb_device: TypedDevice, config: DeviceConfiguration) -> bool:
        """Re-attach the kernel driver. Returns False on USB error (best effort)."""
        self.logger.info(f"Re-attaching kernel driver for device: {usb_device.idProduct:04x}:{usb_device.idVendor:04x} ({config.name})")

        ok = True
        for interface in self._all_used_interfaces(config):
            try:
                if not usb_device.is_kernel_driver_active(interface):
                    self.logger.info(f"Kernel driver inactive on interface {interface}, re-attaching...")
                    usb_device.attach_kernel_driver(interface)
            except usb.core.USBError as e:
                self._log_usb_access_error(e, usb_device, config, interface, action="re-attaching")
                ok = False
        return ok

    def _log_usb_access_error(
        self,
        err: 'usb.core.USBError',
        usb_device: TypedDevice,
        config: DeviceConfiguration,
        interface: int,
        action: str,
    ) -> None:
        if getattr(err, "errno", None) == 13:  # EACCES
            self.permission_error = True
            self.logger.error(
                "USB access denied while %s the kernel driver for %s "
                "(0x%04x:0x%04x) on interface %d. udev rules are missing or "
                "have not been applied to the currently-attached device. "
                "Try one of: 1) replug the dongle, "
                "2) `sudo udevadm control --reload-rules && sudo udevadm trigger`, "
                "3) reinstall ASM via your distro package (deb / rpm / AUR) so "
                "the udev rules are written to /etc/udev/rules.d/. "
                "Skipping this device — the daemon will keep running.",
                action, config.name, usb_device.idVendor, usb_device.idProduct, interface,
            )
        else:
            self.logger.error(
                "USB error %s the kernel driver for %s on interface %d: %s",
                action, config.name, interface, err,
            )
    
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

    async def _status_poll_loop(self, period: float = 2.0):
        # Nova 5 and 7 firmwares only emit a status frame when the radio link
        # changes. If the user powers off the headset while the dongle stays
        # plugged in, no packet arrives and on_device_status_changed never
        # fires. Polling at a fixed cadence detects the power-off within
        # `period` seconds and triggers redirect_audio_on_disconnect().
        try:
            while not self._stopping:
                await asyncio.sleep(period)
                with self._device_lock:
                    have_device = (
                        self.usb_device is not None
                        and self.device_config is not None
                        and self.device_config.status is not None
                    )
                if have_device:
                    try:
                        self.request_device_status()
                    except usb.core.USBError as e:
                        if getattr(e, 'errno', None) not in (16, 19, 110):
                            self.logger.warning(f"Status poll USB error: {e!r}")
                    except Exception as e:
                        self.logger.warning(f"Status poll failed: {e!r}")
        except asyncio.CancelledError:
            raise

    def _release_usb_handle(self) -> None:
        """Release the current libusb handle without performing a full teardown.

        Called when the same device re-enumerates on the USB bus (e.g. the
        Nova Pro Wireless DAC on boot, wake or replug). Without this, the
        stale handle keeps every interface claimed and subsequent transfers
        on the fresh handle fail with EBUSY (errno 16) indefinitely.
        """
        if self.usb_device is None:
            return

        # Stop the OLED manager first: it runs background threads that write
        # to the handle and would race with dispose_resources().
        if self.oled_manager is not None:
            self.logger.info("Stopping OLED manager before releasing stale USB handle")
            self.oled_manager.stop()
            self.oled_manager = None

        if self.device_config is not None:
            for interface in self._all_used_interfaces(self.device_config):
                try:
                    usb.util.release_interface(self.usb_device, interface)
                except usb.core.USBError:
                    pass  # interface may already be released or device gone

            # Return the kernel driver so the OS does not see a dangling claim.
            try:
                if usb.core.find(idVendor=self.device_config.vendor_id):
                    self.kernel_attach(self.usb_device, self.device_config)
            except usb.core.USBError as e:
                self.logger.warning(f"Could not re-attach kernel driver on handle release: {e}")

        # This is the critical call: it closes the underlying libusb file
        # descriptor and frees the interface claim so the next open succeeds.
        try:
            usb.util.dispose_resources(self.usb_device)
            self.logger.info("Stale USB handle released via dispose_resources")
        finally:
            self.usb_device = None

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

        # Stop all dynamic loopbacks before clearing device state so the
        # pw-loopback processes are cleanly terminated.
        try:
            self.loopback_manager.stop_all()
        except Exception as e:
            self.logger.warning(f"Error stopping loopbacks on teardown: {e}")

        device_state.clear()

        if self.oled_manager is not None:
            self.oled_manager.stop()
            self.oled_manager = None

        with self._device_lock:
            if self.usb_device is not None:
                try:
                    usb.util.dispose_resources(self.usb_device)
                except usb.core.USBError as e:
                    self.logger.warning(f"Error disposing USB resources on teardown: {e}")
            self.usb_device = None
            self.device_config = None
            self.device_status = None
            self._active_extra_dial_interfaces = []
        self._device_ready = False
        self._warned_no_out_endpoint = False
