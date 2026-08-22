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
from arctis_sound_manager.bug_reporter import (find_interface_sysfs_dir,
                                               kernel_driver_for_interface)
from arctis_sound_manager.config import (CommandTransport,
                                         DeviceConfiguration,
                                         load_device_configurations,
                                         parsed_status)
from arctis_sound_manager.constants import (PULSE_CHAT_NODE_NAME,
                                            PULSE_MEDIA_NODE_NAME,
                                            STEELSERIES_VENDOR_ID)
from arctis_sound_manager.loopback_manager import (LoopbackManager, make_specs,
                                                   current_pipewire_socket_signature)
from arctis_sound_manager.channel_volumes import load_channel_volumes
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


# Backoff before re-attempting a USB acquisition that failed with EACCES, in
# seconds. Covers the boot-time race between udev applying the access rights
# and the daemon claiming the device, without making a genuinely missing rules
# file take noticeably longer to report.
# How long ASM waits for udev to catch up before it decides a USB EACCES is a
# real permissions problem rather than a boot-time race.
#
# The first version of this stopped after 1+3+6 = 10 seconds. That is not
# enough on a machine where the dongle is enumerated early in a busy boot:
# udev had not applied the rules yet, ASM spent its whole budget, and the
# dialog appeared even though the rules on disk were perfectly valid — the
# exact symptom this retry was added to fix, still reported on a clean install
# months later (discussion #140, #190).
_USB_PERMISSION_RETRY_DELAYS = (1.0, 2.0, 4.0, 8.0, 15.0, 30.0)

# Once the budget above is spent the dialog is shown — the user does need to
# know — but ASM keeps trying at this interval instead of giving up forever.
#
# Giving up was the second half of the bug. The retry budget was only reset by
# a successful acquisition or by the device disappearing, so on the common
# setup where the dongle never leaves its port, a single slow boot meant the
# popup stayed until the next physical replug. Nothing else in the running
# system would ever re-check. With a slow watch, a late udev, a manual
# `udevadm trigger` or a rules install from any other window repairs ASM on
# its own, and the dialog the user is looking at stops being the only way out.
_USB_PERMISSION_WATCH_INTERVAL = 30.0

# Extra tokens a profile's update_sequence can use, for commands that carry a
# byte derived from the setting rather than the setting itself. The Nova Pro
# Omni's mic noise reduction needs both: an on/off byte telling the firmware
# which microphones are being adjusted, and a level the firmware requires to
# stay >= 1 even while that feature is off.
_DERIVED_TOKENS = {
    'value.enabled': lambda value: 0 if value == 0 else 1,
    'value.at_least_1': lambda value: max(1, value),
}


#: Auto mic-switch trigger, stored as an int in the ``micro_autoswitch`` setting.
_MIC_AUTOSWITCH_MODES = {0: 'off', 1: 'connection', 2: 'mute', 3: 'both'}


def resolve_mic_autoswitch_target(
    mode: str,
    key: str,
    online_var: str | None,
    is_online: bool,
    mic_muted: bool,
    alt_source: str,
) -> str | None:
    """Decide which source the Sonar Micro EQ input should switch to.

    Pure so it can be tested without a live device. Returns the target for
    ``micro_input_source`` — ``"__auto__"`` (headset mic) or *alt_source* — or
    ``None`` when *key* is not the trigger for *mode* (nothing to do). Returns
    ``None`` too when the feature is off or no alternate is configured, which is
    what keeps it inert on any headset that never reports the matching status.
    """
    if mode == 'off' or not alt_source:
        return None
    if mode == 'connection' and online_var is not None and key == online_var:
        return alt_source if not is_online else '__auto__'
    if mode == 'mute' and key == 'mic_status':
        return alt_source if mic_muted else '__auto__'
    if mode == 'both' and ((online_var is not None and key == online_var) or key == 'mic_status'):
        # Either condition engages the alternate; the headset mic returns only
        # when it is both connected and unmuted (community request).
        return alt_source if (not is_online or mic_muted) else '__auto__'
    return None


# ASM's slider scale, shared by every family: ten integer steps of half a
# decibel, 20 being 0 dB. Families differ only in what they call 0 dB
# (hardware_eq_zero), which send_eq_command shifts onto afterwards.
EQ_BAND_MIN = 0
EQ_BAND_MAX = 40
EQ_BAND_COUNT = 10


def sanitise_eq_bands(bands, logger) -> list[int] | None:
    """Ten in-domain integers, or None when the input cannot be trusted.

    The D-Bus surface checked that the payload was a list of ten items and
    nothing else, then persisted it and sent it on: an out-of-domain value was
    shifted by hardware_eq_zero and written to real headset firmware, and
    eq_bands.json replayed it at every daemon start (CHA-13). Both the D-Bus
    entry point and send_eq_command go through here, so a hand-edited or
    restored eq_bands.json is covered by the same check as a live call.
    """
    if not isinstance(bands, (list, tuple)) or len(bands) != EQ_BAND_COUNT:
        logger.warning("custom EQ: expected %d bands, got %r — not sending",
                       EQ_BAND_COUNT, bands)
        return None

    cleaned: list[int] = []
    clamped = False
    for band in bands:
        # bool is an int in Python; a True here would silently mean 1.
        if isinstance(band, bool) or not isinstance(band, (int, float)):
            logger.warning("custom EQ: band value %r is not a number — not sending",
                           band)
            return None
        value = int(round(band))
        if value < EQ_BAND_MIN or value > EQ_BAND_MAX:
            clamped = True
            value = max(EQ_BAND_MIN, min(EQ_BAND_MAX, value))
        cleaned.append(value)

    if clamped:
        logger.warning(
            "custom EQ: band values outside %d-%d were clamped before reaching "
            "the headset (got %r)", EQ_BAND_MIN, EQ_BAND_MAX, list(bands),
        )
    return cleaned


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
        # True once the fast retries are spent and the slow watch is armed,
        # so only one timer chain runs and the error is logged once.
        self._usb_permission_watching: bool = False
        # How many permission retries are still pending for this device. A
        # dongle plugged in before boot can be enumerated before its access
        # rights are in place, so the very first acquisition of the session
        # races udev and loses; see _schedule_usb_permission_retry().
        self._usb_permission_attempt: int = 0

        self.general_settings = GeneralSettings.read_from_file()

        self.logger = logging.getLogger('CoreEngine')
        self.pa_audio_manager = PulseAudioManager.get_instance()
        self.usb_devices_monitor = USBDevicesMonitor.get_instance()

        # Dynamic loopback manager: owns the pw-loopback child processes for
        # Arctis_Game / Arctis_Chat / Arctis_Media virtual sinks.
        self.loopback_manager = LoopbackManager()

        # Futures waiting on a specific reply opcode from listen_endpoint_loop
        # (e.g. read_hardware_eq()'s 0x32/0xA6 queries). Only that loop ever
        # reads the listen endpoint, so a request/response caller must not
        # open a second reader on it — racing the daemon's own continuous
        # status polling for whichever packet the kernel hands out next would
        # lose packets on both sides. Registering a future here and letting
        # the existing reader resolve it keeps there being exactly one
        # reader.
        self._raw_response_waiters: dict[int, list[asyncio.Future]] = {}

        # EQ mode the on-device equaliser was last set for ("sonar"/"custom"),
        # so reconcile_hardware_eq_mode() writes on a change and stays quiet
        # otherwise. None until the first reconciliation.
        self._applied_eq_mode: str | None = None

        # Readiness tracking for the periodic re-scan (issue #76).
        # A device present at boot fires no udev 'add' event; these flags let
        # loop() retry detection until the USB/ALSA stack is fully ready.
        self._device_ready: bool = False
        self._detect_lock = threading.Lock()   # serialises detection attempts
        self._rescan_in_flight: bool = False
        self._logged_no_device: bool = False   # throttle the "no device" warning
        self._warned_no_out_endpoint: bool = False  # log once per device attach
        self._last_recreate_loopbacks: float = 0.0  # debounce rapid D-Bus calls
        # Serialises Spatial Audio applies (#169): a filter-chain restart is
        # blocking, so a second slider-driven call arriving mid-restart is
        # dropped rather than overlapped — the conf already reflects the latest
        # JSON, so the in-flight restart loads it.
        self._spatial_apply_lock = threading.Lock()

        # Channels whose persisted virtual-sink volume still needs to be
        # re-asserted after a (re)creation, mapped to remaining retry ticks
        # (issue #134). Populated by setup_loopbacks and the watchdog's
        # dead-process pass; drained by the watchdog once each sink appears.
        self._volume_restore_pending: dict[str, int] = {}

        # Incremented every time the loopback set is torn down and rebuilt for
        # a device (attach, reconnect, teardown). The watchdog's per-channel
        # anti-flap state is keyed by channel *name*, which outlives the
        # processes it describes; comparing this counter lets it tell "the same
        # troubled channel" from "a brand new process that happens to have the
        # same name" and reset accordingly.
        self._device_session_id: int = 0

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
        # Every loopback process is about to be replaced: this is a new device
        # session. Bumping the counter tells the watchdog to drop the anti-flap
        # bookkeeping it accumulated for the *previous* session's processes —
        # see _loopback_watchdog. Without it, a channel that flapped shortly
        # before the headset was switched off stays in cooldown for up to five
        # minutes after it comes back, silently skipped even though its process
        # is brand new.
        self._device_session_id += 1
        try:
            self.loopback_manager.recreate_all(specs)
            self.logger.info(
                "setup_loopbacks: loopbacks recreated (sonar=%s, game=%s, chat=%s)",
                sonar, physical_game, physical_chat,
            )
            self._link_loopbacks(specs)
            # The fresh pw-loopback sinks come up at 100%; queue a restore of
            # each channel's persisted level so the watchdog re-asserts it once
            # the sink appears (issue #134).
            self._queue_volume_restore(spec.channel for spec in specs)
            # This is the headset attach/reconnect path: all three sinks were
            # just destroyed and recreated with new indices, so every app the
            # user had pinned to them (routing_overrides.json) has fallen back
            # to the system default. Nothing replayed those pins here — only a
            # Chat recreate in the watchdog did — so after a replug, Firefox,
            # games and video players silently stopped following the headset.
            try:
                from arctis_sound_manager.pw_utils import reapply_routing_overrides
                reapply_routing_overrides()
            except Exception as exc:
                self.logger.warning(
                    "setup_loopbacks: could not reapply routing overrides: %r", exc
                )
        except Exception as exc:
            self.logger.error("setup_loopbacks: failed to recreate loopbacks: %r", exc)

    # Ticks the watchdog keeps retrying a channel's volume restore before giving
    # up. At the 5 s watchdog cadence this is a ~30 s window, comfortably longer
    # than the moment a pw-loopback sink takes to appear after being spawned.
    _VOLUME_RESTORE_TICKS: int = 6

    def _queue_volume_restore(self, channels) -> None:
        """Mark *channels* as needing their persisted volume re-asserted.

        The pw-loopback sink for a freshly (re)created channel is not in the
        graph immediately, so we cannot set its volume here; instead we record
        it and let the watchdog apply the saved level once the sink appears
        (issue #134). Channels with no saved volume are still queued — the
        restore pass simply no-ops them, which is cheaper than filtering here.
        """
        for channel in channels:
            self._volume_restore_pending[channel] = self._VOLUME_RESTORE_TICKS

    def _restore_channel_volumes(self, channels) -> set[str]:
        """Re-apply persisted virtual-sink volumes for *channels*.

        Returns the subset of *channels* whose sink was not present yet (so the
        caller can retry on a later tick). A channel with no saved volume, or
        no known loopback spec, is treated as done (not returned) — there is
        nothing to restore and nothing to wait for.

        Only fires on discrete (re)creation events, never continuously, so it
        re-asserts the user's level after ASM churns the sink without fighting a
        deliberate change the user makes from the system mixer afterwards.
        """
        saved = load_channel_volumes()
        specs = self.loopback_manager.specs()
        still_pending: set[str] = set()
        for channel in channels:
            spec = specs.get(channel)
            if spec is None:
                continue
            pct = saved.get(spec.capture_name)
            if pct is None:
                continue
            if not self.pa_audio_manager.set_sink_volume_by_node(spec.capture_name, pct):
                still_pending.add(channel)
            else:
                self.logger.info(
                    "restored volume %d%% on %s (issue #134)", pct, spec.capture_name,
                )
        return still_pending

    def _process_volume_restore(self) -> None:
        """Drain :attr:`_volume_restore_pending` — one watchdog tick's worth.

        Applies each queued channel's saved volume; keeps the ones whose sink is
        still absent (decrementing their retry budget) and drops the rest.
        """
        if not self._volume_restore_pending:
            return
        pending = list(self._volume_restore_pending)
        still = self._restore_channel_volumes(pending)
        for channel in pending:
            if channel in still:
                self._volume_restore_pending[channel] -= 1
                if self._volume_restore_pending[channel] <= 0:
                    del self._volume_restore_pending[channel]
            else:
                del self._volume_restore_pending[channel]

    def _link_loopbacks(self, specs, attempts: int = 6, delay: float = 0.2) -> None:
        """Establish the ASM-owned playback→EQ links for *specs* (issue #100).

        The loopbacks run with ``node.autoconnect=false``, so WirePlumber never
        links them and nothing gets routed until ASM creates the links. The
        ``pw-loopback`` nodes appear in the graph a short moment after their
        processes are spawned, so we retry briefly until each links (or give up
        and leave it to the watchdog, which owns these links durably). This just
        avoids a ~5 s silence between plugging the headset in and the first
        watchdog tick.

        Best-effort and synchronous: callers reach this off the asyncio event
        loop (daemon start, or the D-Bus reload/recreate handlers dispatched via
        run_in_executor), so the short sleeps here never stall the loop.
        """
        from arctis_sound_manager.pw_utils import ensure_loopback_link
        pending = {s.playback_name: s.target for s in specs if getattr(s, "target", None)}
        for _ in range(max(1, attempts)):
            if not pending:
                break
            for pb_name, target in list(pending.items()):
                try:
                    if ensure_loopback_link(pb_name, target):
                        pending.pop(pb_name, None)
                except Exception:
                    pass
            if pending:
                time.sleep(delay)
        if pending:
            self.logger.info(
                "_link_loopbacks: %d loopback(s) not linkable yet, leaving to "
                "watchdog: %s", len(pending), list(pending),
            )

    _MICRO_CAPTURE_OUT = "effect_output.sonar-micro-eq"

    def _claim_default_source(self) -> None:
        """Make the Sonar Micro EQ the system's default input — if it carries a mic.

        This used to fire unconditionally at every device init, and that is how
        a machine ends up with a microphone that records nothing: the micro EQ
        chain is only a microphone once ASM has linked one into it, and there
        are two ordinary cases where it hasn't.

        - ``micro_input_source`` is ``"__manual__"``: the user routes the chain
          themselves, so ASM has no business deciding what the default input is
          either — taking it over contradicts the very setting that told us to
          keep our hands off.
        - The configured source is not in the graph (unplugged, or a name that
          no longer resolves): the chain is fed by nothing, and pointing the
          default input at it makes every app pick a silent mic.

        In both cases the default input is left exactly where the user (or
        WirePlumber) put it, and a later device init retries — by then the
        chain usually has its source and the takeover is correct.
        """
        from arctis_sound_manager.sonar_to_pipewire import resolve_micro_input_source

        try:
            source = resolve_micro_input_source()
        except Exception as exc:
            self.logger.warning("Could not resolve the micro EQ input source: %r", exc)
            return

        if not source:
            self.logger.info(
                "Leaving the default input alone: nothing is feeding %s "
                "(manual routing, or no microphone attached)",
                self._MICRO_CAPTURE_OUT,
            )
            return
        if not self.pa_audio_manager.has_source(source):
            self.logger.info(
                "Leaving the default input alone: '%s' feeds %s but is not in "
                "the graph", source, self._MICRO_CAPTURE_OUT,
            )
            return

        self.pa_audio_manager.set_default_source(self._MICRO_CAPTURE_OUT)

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
            recreated = [s for s in specs if s.channel != "chat"]
            for spec in recreated:
                self.loopback_manager.recreate(spec)  # keep Arctis_Chat alive — Discord-safe
            self._link_loopbacks(recreated)

            self.logger.info(
                "recreate_loopbacks_game_media: game+media recreated, chat preserved"
            )
        except Exception as exc:
            self.logger.error("recreate_loopbacks_game_media: unexpected error: %r", exc)

    def recreate_loopback_single(self, channel: str) -> None:
        """Recreate the loopback for a single channel, leaving all others intact.

        Used when only one EQ channel's preset changed — avoids disrupting the
        sibling channel's audio stream (e.g. editing Media EQ no longer cuts Game).
        Chat is never recreated via this path: it auto-reconnects after a
        filter-chain restart without process teardown.

        Uses the same debounce timestamp as recreate_loopbacks_game_media so
        rapid successive per-channel calls are also throttled.
        """
        if channel not in ("game", "media"):
            self.logger.debug(
                "recreate_loopback_single: channel=%r has no Arctis_* loopback, skipping",
                channel,
            )
            return
        now = time.monotonic()
        elapsed = now - self._last_recreate_loopbacks
        if elapsed < self._RECREATE_DEBOUNCE_S:
            self.logger.debug(
                "recreate_loopback_single(%s): debounced (%.1f s since last call)",
                channel, elapsed,
            )
            return
        self._last_recreate_loopbacks = now
        self.logger.info("recreate_loopback_single: channel=%r requested via D-Bus", channel)
        try:
            if not device_state.is_device_set():
                self.logger.info("recreate_loopback_single: no device, skipping")
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
                if spec.channel == channel:
                    self.loopback_manager.recreate(spec)
                    self._link_loopbacks([spec])
                    self.logger.info(
                        "recreate_loopback_single: channel=%r recreated", channel,
                    )
                    return
            self.logger.warning("recreate_loopback_single: spec for channel=%r not found", channel)
        except Exception as exc:
            self.logger.error("recreate_loopback_single: unexpected error: %r", exc)

    def apply_spatial_audio(self) -> None:
        """Regenerate the HeSuVi chains from saved Spatial Audio JSON and, if a
        conf changed, restart the filter-chain so PipeWire applies it (#169).

        The GUI can't do this itself: its process has no device registered, so
        ``generate_hesuvi_conf()`` no-ops there — moving an Immersion/Distance
        slider only rewrites sonar_spatial_audio*.json. This daemon-side call
        (device attached) rebuilds the on-disk conf(s) and, only when something
        actually changed, restarts the ``filter-chain`` service to reload them.
        Runs off the D-Bus event loop (blocking restart). No-op when no device
        is attached or when the confs already match the JSON.
        """
        if not device_state.is_device_set():
            return
        if not self._spatial_apply_lock.acquire(blocking=False):
            # An apply is already restarting the filter-chain; it will pick up
            # the latest conf (regenerated from the newest JSON). Drop this one.
            self.logger.debug("apply_spatial_audio: already in flight — skipping")
            return
        try:
            from arctis_sound_manager.sonar_to_pipewire import apply_spatial_audio_change
            # Regenerates the HeSuVi confs and, only if one changed, restarts the
            # filter-chain (quiesced/#100-safe) and re-owns the links. All the
            # filter-chain knowledge (quiesce, crash-loop → safe mode) lives in
            # that helper, alongside apply_hrir_choice's identical restart path.
            if apply_spatial_audio_change():
                self.logger.info("Spatial Audio changed — filter-chain restarted to apply (#169)")
        except Exception as exc:
            self.logger.warning("apply_spatial_audio failed: %r", exc)
        finally:
            self._spatial_apply_lock.release()

    async def _loopback_watchdog(self) -> None:
        """Periodically check for dead or mislinked loopback processes.

        Runs as an asyncio ``Task`` alongside ``core_loop``.  Every 5 seconds,
        if a device is currently connected (``device_state.is_device_set()``),
        performs three checks in order:

        0. **Socket change detection** — compares the current PipeWire socket
           path to the one seen on the previous tick.  If it differs (e.g.
           Gamescope / Steam Game Mode session switch under Distrobox), all
           loopbacks are recreated immediately and the rest of the tick is
           skipped so they have one full cycle to bind to the new socket.
        1. **Dead-process pass** — calls :meth:`LoopbackManager.restart_dead`
           to revive any crashed ``pw-loopback`` processes.
        2. **Link-enforcement pass** — for every loopback that was *not* just
           restarted and is still running, calls
           :func:`~arctis_sound_manager.pw_utils.ensure_loopback_link` to make
           sure the playback node is linked, channel-for-channel, to its EQ
           target.  Because the loopbacks run with ``node.autoconnect=false``
           (issue #100), WirePlumber never links or moves them: ASM owns the
           links.  A loopback can therefore only be *correctly linked* or
           *not-yet-linked* — never "mislinked to a physical DAC" — so no
           competing output device (a second USB DAC, the default sink, …) can
           ever steal it, and there is no WirePlumber tug-of-war to fight.  When
           the link cannot be established the loopback is treated as an orphan:
           it is given ``_ORPHAN_GRACE_TICKS`` consecutive failing ticks (15 s at
           the default interval) before action, because a one-tick failure is a
           normal transient (e.g. the surround chain rebuilding when Spatial
           Audio toggles).  After the grace period, if the target EQ node is
           absent the filter-chain is assumed dead and
           :func:`~arctis_sound_manager.sonar_to_pipewire.ensure_filter_chain_healthy`
           is called instead of a pointless recreate; otherwise the loopback is
           recreated.

        **Anti-flapping guard** — a recreate is still a heavyweight action, so
        the watchdog tracks recent interventions per channel and applies an
        exponential-backoff cooldown when a channel is recreated too often.
        During cooldown the channel is skipped entirely (no restart, no
        recreate).  A muted-but-stable loopback is less disruptive than constant
        cuts.

        The coroutine exits cleanly when ``self._stopping`` is set (by
        :meth:`stop`) or when the task is cancelled (by the daemon shutdown
        handler).  Errors are always caught and logged — this coroutine must
        never crash the daemon.
        """
        from arctis_sound_manager.pw_utils import ensure_loopback_link, pw_dump_or_none

        _WATCHDOG_INTERVAL: float = 5.0
        # Number of consecutive ticks a loopback may be None-linked before we
        # treat it as a permanent orphan and recreate it.  One tick = 5 s, so
        # 3 ticks = 15 s of grace before action.  Transient None states (one
        # PipeWire graph cycle) are ignored entirely.
        # A loopback that cannot be linked to its EQ target is treated the same
        # way: with node.autoconnect=false (issue #100) an unlinked loopback is
        # the only failure mode, and a single failing tick is a normal transient
        # (e.g. the Sonar EQ → HeSuVi surround chain being rebuilt on a Spatial
        # Audio toggle), so it must NOT trigger a recreate that would itself
        # churn the graph and silence the channel.
        _ORPHAN_GRACE_TICKS: int = 3
        # Consecutive permission refusals before a channel's EQ node is
        # degraded to a pickable Audio/Sink (#203). Deliberately longer than
        # the orphan grace: grant_link_permissions() retries on its own
        # schedule, and the class change is visible to the user, so it must be
        # the answer to a settled state and not to one bad tick.
        _PERM_FALLBACK_TICKS: int = 6

        # ── Anti-flapping constants ───────────────────────────────────────────
        # Threshold: how many interventions (restart OR recreate) within the
        # observation window trigger a cooldown.
        _FLAP_THRESHOLD: int = 3
        # Rolling window for counting recent interventions (seconds).
        # Correctif 3 (issue #88): raised from 30 → 60 s so that 3 orphan
        # recreations spaced ~15-16 s apart (3 ticks × 5 s grace + overhead)
        # all fall within the window and correctly trigger the cooldown.
        # At 30 s only 2 of those recreations would fit, never reaching the
        # threshold of 3 and letting the recreate loop run indefinitely.
        _FLAP_WINDOW: float = 60.0
        # First cooldown applied when flapping is detected (seconds).
        _COOLDOWN_BASE: float = 60.0
        # Maximum cooldown; repeated flapping doubles up to this cap (seconds).
        _COOLDOWN_MAX: float = 300.0

        # ── Per-channel state ─────────────────────────────────────────────────
        # Per-channel count of consecutive ticks where the loopback could not be
        # linked to its EQ target.  Reset to 0 as soon as the link succeeds or
        # the loopback is restarted.
        _none_ticks: dict[str, int] = {}
        # Channels whose links PipeWire is refusing on permissions, and for
        # which the explanation has already been logged. Without this the line
        # would repeat every tick for as long as the condition lasts.
        _perm_denied_logged: set[str] = set()
        _perm_denied_ticks: dict[str, int] = {}
        # Timestamps of recent interventions per channel (monotonic clock).
        _flap_history: dict[str, list[float]] = {}
        # Monotonic timestamp past which the channel is in cooldown.
        _cooldown_until: dict[str, float] = {}
        # Current cooldown duration per channel (doubles on each new flap event,
        # capped at _COOLDOWN_MAX; resets toward base after the channel is healthy).
        _cooldown_dur: dict[str, float] = {}
        # Whether we have already emitted the "skipping (in cooldown)" log line
        # for the current cooldown period (avoid per-tick log spam).
        _cooldown_logged: dict[str, bool] = {}

        # ── Target-absent tracking (issue #88 Correctif 3) ───────────────────
        # Per-channel count of consecutive ticks where the loopback is orphaned
        # AND the expected target EQ node (e.g. effect_input.sonar-game-eq) is
        # absent from the PipeWire graph (= filter-chain is dead/crash-looping).
        # Recreating an orphan when the target doesn't exist is pointless; after
        # enough such ticks we call ensure_filter_chain_healthy() instead.
        _target_absent_ticks: dict[str, int] = {}
        # How many consecutive "orphan + target absent" ticks before we escalate
        # to ensure_filter_chain_healthy().  Each tick = _WATCHDOG_INTERVAL s.
        _TARGET_ABSENT_TICKS: int = 3

        # ── PipeWire socket tracking (issue #90) ─────────────────────────────
        # Last-known PipeWire socket path seen by the watchdog.  ``None`` means
        # "not yet initialised" (first tick with a device present).  Changes in
        # this value indicate a session switch (e.g. Gamescope / Steam Game Mode
        # under Distrobox) that requires all loopbacks to be recreated so they
        # reconnect to the new socket rather than hanging on the stale one.
        _pw_socket_sig: str | None = None

        # ── Device-session tracking ──────────────────────────────────────────
        # All the anti-flap bookkeeping above is keyed by channel *name*, but a
        # channel name outlives the process it describes: teardown/attach
        # replaces every pw-loopback while "game"/"chat"/"media" stay the same
        # strings. A channel that flapped just before the headset was switched
        # off would therefore stay in cooldown after it came back — skipped by
        # both restart_dead() and the link pass for up to _COOLDOWN_MAX, with
        # nothing in the logs to suggest the device underneath had changed.
        # CoreEngine bumps _device_session_id whenever it rebuilds the loopback
        # set; a change here means the previous session's history describes
        # processes that no longer exist.
        _session_seen: int | None = None

        # ── Last-hop enforcement failures ────────────────────────────────────
        # The loopback→EQ pass has a grace period and escalates to
        # recreate()/ensure_filter_chain_healthy(). The three "last hop" passes
        # (spatial EQ, physical output, micro capture) had nothing: they logged
        # and retried forever, with no counter and no ceiling, so sustained
        # trouble had no fallback at all. Count consecutive failures per hop and
        # escalate once, then reset so escalation cannot loop.
        _hop_fail_ticks: dict[str, int] = {}
        _HOP_FAIL_TICKS: int = 6  # ~30 s at the 5 s watchdog cadence

        try:
            while not self._stopping:
                await asyncio.sleep(_WATCHDOG_INTERVAL)
                if self._stopping:
                    break
                if not device_state.is_device_set():
                    # No device — no loopbacks to watch.
                    continue

                now = time.monotonic()

                # New device session → the anti-flap history describes processes
                # that no longer exist. Drop it, so a channel that was cooling
                # down before the headset went away starts clean now that it has
                # been rebuilt. This only fires on an actual rebuild of the
                # loopback set, never per tick, so flapping *within* a session
                # still backs off exactly as before (issue #90).
                _session_now = self._device_session_id
                if _session_seen is None:
                    _session_seen = _session_now
                elif _session_now != _session_seen:
                    _session_seen = _session_now
                    if _cooldown_until or _flap_history or _none_ticks:
                        self.logger.info(
                            "_loopback_watchdog: new device session (#%d) — clearing "
                            "anti-flap state from the previous one (was cooling: %s)",
                            _session_now, sorted(_cooldown_until) or "none",
                        )
                    _none_ticks.clear()
                    _flap_history.clear()
                    _cooldown_until.clear()
                    _cooldown_dur.clear()
                    _cooldown_logged.clear()
                    _target_absent_ticks.clear()

                # Channels currently in cooldown — passed to restart_dead so that
                # a dead process in cooldown is NOT revived this tick.
                cooled_channels: set[str] = {
                    ch for ch, until in _cooldown_until.items() if now < until
                }

                # Set by any pass that rebuilds a loopback sink this tick.
                # Rebuilding one destroys the sink the user's apps were pinned
                # to, so their routing overrides must be replayed; a single pass
                # restores every pin, hence one flag drained once per tick
                # rather than a call per channel.
                _overrides_needed = False

                def _record_intervention(channel: str) -> None:
                    """Record one intervention, purge stale history, trigger cooldown if flapping."""
                    history = _flap_history.setdefault(channel, [])
                    history.append(now)
                    # Purge entries older than the observation window.
                    cutoff = now - _FLAP_WINDOW
                    _flap_history[channel] = [t for t in history if t >= cutoff]
                    if len(_flap_history[channel]) >= _FLAP_THRESHOLD:
                        dur = _cooldown_dur.get(channel, _COOLDOWN_BASE)
                        _cooldown_until[channel] = now + dur
                        # Double the duration for the next flap event (exponential
                        # backoff), capped at _COOLDOWN_MAX.
                        _cooldown_dur[channel] = min(dur * 2.0, _COOLDOWN_MAX)
                        self.logger.warning(
                            "_loopback_watchdog: loopback '%s' flapping "
                            "(%d recreations/%ds) — backing off for %ds; "
                            "audio for this channel may be degraded but stable",
                            channel,
                            len(_flap_history[channel]),
                            int(_FLAP_WINDOW),
                            int(dur),
                        )
                        # Clear history so the window starts fresh after cooldown.
                        _flap_history[channel] = []
                        _cooldown_logged[channel] = False

                # Channels that have exited their cooldown since the last tick:
                # gradually reset their cooldown duration back toward base so a
                # channel that was flapping but became healthy doesn't stay at a
                # high backoff forever.
                for ch in list(_cooldown_until.keys()):
                    if now >= _cooldown_until[ch]:
                        # Cooldown expired — halve the duration back toward base
                        # (but keep the key so the next flap doesn't restart cold).
                        current = _cooldown_dur.get(ch, _COOLDOWN_BASE)
                        _cooldown_dur[ch] = max(current / 2.0, _COOLDOWN_BASE)

                # ── PipeWire socket change detection (issue #90) ──────────────
                # Under Gamescope / Steam Game Mode with Distrobox, the PipeWire
                # socket can change when the session switches (e.g. Desktop ↔
                # Game Mode).  pw-loopback processes that were spawned against the
                # old socket stop routing audio until they are restarted against
                # the new one.  We detect this by comparing the current socket
                # signature to the one seen on the previous tick; when it changes
                # we recreate all loopbacks immediately and skip the rest of this
                # tick so they get one full cycle to bind.
                try:
                    _new_sig = current_pipewire_socket_signature()
                    if _pw_socket_sig is None:
                        # First tick with a device present — establish the
                        # baseline without triggering a recreate (the socket
                        # hasn't *changed* yet relative to any prior state).
                        _pw_socket_sig = _new_sig
                    elif _new_sig != _pw_socket_sig:
                        _old_sig = _pw_socket_sig
                        _pw_socket_sig = _new_sig
                        self.logger.warning(
                            "_loopback_watchdog: PipeWire socket changed "
                            "(%r → %r) — recreating all loopbacks to rebind "
                            "to new socket",
                            _old_sig, _new_sig,
                        )
                        _specs = list(self.loopback_manager.specs().values())
                        self.loopback_manager.recreate_all(_specs)
                        # Recreated sinks come up at 100%; queue their saved
                        # levels for the next tick (issue #134).
                        self._queue_volume_restore(s.channel for s in _specs)
                        # Skip dead/mislink passes this tick: give the new
                        # loopbacks one watchdog cycle to bind before we inspect
                        # them (avoids immediate false-positive orphan/mislink).
                        continue
                except Exception as exc:
                    self.logger.error(
                        "_loopback_watchdog: error checking PipeWire socket "
                        "signature: %r", exc,
                    )

                try:
                    restarted = self.loopback_manager.restart_dead(
                        skip_channels=cooled_channels if cooled_channels else None
                    )
                    if restarted:
                        self.logger.warning(
                            "_loopback_watchdog: restarted dead loopback(s): %s",
                            restarted,
                        )
                        for ch in restarted:
                            _none_ticks.pop(ch, None)
                            _record_intervention(ch)
                            # A revived loopback comes back at 100%; re-assert the
                            # user's saved level once its sink reappears (#134).
                            self._volume_restore_pending[ch] = self._VOLUME_RESTORE_TICKS
                        # A revived loopback also comes back with a *new* sink,
                        # so the user's app→sink pins were dropped with the old
                        # one. This path never restored them.
                        _overrides_needed = True
                    # Log once per cooldown period for any channel we are skipping.
                    for ch in cooled_channels:
                        if not _cooldown_logged.get(ch, False):
                            remaining = int(_cooldown_until[ch] - now)
                            self.logger.info(
                                "_loopback_watchdog: loopback '%s' in anti-flap "
                                "cooldown — skipping for ~%ds more",
                                ch, remaining,
                            )
                            _cooldown_logged[ch] = True
                except Exception as exc:
                    self.logger.error(
                        "_loopback_watchdog: unexpected error in restart_dead: %r", exc
                    )
                    continue

                # Volume-restore pass: re-assert saved virtual-sink levels for any
                # channel that was just (re)created, once its sink is back in the
                # graph (issue #134). Best-effort — never break the watchdog.
                try:
                    self._process_volume_restore()
                except Exception as exc:
                    self.logger.error(
                        "_loopback_watchdog: error restoring channel volumes: %r", exc
                    )

                # Link-enforcement pass: make sure every running loopback's
                # playback node is linked to its EQ target. Because the loopbacks
                # run with node.autoconnect=false (issue #100), ASM owns these
                # links — WirePlumber never creates or moves them, so a loopback
                # can only be either correctly linked or not-yet-linked, never
                # "mislinked to a physical DAC". One pw-dump is shared across all
                # channels this tick. Skip channels that were just restarted —
                # give them one tick to appear in the graph.
                link_data = None  # guards the spatial-link pass below if pw-dump itself raises
                try:
                    link_data = await asyncio.get_running_loop().run_in_executor(
                        None, pw_dump_or_none,
                    )
                    if link_data is None:
                        # Could not read the graph — not "the graph is empty"
                        # (CHA-11). Every channel would look unlinkable, the
                        # orphan counters would run up and the watchdog would
                        # recreate loopbacks and restart the filter-chain over
                        # an audio path that is fine. Skip the tick instead:
                        # the counters keep their state and the next tick, five
                        # seconds later, decides on real information.
                        self.logger.warning(
                            "_loopback_watchdog: pw-dump unreadable this tick — "
                            "skipping, rather than treating the graph as empty",
                        )
                        continue
                    for channel, spec in self.loopback_manager.specs().items():
                        if channel in cooled_channels:
                            # In anti-flap cooldown — do not intervene this tick.
                            continue
                        if channel in restarted:
                            # Just recreated — give it one tick to appear in the
                            # graph, then the next tick will link it.
                            _none_ticks.pop(channel, None)
                            continue
                        if not self.loopback_manager.is_running(channel):
                            continue
                        # ASM owns the link (node.autoconnect=false, issue #100):
                        # (re)create the channel-matched playback→EQ port links
                        # directly. Idempotent — a no-op when already linked, and
                        # it tears down any stray link. This replaces the old
                        # pw-metadata relink that fought WirePlumber's policy; with
                        # autoconnect off there is nothing to fight.
                        link_outcome: dict = {}
                        linked = await asyncio.get_running_loop().run_in_executor(
                            None,
                            ensure_loopback_link,
                            spec.playback_name,
                            spec.target,
                            link_data,
                            link_outcome,
                        )
                        if linked:
                            _none_ticks.pop(channel, None)
                            _target_absent_ticks.pop(channel, None)
                            continue

                        # PipeWire refused the link on permissions, it did not
                        # fail to find the nodes. Recreating the loopback is the
                        # wrong answer twice over: the new client comes up just
                        # as restricted, and the recreation drops the channels
                        # that *were* linked — which is how "no sound on Game"
                        # turned into sound for thirty seconds, then one ear,
                        # then silence, on a loop, every five seconds (#181).
                        # Leave the loopback alone; grant_link_permissions()
                        # retries the repair on its own schedule.
                        if link_outcome.get("denied"):
                            _none_ticks.pop(channel, None)
                            _target_absent_ticks.pop(channel, None)
                            if channel not in _perm_denied_logged:
                                _perm_denied_logged.add(channel)
                                self.logger.warning(
                                    "_loopback_watchdog: PipeWire refused %d of %d "
                                    "links for '%s' on permissions — not recreating "
                                    "it, that would only drop what still plays. "
                                    "This system starts our clients restricted (#181).",
                                    link_outcome.get("denied"),
                                    link_outcome.get("total"), channel,
                                )
                            # After the permission repair has had its chances and
                            # the link is still refused, degrade the channel's EQ
                            # node from Audio/Sink/Internal to a plain Audio/Sink
                            # (issue #203): PipeWire allows the identical
                            # cross-client link into a non-Internal node on the
                            # sessions that refuse it into an Internal one. Done
                            # per channel, only after repeated refusals, and
                            # remembered — a session that links fine (the normal
                            # case, measured) never reaches this and keeps its
                            # plumbing out of the output pickers.
                            count = _perm_denied_ticks.get(channel, 0) + 1
                            _perm_denied_ticks[channel] = count
                            if count >= _PERM_FALLBACK_TICKS:
                                _perm_denied_ticks.pop(channel, None)
                                await self._degrade_channel_media_class(channel)
                            continue
                        _perm_denied_ticks.pop(channel, None)
                        _perm_denied_logged.discard(channel)

                        # Could not link: the loopback node is not in the graph yet,
                        # or the target EQ node is absent (filter-chain still
                        # starting, or dead). Apply the orphan grace so a one-tick
                        # transient (e.g. the surround chain rebuilding on a Spatial
                        # Audio toggle) never triggers a churn-inducing recreate.
                        count = _none_ticks.get(channel, 0) + 1
                        _none_ticks[channel] = count
                        if count < _ORPHAN_GRACE_TICKS:
                            continue
                        _none_ticks.pop(channel, None)
                        self.logger.warning(
                            "_loopback_watchdog: loopback '%s' unlinkable for %d "
                            "ticks — checking target", channel, count,
                        )

                        # Correctif 3 (issue #88): if the expected target EQ node is
                        # absent from the PipeWire graph, the filter-chain is
                        # dead/crash-looping and recreating the loopback is pointless
                        # — it would just re-orphan. Count "target absent" ticks and
                        # escalate to ensure_filter_chain_healthy() so safe mode can
                        # be armed.
                        if spec.target:
                            from arctis_sound_manager.pw_utils import pw_node_exists
                            target_exists = await asyncio.get_running_loop().run_in_executor(
                                None, pw_node_exists, spec.target,
                            )
                            if not target_exists:
                                ta_count = _target_absent_ticks.get(channel, 0) + 1
                                _target_absent_ticks[channel] = ta_count
                                self.logger.warning(
                                    "_loopback_watchdog: loopback '%s' unlinkable and "
                                    "target '%s' absent from PW graph (filter-chain "
                                    "dead?) — ticks=%d, skipping recreate",
                                    channel, spec.target, ta_count,
                                )
                                if ta_count >= _TARGET_ABSENT_TICKS:
                                    _target_absent_ticks.pop(channel, None)
                                    try:
                                        from arctis_sound_manager.sonar_to_pipewire import (
                                            ensure_filter_chain_healthy,
                                            ensure_sonar_eq_configs,
                                            _restart_filter_chain,
                                        )
                                        # A target node is absent for one of two
                                        # reasons: (a) its sonar-*-eq.conf is simply
                                        # missing — never written, or moved aside —
                                        # so no amount of restarting a healthy
                                        # filter-chain will bring it back; or (b) the
                                        # filter-chain is genuinely crash-looping.
                                        # Try (a) first: regenerate any missing config
                                        # and restart so PipeWire loads it (#111/#88).
                                        # Only if there was nothing to regenerate is
                                        # this a real crash-loop → hand off to the
                                        # safe-mode handler.
                                        regenerated = await asyncio.get_running_loop().run_in_executor(
                                            None, ensure_sonar_eq_configs,
                                        )
                                        if regenerated:
                                            self.logger.warning(
                                                "_loopback_watchdog: target '%s' absent — "
                                                "regenerated missing EQ config(s), "
                                                "restarting filter-chain to load them",
                                                spec.target,
                                            )
                                            await asyncio.get_running_loop().run_in_executor(
                                                None, _restart_filter_chain,
                                            )
                                        else:
                                            self.logger.warning(
                                                "_loopback_watchdog: target '%s' absent for "
                                                "%d ticks and no config to regenerate — "
                                                "calling ensure_filter_chain_healthy()",
                                                spec.target, ta_count,
                                            )
                                            await asyncio.get_running_loop().run_in_executor(
                                                None, ensure_filter_chain_healthy,
                                            )
                                    except Exception as _ehc_exc:
                                        self.logger.error(
                                            "_loopback_watchdog: config regen / health "
                                            "check failed: %r", _ehc_exc
                                        )
                                continue  # do NOT recreate — target doesn't exist yet
                            else:
                                _target_absent_ticks.pop(channel, None)
                                self.logger.warning(
                                    "_loopback_watchdog: loopback '%s' unlinkable for "
                                    "%d ticks (target present) — recreating",
                                    channel, count,
                                )

                        self.loopback_manager.recreate(spec)
                        _record_intervention(channel)
                        # Recreating a loopback destroys and rebuilds its sink,
                        # so every stream the user had pinned to it falls back
                        # to the system default. This used to run for "chat"
                        # only — Discord being the reported case — leaving pins
                        # on Game and Media silently dropped. Any channel needs
                        # it, and one pass restores them all, so it is done once
                        # per tick no matter how many channels were rebuilt.
                        _overrides_needed = True
                except Exception as exc:
                    self.logger.error(
                        "_loopback_watchdog: unexpected error in mislink check: %r", exc
                    )

                # ── Spatial EQ output link-enforcement (Phase 3, #100/#88) ───
                # effect_output.sonar-{game,media}-eq run with
                # node.autoconnect=false — the exact same tug-of-war fix as
                # the loopback playback nodes above (issue #100): ASM must own
                # this link too, since WirePlumber will never create or move
                # it. This keeps the link in sync with the Spatial Audio
                # toggle even across an out-of-band filter-chain restart (HRIR
                # change, crash recovery, …) that recreated the node with
                # nothing linked into it yet. Reuses link_data from the pass
                # above when available; best-effort otherwise (a fresh
                # pw-dump is cheap and this call never restarts anything).
                async def _enforce_hop(hop: str, fn, *lead_args) -> None:
                    await self._enforce_link_hop(
                        hop, fn, lead_args, link_data,
                        _hop_fail_ticks, _HOP_FAIL_TICKS,
                    )

                from arctis_sound_manager.sonar_to_pipewire import ensure_spatial_eq_links
                await _enforce_hop("spatial EQ", ensure_spatial_eq_links, ("game", "media"))

                # ── Physical output link-enforcement (headset power-cycle) ───
                # effect_output.sonar-chat-eq and effect_output.virtual-
                # surround-7.1-hesuvi both carry a node.target hint at the
                # physical Arctis output, but that hint is only honoured by
                # WirePlumber once, at node-creation time. When the headset
                # powers off and back on the physical output node is
                # destroyed and recreated under a new id, and neither of
                # these "last hop" links was being watched by anything —
                # not the loopback pass above (loopback→EQ only) nor
                # ensure_spatial_eq_links (EQ→{HeSuVi,physical} only) — so
                # sound never came back on its own. Reuses link_data from
                # the pass above when available; best-effort otherwise.
                # No-ops silently when the physical output is absent
                # (headset off) — it self-heals on the tick after the
                # headset reappears.
                from arctis_sound_manager.sonar_to_pipewire import ensure_physical_output_links
                await _enforce_hop("physical output", ensure_physical_output_links)

                # ── Micro EQ capture link-enforcement (issue #127) ────────────
                # effect_input.sonar-micro-eq runs with node.autoconnect=false /
                # state.restore-target=false — the same "ASM owns this link"
                # fix as the loopback/EQ-output links above, applied to the
                # input side: WirePlumber never links or moves it, so a link
                # stolen by a competing microphone between two Sonar Micro EQ
                # applies (or after any out-of-band filter-chain restart) is
                # never repaired on its own. Reuses link_data from the pass
                # above when available; best-effort otherwise.
                from arctis_sound_manager.sonar_to_pipewire import ensure_micro_capture_link
                await _enforce_hop("micro capture", ensure_micro_capture_link)

                # ── Routing-override replay ──────────────────────────────────
                # Any loopback rebuilt above came back as a *new* sink, so every
                # stream the user had pinned to it (routing_overrides.json) fell
                # back to the system default. Replayed once here, after the link
                # passes, so the sinks are linked before streams are moved onto
                # them. Runs for every channel — it used to fire for "chat" only,
                # which silently dropped pins on Game and Media.
                if _overrides_needed:
                    try:
                        from arctis_sound_manager.pw_utils import reapply_routing_overrides
                        await asyncio.get_running_loop().run_in_executor(
                            None, reapply_routing_overrides,
                        )
                    except Exception as exc:
                        self.logger.error(
                            "_loopback_watchdog: error reapplying routing overrides: %r", exc
                        )
        except asyncio.CancelledError:
            raise

    @staticmethod
    def _hop_result_ok(result) -> bool:
        """Did a link-enforcement pass succeed?

        Passes report either a bool or a dict of per-hop bools. An **empty**
        dict means "nothing to enforce" — headset off, no external sink
        configured — and counts as success: treating it as a failure would have
        a powered-off headset escalate forever.
        """
        if isinstance(result, dict):
            return all(result.values())
        return bool(result)

    async def _degrade_channel_media_class(self, channel: str) -> None:
        """Regenerate *channel*'s EQ conf as a pickable Audio/Sink (#203).

        Last resort for a session that refuses the cross-client link into an
        Internal node: the channel carries no audio at all until this happens.
        The cost is that ASM's plumbing becomes visible in output pickers for
        that channel, which is why it takes repeated refusals to get here.
        """
        loop = asyncio.get_running_loop()
        try:
            from arctis_sound_manager import service_control as sc
            from arctis_sound_manager import sonar_to_pipewire as stp
            newly = await loop.run_in_executor(
                None, stp.mark_link_permission_fallback, channel)
            if not newly:
                return  # already degraded; the conf on disk is current
            await loop.run_in_executor(None, stp.ensure_sonar_eq_configs)
            await loop.run_in_executor(
                None, lambda: sc.restart("filter-chain", timeout=20))
            self.logger.warning(
                "_loopback_watchdog: '%s' regenerated as a pickable sink after "
                "repeated permission refusals — it will appear in output "
                "pickers, which is the price of it carrying audio at all.",
                channel,
            )
        except Exception as exc:
            self.logger.error(
                "_loopback_watchdog: could not degrade '%s': %r", channel, exc)

    async def _enforce_link_hop(
        self, hop: str, fn, lead_args: tuple, data,
        fail_ticks: dict[str, int], max_fail_ticks: int,
    ) -> None:
        """Run a last-hop link-enforcement pass, retrying once on a fresh snapshot.

        All the link passes in a watchdog tick share the single ``pw-dump``
        taken at the top of it. Ports move underneath that snapshot —
        WirePlumber churn, an ALSA reconfiguration — and ids resolved from it
        then point at nothing, which is what produces the observed
        ``pw-link … failed``, ``(4/8 channels linked)`` and
        ``no matchable ports … in=[]`` bursts. Rather than write off the whole
        tick and wait five seconds, re-dump **once** and retry; the extra
        subprocess is only ever paid when something actually failed.

        Unlike the loopback→EQ pass, these hops had no escalation whatsoever:
        they logged and retried forever. After *max_fail_ticks* consecutive
        failed ticks this calls ``ensure_filter_chain_healthy()`` once, then
        clears the counter so escalation cannot loop.
        """
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(None, fn, *lead_args, data)
            if self._hop_result_ok(result):
                fail_ticks.pop(hop, None)
                return

            from arctis_sound_manager.pw_utils import pw_dump_or_none
            fresh = await loop.run_in_executor(None, pw_dump_or_none)
            if fresh is None:
                # Unreadable graph: the retry proves nothing, so do not let it
                # count towards the escalation that restarts the chain (CHA-11).
                return
            result = await loop.run_in_executor(None, fn, *lead_args, fresh)
            if self._hop_result_ok(result):
                self.logger.info(
                    "_loopback_watchdog: %s hop recovered on a fresh pw-dump "
                    "(the shared snapshot was stale)", hop,
                )
                fail_ticks.pop(hop, None)
                return

            count = fail_ticks.get(hop, 0) + 1
            fail_ticks[hop] = count
            if count < max_fail_ticks:
                return
            fail_ticks.pop(hop, None)
            self.logger.warning(
                "_loopback_watchdog: %s hop still unlinked after %d consecutive "
                "ticks — calling ensure_filter_chain_healthy()", hop, count,
            )
            from arctis_sound_manager.sonar_to_pipewire import ensure_filter_chain_healthy
            await loop.run_in_executor(None, ensure_filter_chain_healthy)
        except Exception as exc:
            self.logger.error(
                "_loopback_watchdog: error enforcing %s hop: %r", hop, exc
            )

    def start(self) -> Coroutine:
        self._stopping = False
        self.usb_devices_monitor.start()

        # Apply the configured quantum (#183) — including 0, which is what
        # makes this self-healing: if a previous run was killed before stop()
        # could release it, the forced value would otherwise stay set for the
        # whole session with nothing left to undo it.
        try:
            from arctis_sound_manager.pw_utils import apply_force_quantum
            apply_force_quantum(int(getattr(self.general_settings, 'pipewire_quantum', 0)))
        except Exception as exc:
            self.logger.warning("start(): could not apply quantum setting: %r", exc)

        return self.loop()
    
    def stop(self):
        self.logger.info("Stopping CoreEngine...")
        self._stopping = True
        self.usb_devices_monitor.stop()
        # Honor "redirect on disconnect" *before* tearing down the loopbacks.
        # redirect_audio_on_disconnect() only fires when the current default is
        # still an Arctis-owned sink (its guard); once stop_all() removes the
        # Arctis_* sinks below, PipeWire falls back to some other device and the
        # guard no longer matches — which is why quitting ASM left audio on the
        # wrong output instead of the user's configured disconnect device.
        # teardown() calls this again later, but by then it's a no-op.
        try:
            self.redirect_audio_on_disconnect()
        except Exception as exc:
            self.logger.warning("stop(): redirect on disconnect failed: %r", exc)
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

        # Release the forced quantum (#183). It is a system-wide PipeWire
        # setting, so leaving it set would keep every other application on a
        # larger buffer for the rest of the session, long after ASM is gone.
        if getattr(self.general_settings, 'pipewire_quantum', 0):
            try:
                from arctis_sound_manager.pw_utils import apply_force_quantum
                apply_force_quantum(0)
            except Exception as exc:
                self.logger.warning("stop(): could not release forced quantum: %r", exc)

    # The ChatMix dial is an analogue control, and an analogue control that
    # nobody is touching still reports a position wobbling by a point: a
    # headset left alone on the desk sends 100, 99, 100, 99 … for as long as it
    # is on. Every one of those used to be written to the virtual sinks, which
    # on a desktop whose default output *is* one of them — and ASM makes
    # Arctis_Media the default — means the system volume OSD popping up over
    # whatever the user is doing, every few seconds, on its own. A move this
    # small is noise, not an intent: a hand on the dial moves it further.
    _MIX_JITTER_TOLERANCE = 1

    def _mix_is_jitter(self, new_media_mix: int, new_chat_mix: int) -> bool:
        """Whether a new dial reading is too small a move to be a real one.

        Both channels have to be within tolerance — a genuine turn moves at
        least one of them further. The travel ends (0 and 100) are always taken
        as real even when they are one point away: those are the positions the
        user can feel, and stopping a point short of silence (or of full
        volume) is exactly the kind of "not quite right" the dial is used to
        fix.
        """
        for new, current in ((new_media_mix, self.media_mix),
                             (new_chat_mix, self.chat_mix)):
            if new != current and new in (0, 100):
                return False
            if abs(new - current) > self._MIX_JITTER_TOLERANCE:
                return False
        return True

    def manage_mix_change(self):
        if not self.device_status or not self.device_config:
            return

        new_media_mix = self.device_status.get('media_mix', None)
        new_chat_mix = self.device_status.get('chat_mix', None)

        if new_media_mix is None or new_chat_mix is None:
            return

        new_media_mix = parsed_status({'media_mix': new_media_mix}, self.device_config).get('media_mix', self.media_mix)
        new_chat_mix = parsed_status({'chat_mix': new_chat_mix}, self.device_config).get('chat_mix', self.chat_mix)

        if new_media_mix == self.media_mix and new_chat_mix == self.chat_mix:
            return
        if self._mix_is_jitter(new_media_mix, new_chat_mix):
            # Deliberately not stored: keeping the settled values as the
            # reference is what lets a slow, real turn accumulate past the
            # tolerance instead of drifting one ignored point at a time.
            self.logger.debug(
                "Ignoring ChatMix jitter: media %d→%d, chat %d→%d",
                self.media_mix, new_media_mix, self.chat_mix, new_chat_mix,
            )
            return

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
            # 1 s read timeout, not 200 ms: an interrupt IN read returns the
            # instant the device sends a frame, so a longer timeout does NOT add
            # latency — it only lowers how often the loop wakes on an idle
            # endpoint. Paired with the timeout no longer triggering a back-off
            # (see the except below), this is what makes a turned dial land in
            # milliseconds instead of every second or two (GameDAC 2 volume /
            # ChatMix responsiveness).
            read_input: list[int] = list(await asyncio.to_thread(usb_device.read, endpoint, max_packet_size, 1000))
            self._eio_count = 0  # transfer succeeded, clear any EIO recovery state
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

            self._absorb_settings_readback(read_input)
            self._resolve_raw_response_waiters(read_input)

            await asyncio.sleep(0.1)
        except usb.core.USBError as e:
            if isinstance(e, usb.core.USBTimeoutError) or e.errno == 110:  # ETIMEDOUT
                # A plain interrupt-read timeout is the NORMAL idle state — no
                # frame was pending — not an error, so it must not be backed off
                # from. The old 1 s sleep here left the endpoint deaf for a full
                # second at a time, so a dial's real-time push frames (0x0725
                # volume, 0x0745 ChatMix) were only picked up about once a
                # second — the "updates every few seconds / turn it back the
                # other way to register" the GameDAC 2 users saw. The read
                # timeout paces the loop on its own; just read again.
                pass
            elif e.errno == 16:  # EBUSY — interface genuinely busy, back off
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
            elif e.errno == 5:  # EIO — interface got rebound by the kernel driver (usbhid)
                self._eio_count = getattr(self, '_eio_count', 0) + 1
                if self._eio_count == 1 or self._eio_count % 20 == 0:
                    # Naming the driver, not just "the kernel driver": once
                    # hid-steelseries exists (Linux 7.3+) this line is what
                    # tells "usbhid/hid-generic won the race again" apart
                    # from "hid-steelseries is now actively polling this
                    # interface" — see INT-1 in docs/HARDWARE-QUESTIONS.md.
                    self.logger.warning(
                        'USB I/O error (errno 5 ×%d) on interface %d, currently held '
                        'by driver=%s: %s', self._eio_count, interface_id,
                        self._interface_kernel_driver(usb_device, interface_id), e)
                await asyncio.sleep(0.5)
                if self._eio_count == 10:
                    # ~5 s of consecutive EIO: try to reclaim the interface(s)
                    # from the kernel before giving up on this connection.
                    with self._device_lock:
                        usb_device, device_config = self.usb_device, self.device_config
                    if usb_device is not None and device_config is not None:
                        self.logger.info('Re-acquiring USB interfaces after repeated EIO errors')
                        self.kernel_detach(usb_device, device_config)
                elif self._eio_count >= 20:
                    # Re-acquisition did not help: force a full reset.
                    self.logger.warning('EIO persists after re-acquisition attempt, forcing device reset')
                    self._eio_count = 0
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
        # Unlike poll_task this is not tied to a connected headset: it watches
        # the audio graph, which outlives any single connection, and it must
        # not restart (and reset its once-per-session notice) on every replug.
        xrun_task: asyncio.Task = asyncio.create_task(self._xrun_watch_loop())
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

            if not listen_coroutines:
                # Nothing to listen on. gather() over an empty list returns
                # immediately, so this while-loop would spin at 100% CPU and
                # never yield — the event loop never gets to acquire the D-Bus
                # name, and the GUI finds no daemon at all.
                #
                # No headset reaches this: every profile declares at least one
                # listen interface, and the validation refuses one that does
                # not. The generic profile (#189) has none by definition, which
                # is how this surfaced — on hardware, not in a test.
                await asyncio.sleep(1)
                continue

            await asyncio.gather(*listen_coroutines, return_exceptions=True)

        # Cleanup on stop
        for task in listen_coroutines:
            task.cancel()
        if poll_task is not None:
            poll_task.cancel()
        xrun_task.cancel()

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
                if self._detect_lock.locked():
                    # A detection is already in progress (e.g. a burst of udev
                    # 'add' events for the same device) — skip this one instead
                    # of blocking on the lock, which would call
                    # configure_virtual_sinks() again right after the running
                    # one finishes and re-release the just-claimed USB handle
                    # (EBUSY window). If this event turns out to have been
                    # needed, the periodic _rescan_for_device() retry catches
                    # it (issue #90-adjacent, adapted from PR #104).
                    self.logger.debug("on_device_connected: detection already in progress, skipping")
                    return
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
            # A replug is a fresh start: give the next acquisition its full
            # allowance of permission retries instead of inheriting a budget
            # already spent on the previous session.
            self._usb_permission_attempt = 0
            self._usb_permission_watching = False
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

    def reset_filter_chain_safe_mode(self) -> bool:
        """User-initiated: clear filter-chain safe mode and bring EQ back (#88).

        Restores the EQ configs safe mode disabled, clears the latch and
        restarts the filter-chain. If the graph still genuinely crashes it
        re-arms safe mode. Returns True on success."""
        try:
            from arctis_sound_manager.sonar_to_pipewire import clear_safe_mode_and_restore
            clear_safe_mode_and_restore()
            return True
        except Exception as exc:
            self.logger.warning("reset_filter_chain_safe_mode failed: %r", exc)
            return False

    def _resolve_sink_name(self, wanted: str) -> str | None:
        """Turn a saved device id into the ``node.name`` the graph uses.

        Settings store whichever id the picker offered — a ``node.nick`` from
        the Channels tab, a ``node.name`` elsewhere — so both are matched, the
        same rule the rest of the app follows.
        """
        if not wanted:
            return None
        try:
            import pulsectl
            with pulsectl.Pulse("asm-generic-resolve") as pulse:
                for sink in pulse.sink_list():
                    if wanted in (sink.name, sink.proplist.get("node.nick", "")):
                        return sink.name
        except Exception as exc:
            self.logger.debug("could not resolve generic output %r: %r", wanted, exc)
        return None

    def _setup_generic_device(self) -> bool:
        """Build the channels on a device ASM does not talk to (#189).

        Everything below the HID layer is unchanged: the loopbacks, the Sonar
        EQ chains, HeSuVi and the router only ever need sink names. What is
        skipped is the conversation with the headset — init_device() and the
        OLED — because there is no headset to hold it with.

        Returns True when generic mode handled this pass, so the caller stops
        treating "no Arctis found" as an error.
        """
        # Read through a missing attribute, not just a missing value: this runs
        # on the "no device found" path, which is reached before general_settings
        # exists in some start-up orders — and by every test that builds a bare
        # engine. Anything raising here would turn "no headset attached", an
        # ordinary state, into a crash.
        settings = getattr(self, "general_settings", None)
        if not getattr(settings, "generic_device_mode", False):
            return False

        device_config = next(
            (c for c in self.device_configurations if getattr(c, "generic", False)), None)
        if device_config is None:
            self.logger.error(
                "generic_device_mode is on but no profile declaring 'generic: true' "
                "was loaded — devices/generic.yaml is missing or was skipped as "
                "invalid; see the 'Skipping invalid device YAML' warnings above")
            return False

        wanted = getattr(settings, "generic_output_device", None)
        sink = self._resolve_sink_name(wanted or "")
        if sink is None:
            # Not an error worth repeating on every rescan: a Bluetooth headset
            # in its case is the ordinary case, and it comes back on its own.
            if not self._logged_no_device:
                self.logger.warning(
                    "Generic mode: output device %r is not in the graph — "
                    "waiting for it. Pick another in Settings if it is gone for good.",
                    wanted or "(unset)")
                self._logged_no_device = True
            return True

        self._logged_no_device = False

        if self.device_config is not None and self.device_config != device_config:
            self.teardown()

        self.device_config = device_config
        self.usb_device = None          # nothing to claim, detach or write to
        self.device_status = None

        source = self._resolve_sink_name(
            getattr(settings, "generic_input_device", None) or "")

        # Game and Chat share one sink here. A real Arctis exposes two PCMs and
        # ASM separates them; a generic headset has one output, so both channels
        # land on it. They stay independent where it matters — own volume, own
        # EQ, own place in the mixer and in ChatMix — they simply end up in the
        # same jack.
        device_state.set_current_device(
            physical_out_game=sink,
            physical_out_chat=sink,
            physical_in=source or "",
            spatial_engine=device_config.spatial_engine,
            device_name=device_config.name,
        )
        self.logger.info(
            "Generic mode: channels on %s%s", sink,
            f", microphone from {source}" if source else ", no microphone configured")

        try:
            from arctis_sound_manager.sonar_to_pipewire import check_and_fix_stale_configs
            fixed, needs_pw_restart = check_and_fix_stale_configs()
            if fixed:
                from arctis_sound_manager import service_control as sc
                if needs_pw_restart:
                    sc.restart("pipewire", "wireplumber", "pipewire-pulse", timeout=20)
                sc.restart("filter-chain", timeout=20)
        except Exception as exc:
            self.logger.warning("generic mode: config repair failed: %r", exc)

        self.setup_loopbacks()
        if source:
            self._claim_default_source()

        self.redirect_to_media_sink()
        self._device_ready = True
        return True

    def configure_virtual_sinks(self) -> None:
        with self._detect_lock:
            usb_device: Device | Any | None = None
            device_config: DeviceConfiguration | None = None

            for device_config in self.device_configurations:
                usb_device = self._find_hid_device(device_config.vendor_id, device_config.product_ids)
                if usb_device is not None:
                    break

            if not device_config or not usb_device:
                # No Arctis — but the audio half of ASM does not need one. The
                # channels, the Sonar EQ, HeSuVi, the router and Clips only ever
                # manipulate PipeWire sink names; it is the HID conversation
                # (battery, ANC, sidetone, ChatMix, OLED) that needs the device.
                # Generic mode does without it, on a sink the user names (#189).
                if self._setup_generic_device():
                    return
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

            # Apply (or clean up) per-device WirePlumber quirks now that the config
            # is resolved — e.g. the ALSA headroom fix for the Nova Pro Wireless USB
            # SYNC endpoint crackle (issue #105). No-op if the device YAML doesn't
            # declare alsa_headroom, or if the fragment on disk is already correct.
            try:
                from arctis_sound_manager.pw_quirks import apply_alsa_headroom_quirk
                apply_alsa_headroom_quirk(self.device_config)
            except Exception as e:
                self.logger.warning(f"Failed to apply WirePlumber ALSA headroom quirk: {e!r}")

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
                    # steps. Bail out so the daemon stays alive instead of crashing,
                    # but give udev a chance to catch up first: at boot the device is
                    # routinely enumerated before its access rights land.
                    self._schedule_usb_permission_retry()
                    return
                # Acquired: stand the whole retry machinery down, including a
                # slow watch that may have been running for hours.
                self._usb_permission_attempt = 0
                self._usb_permission_watching = False

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
            # The input deliberately does NOT share the output's fallback.
            # A headset with no capture node of its own — a Nova 7 sitting on
            # an iec958-stereo profile, a wireless dongle whose mic has not
            # enumerated yet — used to have its *sink* name stored as the mic,
            # and ensure_micro_capture_link then wired that sink's monitor into
            # effect_input.sonar-micro-eq. Everything the user could hear was
            # transmitted as their microphone: a browser tab went out over
            # Discord as if they were speaking it. Empty is the honest answer;
            # the watchdog links the mic once a real source appears.
            device_state.set_current_device(
                physical_out_game=physical_out_game or fallback,
                physical_out_chat=physical_out_chat or fallback,
                physical_in=physical_in or "",
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
            # Correctif 1 (issue #88): detect a pre-existing filter-chain crash-loop
            # BEFORE touching configs.  If the service was already crash-looping at
            # session start (e.g. a missing LADSPA .so causes a SEGV), entering
            # safe mode here prevents check_and_fix_stale_configs from regenerating
            # the crashing configs and re-arming the crash.  Safe mode writes a
            # disk marker (Correctif 2) so the flag survives daemon restarts.
            # Correctif 3 (issue #88): if safe mode is still armed from a prior
            # crash-loop but the ASM/PipeWire version has changed since (i.e. the
            # crash may now be fixed), auto-clear the latch and restore the EQ
            # configs so the normal path below re-tests them. If it still
            # crashes, ensure_filter_chain_healthy() / the watchdog re-arm.
            try:
                from arctis_sound_manager.sonar_to_pipewire import maybe_recover_from_safe_mode
                maybe_recover_from_safe_mode()
            except Exception as exc:
                self.logger.warning("maybe_recover_from_safe_mode failed: %r", exc)

            try:
                from arctis_sound_manager.sonar_to_pipewire import ensure_filter_chain_healthy
                ensure_filter_chain_healthy()
            except Exception as exc:
                self.logger.warning("ensure_filter_chain_healthy failed: %r", exc)

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
            self._claim_default_source()

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
                # The OLED is decoration: never let it take the daemon down with
                # it.  A missing font, an unexpected Pillow version (#154) or a
                # refused USB interface used to abort startup entirely, leaving
                # the user with no audio routing at all.
                try:
                    self.oled_manager = OledManager(self)
                    self.oled_manager.start()
                except Exception as exc:
                    self.logger.error("OLED display disabled, initialisation failed: %r", exc)
                    self.oled_manager = None

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
        # Ask the headset what it currently has, before pushing anything. The
        # replies land on the listen loop and only fill in settings the user
        # never chose here — see _absorb_settings_readback().
        self._request_settings_readback()
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

        # Not _apply_stored_eq() directly: in Sonar mode the stored curve must
        # not be reinstated at all, or the headset would colour the sound
        # underneath the software EQ on every start.
        self._applied_eq_mode = None
        self.reconcile_hardware_eq_mode()

    def _apply_stored_eq(self) -> bool:
        """Replay the saved custom curve at init, the way the sliders send it.

        This used to write [0x06, 0x33] + gains itself — the Nova Pro Wireless'
        report id and opcode, hardcoded. send_eq_command() stopped doing that
        for #146, but this path kept its own copy: on a parametric family the
        restored curve went out as a frame the headset doesn't recognise and
        was discarded, so the EQ only ever took effect if the user touched a
        slider after the daemon started. Going through send_eq_command() means
        one encoder for both paths.
        """
        eq_file = Path.home() / '.config' / 'arctis_manager' / 'eq_bands.json'
        if not eq_file.exists():
            return False
        try:
            bands = json.loads(eq_file.read_text())
            if isinstance(bands, list) and len(bands) == 10:
                if self.send_eq_command([int(b) for b in bands]):
                    self.logger.info("Custom EQ applied from eq_bands.json")
                    return True
        except Exception as e:
            self.logger.warning(f"Failed to apply stored EQ: {e}")
        return False

    def send_eq_command(self, bands: list[int]) -> bool:
        """Push the 10-band EQ to the headset. Returns False if it has none.

        The command bytes come from the device profile: they used to be
        hardcoded to [0x06, 0x33] — the Nova Pro Wireless' report id and
        opcode — and sent to whatever was connected. On every other family
        that is not a valid command, so the headset discarded it without a
        word and the custom EQ silently did nothing (#146). Sending it anyway
        is worse than not sending it: it makes a dead control look alive.
        """
        if self.device_config is None:
            return False

        bands = sanitise_eq_bands(bands, self.logger)
        if bands is None:
            return False

        endpoint = self.get_command_endpoint_address()

        eq_format = self.device_config.hardware_eq_format
        if eq_format:
            # Parametric families (Nova 7 Gen 2 and friends): the curve is
            # described band by band and sent over three frames, the last of
            # which commits it. ASM's sliders are 0-40 with 20 = 0 dB, half a
            # decibel per step, which is the -10..+10 dB the hardware takes.
            from arctis_sound_manager.hardware_eq import ENCODERS, bands_from_gains

            encoder = ENCODERS.get(eq_format)
            if encoder is None:
                self.logger.error("Unknown hardware EQ format %r for %s",
                                  eq_format, self.device_config.name)
                return False
            options = dict(self.device_config.hardware_eq_options)
            # Some families need the device to breathe between the name frame
            # and the bands: GG raises its inter-command delay to 600 ms there
            # (and its USB timeout to 1200 ms) for the Nova 5 and the GameBuds.
            # Sending both back to back risks the curve not landing.
            frame_delay = options.pop('frame_delay_ms', 0) / 1000
            frames = encoder(bands_from_gains([(b - 20) / 2 for b in bands]),
                             **options)
            for index, frame in enumerate(frames):
                if index and frame_delay:
                    time.sleep(frame_delay)
                self.send_command(frame, endpoint)
            self._select_custom_eq_preset(endpoint)
            return True

        command = self.device_config.hardware_eq_command
        if not command:
            self.logger.info(
                "%s has no on-device equaliser ASM can drive — custom EQ not sent.",
                self.device_config.name,
            )
            return False

        # Shift ASM's 0-40 sliders onto whatever this family calls 0 dB.
        shift = self.device_config.hardware_eq_zero - 20
        self.send_command(list(command) + [b + shift for b in bands], endpoint)
        self._select_custom_eq_preset(endpoint)
        return True

    def _select_custom_eq_preset(self, endpoint: int) -> None:
        """Make the curve just written the one the headset actually applies.

        On the Nova Pro Wireless and the Nova Pro Wired GameDAC, writing the
        ten gains only fills the Custom slot: the headset keeps applying
        whichever preset is *selected*, and ASM selects Flat during
        `device_init` so its own Sonar EQ is the only thing colouring the
        sound. The result was a custom EQ that could be seen and not heard —
        the DAC screen drew the curve as the sliders moved while nothing
        changed audibly, because Flat was still the active preset.

        Selecting Custom here rather than in `device_init` keeps the neutral
        default for anyone who never touches these sliders: with no
        eq_bands.json, `_apply_stored_eq()` returns before reaching this, so
        the headset stays on Flat exactly as before.

        Families whose write already activates the curve (the parametric ones
        carry `preset_type` in the frame itself, and declare no such opcode)
        leave `preset_select` unset and are untouched.
        """
        if self.device_config is None:
            return
        select = self.device_config.hardware_eq_preset_select
        if not select:
            return
        # The two equalisers are mutually exclusive: in Sonar mode the on-device
        # curve must stay flat whoever asked for a write, so the switch to
        # Custom is gated on the mode rather than on the caller being polite.
        if self._read_eq_mode_is_sonar():
            return
        self.send_command(
            list(select) + [self.device_config.hardware_eq_custom_preset_id],
            endpoint,
        )

    def _flatten_hardware_eq(self) -> bool:
        """Take the on-device equaliser out of the signal path.

        Sonar mode does its equalising in software, in the filter chain; a
        curve still active in the headset would stack on top of it, so the
        hardware side has to be neutral for the Sonar curve to be the only
        thing shaping the sound.

        Families with a preset command switch to their Flat preset, which
        leaves the stored custom curve untouched in its slot. The others have
        no such command, so they get a curve that is flat — ASM keeps
        eq_bands.json as the source of truth and writes the real curve back
        when the mode returns to Custom.
        """
        if self.device_config is None or not self.has_hardware_eq():
            return False

        select = self.device_config.hardware_eq_preset_select
        if select:
            self.send_command(
                list(select) + [self.device_config.hardware_eq_flat_preset_id],
                self.get_command_endpoint_address(),
            )
            return True

        # 20 is 0 dB on ASM's 0-40 slider scale; send_eq_command shifts it onto
        # whatever this family calls zero.
        return self.send_eq_command([20] * 10)

    def reconcile_hardware_eq_mode(self) -> bool:
        """Make the on-device equaliser match the active EQ mode.

        `.eq_mode` is written from four places — both mode toggles, a profile
        being restored, and the Sonar-forcing path in equalizer_page — and
        adding a D-Bus call to each of them would be four call sites to keep in
        step, the drift this codebase keeps getting bitten by. Reconciling from
        the status poll instead covers every writer, including a file edited by
        hand, and sends nothing while the mode is unchanged.

        Returns True when a write was issued.
        """
        if self.device_config is None or not self.has_hardware_eq():
            return False

        mode = 'sonar' if self._read_eq_mode_is_sonar() else 'custom'
        if mode == self._applied_eq_mode:
            return False

        applied = (self._flatten_hardware_eq() if mode == 'sonar'
                   else self._apply_stored_eq())
        # Remember the mode either way: a family with no stored curve has
        # nothing to apply, and retrying every two seconds would be pointless.
        self._applied_eq_mode = mode
        if applied:
            self.logger.info("On-device EQ set for %s mode", mode)
        return applied

    def has_hardware_eq(self) -> bool:
        """True when this headset exposes an EQ the custom band sliders drive."""
        return bool(self.device_config and (self.device_config.hardware_eq_command
                                            or self.device_config.hardware_eq_format))
    
    def is_device_online(self) -> bool:
        # Generic mode has no device to be online or offline (#189). "Online"
        # is the honest answer: the channels point at a sink that is in the
        # graph — _setup_generic_device refuses to build them otherwise — so
        # the routing that keys off this (adopting the default on connect,
        # handing it back on disconnect) behaves as it does with a headset on.
        if (getattr(getattr(self, "general_settings", None), "generic_device_mode", False)
                and self.usb_device is None):
            return bool(device_state.get_physical_out_game())
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

        self._auto_switch_mic(key)

    def _auto_switch_mic(self, key: str) -> None:
        """Flip the Sonar Micro EQ input between the headset mic and a configured
        alternate mic when the armed trigger fires (community request).

        Works on every headset: the manual selector (``micro_input_source``)
        always applies, and the automatic triggers act only on status keys the
        device actually sends, so a headset that never reports mute (or that is
        always-on and never "disconnects") simply doesn't auto-switch — it never
        errors. Apps stay on ``effect_output.sonar-micro-eq`` throughout, so the
        change is inaudible to a call in progress.
        """
        gs = getattr(self, 'general_settings', None)
        if gs is None:
            return
        mode = _MIC_AUTOSWITCH_MODES.get(int(getattr(gs, 'micro_autoswitch', 0) or 0), 'off')
        alt = getattr(gs, 'micro_alt_source', '') or ''
        if mode == 'off' or not alt:
            return

        online_var = None
        if self.device_config and self.device_config.online_status:
            online_var = self.device_config.online_status.status_variable

        mic_muted = False
        if self.device_status is not None:
            parsed = parsed_status({'mic_status': self.device_status.get('mic_status')},
                                   self.device_config)
            mic_muted = parsed.get('mic_status') == 'muted'

        target = resolve_mic_autoswitch_target(
            mode, key, online_var, self.is_device_online(), mic_muted, alt)
        if target is None:
            return

        current = getattr(gs, 'micro_input_source', '__auto__') or '__auto__'
        if current == target:
            return  # already there — natural debounce, no relink churn

        gs.micro_input_source = target
        try:
            gs.write_to_file()
        except Exception as exc:  # noqa: BLE001 — never let a settings write break status handling
            self.logger.warning("Auto mic-switch: could not persist source: %s", exc)
            return
        self.logger.info(
            "Auto mic-switch (%s): Sonar Micro EQ input → %s",
            mode, "alternate mic" if target != '__auto__' else "headset mic")
        try:
            from arctis_sound_manager.sonar_to_pipewire import ensure_micro_capture_link
            ensure_micro_capture_link()
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("Auto mic-switch: relink failed: %s", exc)

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
        """Make the Media channel the system default when the headset comes up.

        Media, because the default output is what everything ASM does not route
        by name follows: a browser, a music player, system sounds, any app the
        router has never heard of. Sending that to Game — which is what this did
        for as long as PULSE_MEDIA_NODE_NAME held ``Arctis_Game`` — files all of
        it as game audio, so the ChatMix dial then balances a podcast against
        Discord.

        Only when the headset is actually on: the channels point at it, so
        adopting the default while it is off would move audio to a device that
        is not there. That is why redirect_audio_on_disconnect exists to hand
        the default back when it goes away.
        """
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
            # The toggle is a separate setting from the device it redirects to,
            # and that device defaults to unset. Returning here was silent: the
            # user switched the feature on, nothing ever happened, and no log
            # line said why — which reads as "the feature is broken"
            # (discussion #48). Fall back to the best non-Arctis sink instead,
            # since "send my audio somewhere else when the headset goes" is
            # unambiguous even when the somewhere was never picked.
            redirect_device = self._fallback_disconnect_sink()
            if not redirect_device:
                self.logger.warning(
                    "redirect on disconnect is enabled but no device is "
                    "configured, and no other output is available — audio "
                    "stays where it is. Pick a device in Settings."
                )
                return
            self.logger.info(
                "redirect on disconnect is enabled with no device configured "
                "— falling back to '%s'. Pick one in Settings to choose.",
                redirect_device,
            )

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

    def _fallback_disconnect_sink(self) -> str | None:
        """Best sink to fall back to when no redirect device was configured.

        Anything that is not one of ASM's own channels and not the headset
        itself — redirecting to those would either be a no-op or send the
        audio straight back where it cannot be heard. Ordered by PulseAudio's
        own idea of priority so the answer matches what the desktop would
        have picked on its own.
        """
        try:
            sinks = self.pa_audio_manager.sink_list_wrapper()
        except Exception as exc:
            self.logger.warning("could not list sinks for the disconnect fallback: %r", exc)
            return None

        candidates = []
        for sink in sinks:
            name = getattr(sink, 'name', '') or ''
            if not name:
                continue
            if any(frag in name for frag in self._ARCTIS_OWNED_SINK_FRAGMENTS):
                continue
            props = getattr(sink, 'proplist', {}) or {}
            try:
                vendor = int(props.get('device.vendor.id', '0') or '0', 16)
            except (TypeError, ValueError):
                vendor = 0
            if name.startswith('alsa_output') and vendor == STEELSERIES_VENDOR_ID:
                continue
            candidates.append((int(props.get('device.priority', 0) or 0), name))

        if not candidates:
            return None
        return max(candidates)[1]

    def reconcile_audio_routing_for_power_state(self) -> None:
        """Re-assert audio routing to match the headset's current power state.

        On resume from sleep, PipeWire/WirePlumber re-links each stream to its
        remembered ``target.node`` once the graph settles. Media apps whose last
        target was ``Arctis_Media`` snap back onto it even while the headset is
        powered off, so audio stops reaching the external speakers/TV until the
        user toggles the headset (which fires ``redirect_audio_on_disconnect``).
        This performs that same reconciliation programmatically (issue #128).

        Both ``redirect_to_media_sink`` and ``redirect_audio_on_disconnect`` have
        their own setting guards, so this is a no-op when the user disabled the
        connect/disconnect redirection.
        """
        with self._device_lock:
            have_device = self.usb_device is not None and self.device_config is not None
        if not have_device:
            return
        if self.is_device_online():
            self.redirect_to_media_sink()
        else:
            self.redirect_audio_on_disconnect()

    def _setting_default(self, name: str) -> int:
        """Profile-declared default for a setting, or 0 if none is an int.

        Used as the fallback when a saved setting value is missing, so device
        init never pushes a stray 0 that would mute/min-cap a control.
        """
        if self.device_config is not None:
            for section in self.device_config.settings.values():
                for setting in section:
                    if setting.name == name and isinstance(setting.default_value, int):
                        return setting.default_value
        return 0

    def translate_init_bytes(self, data: list[int|str]) -> list[int]:
        result: list[int] = []

        for byte in data:
            if isinstance(byte, int):
                result.append(byte)
            elif isinstance(byte, str):
                uri = byte.split('.')
                if uri[0] == 'settings':
                    # Fall back to the profile default (not 0) when the saved
                    # value is missing. A stray 0 here gets pushed to the device
                    # and min-caps the control — e.g. mic_volume dropping to 1/10
                    # after a reconnect/update instead of the user's saved level.
                    resolved = self.device_settings.get(uri[1], self._setting_default(uri[1]))
                    # Optional derived form, e.g. 'settings.mic_side_tone.enabled':
                    # apply the same value.* transforms _resolve_update_sequence
                    # uses, so init can split a setting into a state byte + a
                    # level byte rather than hardcoding the state on (#161 — the
                    # Nova Pro Omni sidetone could never be turned off because
                    # the init always forced its state byte to 1).
                    if len(uri) > 2:
                        token = f'value.{uri[2]}'
                        if token not in _DERIVED_TOKENS:
                            raise Exception(f'Unknown derived token in init byte: {byte}')
                        resolved = _DERIVED_TOKENS[token](resolved)
                    result.append(resolved)
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

        try:
            endpoint, _ = self.guess_interface_endpoint('out', self.device_config.command_interface_index[0], self.device_config.command_interface_index[1])
        except Exception as exc:
            # The declared command interface does not exist on this hardware unit
            # (e.g. wrong interface/alt-setting in the YAML, issue #100 Nova Elite).
            # Treat identically to the "no OUT endpoint" case: fall back to
            # HID SET_REPORT over the control endpoint so the daemon keeps running.
            if not self._warned_no_out_endpoint:
                self._warned_no_out_endpoint = True
                self.logger.warning(
                    f"Command interface not found on "
                    f"{self.usb_device.idVendor:04x}:{self.usb_device.idProduct:04x} "
                    f"({exc}); falling back to HID SET_REPORT (control transfer)."
                )
            else:
                self.logger.debug(
                    "Command interface not found on %04x:%04x (SET_REPORT fallback active).",
                    self.usb_device.idVendor, self.usb_device.idProduct,
                )
            return 0
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
            elif b in _DERIVED_TOKENS:
                result.append(_DERIVED_TOKENS[b](value))
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

    def _interface_kernel_driver(
        self, usb_device: TypedDevice, interface: int,
        sys_root: Path = Path('/sys/bus/usb/devices'),
    ) -> str:
        """Best-effort name of whatever kernel driver currently holds *interface*.

        Diagnostic only — this never decides ASM's behaviour, only what gets
        logged. `usb_device.is_kernel_driver_active()` (libusb) can only say
        yes/no; it cannot say *which* driver, which is exactly the question
        that matters once hid-steelseries exists (Linux 7.3+): "usbhid /
        hid-generic got there first" and "hid-steelseries got there first"
        call for different remediation, and today's EACCES/EIO log lines
        cannot tell a bug reporter which one they are looking at. See INT-1
        in docs/HARDWARE-QUESTIONS.md.

        A plain sysfs read, so it works even when the USB permission problem
        being diagnosed would make any pyusb call unreliable. Returns
        "unknown" rather than raising — this must never be why a detach or
        EIO-recovery log line goes missing. *sys_root* exists so tests can
        point this at a fixture tree; nothing else should pass it.
        """
        try:
            bus = usb_device.bus
            ports = getattr(usb_device, 'port_numbers', None)
        except Exception:
            return "unknown"
        if not bus or not ports:
            return "unknown"
        device_dir_name = f"{bus}-" + ".".join(str(p) for p in ports)
        try:
            iface_dir = find_interface_sysfs_dir(sys_root, device_dir_name, interface)
            if iface_dir is None:
                return "unknown"
            return kernel_driver_for_interface(iface_dir) or "none (unclaimed)"
        except Exception:
            return "unknown"

    def kernel_detach(self, usb_device: TypedDevice, config: DeviceConfiguration) -> bool:
        """Detach the kernel driver from every interface ASM uses, then claim it.

        Detaching without claiming leaves the interface free: the kernel is
        liable to rebind usbhid to it behind our back, at which point every
        transfer ASM issues fails with EIO (errno 5). Claiming is idempotent
        (pyusb/libusb no-ops a claim on an interface this process already
        holds), so calling this repeatedly — e.g. from the EIO recovery path
        below — is safe.

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
                        "(0x%04x:0x%04x) on interface %d (currently held by driver=%s). "
                        "udev rules are missing or "
                        "have not been applied to the currently-attached device. "
                        "Try one of: 1) replug the dongle, "
                        "2) `sudo asm-cli udev reload-rules`, "
                        "3) reinstall ASM via your distro package so the rules go "
                        "to /etc/udev/rules.d/. The GUI will offer a one-click fix "
                        "if it's open. The daemon will keep running.",
                        config.name, usb_device.idVendor, usb_device.idProduct, interface,
                        self._interface_kernel_driver(usb_device, interface),
                    )
                    continue
                else:
                    self.logger.warning(
                        f"Could not detach kernel driver on interface {interface} "
                        f"(currently held by driver={self._interface_kernel_driver(usb_device, interface)}): "
                        f"{e!r}. Continuing with remaining interfaces."
                    )

            # Claim the interface for this process regardless of whether the
            # kernel driver was active: without this, nothing actually owns
            # the interface and the kernel is free to rebind usbhid to it.
            try:
                usb.util.claim_interface(usb_device, interface)
            except usb.core.USBError as e:
                self.logger.warning(
                    f"Could not claim interface {interface}: {e!r}. "
                    "Continuing with remaining interfaces."
                )
        # Surface the EACCES state to the GUI; clear it once a clean pass happens.
        self.permission_error = had_eacces
        return not had_eacces

    def _request_settings_readback(self) -> None:
        """Send the queries whose replies carry the device's current settings."""
        if self.device_config is None or not self.device_config.settings_readback:
            return
        endpoint = self.get_command_endpoint_address()
        for readback in self.device_config.settings_readback:
            try:
                self.send_command([readback.request], endpoint)
            except usb.core.USBError as e:
                # Not worth a retry: the settings simply stay at their defaults.
                self.logger.warning("Settings readback 0x%02x failed: %r",
                                    readback.request, e)

    def _absorb_settings_readback(self, response: list[int]) -> None:
        """Adopt values the headset reports for settings the user never set.

        ASM pushes its own settings at every device init, which is right when
        the user has made a choice and wrong when they haven't: a headset
        configured elsewhere — in GG on a dual-boot, or from the device's own
        controls — got flattened to profile defaults the first time ASM ran.

        A value read back from the device therefore fills in blanks only. Once
        a setting exists in the user's settings file it is theirs, and nothing
        the headset reports may overwrite it, or a stale reply would fight
        every change they make.
        """
        if self.device_config is None or not self.device_config.settings_readback:
            return
        if not response:
            return

        for readback in self.device_config.settings_readback:
            if response[0] != readback.starts_with:
                continue
            for name, value in readback.values_from(response).items():
                if self.device_settings.was_chosen_by_user(name):
                    continue
                if self.device_settings.get(name, None) == value:
                    continue
                self.logger.info(
                    "Adopting %s=%s read back from the headset (never set here)",
                    name, value)
                self.device_settings.settings[name] = value

    def _resolve_raw_response_waiters(self, response: list[int]) -> None:
        """Hand a raw listen-endpoint frame to whoever is awaiting its opcode.

        Called from listen_endpoint_loop for every frame it reads, alongside
        the existing status/settings-readback absorption — this is the same
        stream, just also usable for a one-shot request/response instead of
        only "update whatever's listening".
        """
        if not response or not self._raw_response_waiters:
            return
        futures = self._raw_response_waiters.pop(response[0], None)
        if not futures:
            return
        for future in futures:
            if not future.done():
                future.set_result(list(response))

    async def _await_raw_response(self, opcode: int, timeout: float = 2.0) -> list[int] | None:
        """Wait up to *timeout* seconds for a listen-endpoint frame starting
        with *opcode*. Returns None on timeout — the caller decides what that
        means (headset didn't reply vs. reply lost vs. no headset at all)."""
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._raw_response_waiters.setdefault(opcode, []).append(future)
        try:
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            waiters = self._raw_response_waiters.get(opcode)
            if waiters and future in waiters:
                waiters.remove(future)
                if not waiters:
                    del self._raw_response_waiters[opcode]

    def has_hardware_eq_readback(self) -> bool:
        """True when this headset's profile declares how to read the
        parametric EQ curve back — only set for families whose spec was
        checked directly for the 0x32/0xA6 opcodes (#146)."""
        return bool(
            self.device_config
            and self.device_config.hardware_eq_format == 'parametric'
            and self.device_config.hardware_eq_readback is not None
        )

    async def read_hardware_eq(self, connection_type: int | None = None) -> dict:
        """Query the headset for the parametric EQ curve and preset name it
        currently has stored (issue #146 diagnostics).

        Distinguishes three outcomes a user pasting this into an issue needs
        told apart:
          - a conformant curve comes back matching what the sliders sent →
            the headset received and applied it; the bug is not in the write
            path or the firmware's application of it.
          - a reply comes back but decodes to something else (all zero,
            stuck at defaults, garbage) → the frame arrived but the headset
            did not adopt it as the active curve.
          - no reply at all (timeout) → the write likely never reached the
            headset either — a transport/USB problem, not an EQ one.

        Returns a JSON-serialisable dict; never raises for "no device" /
        "unsupported" / "no reply", only for a programming error.
        """
        if self.device_config is None:
            return {'ok': False, 'error': 'no_device'}
        if not self.has_hardware_eq_readback():
            return {'ok': False, 'error': 'unsupported'}

        from arctis_sound_manager.hardware_eq import (decode_eq_preset_data,
                                                       decode_eq_preset_name)

        readback = self.device_config.hardware_eq_readback
        assert readback is not None  # has_hardware_eq_readback() just checked
        conn = readback.connection_type if connection_type is None else connection_type
        endpoint = self.get_command_endpoint_address()

        result: dict = {'ok': True, 'connection_type': conn}

        try:
            self.send_command([readback.band_query, conn], endpoint)
        except usb.core.USBError as e:
            return {'ok': False, 'error': f'usb_write_error: {e!r}'}
        band_response = await self._await_raw_response(readback.band_query)
        if band_response is None:
            result['bands'] = None
            result['bands_error'] = 'timeout'
        else:
            # Always carry the raw reply, decoded or not. Where band 1 starts
            # was first inferred from the spec's "TODO: remove after FW fix
            # missing byte" note and was off by one: the reply decoded into
            # ten plausible bands (8192 Hz, Q 34.30) that were pure phase
            # error. A real reply (#146) settled it at offset 2. Keep shipping
            # the hex — it is what let the offset be corrected from a user
            # report instead of believed.
            result['bands_raw'] = ' '.join(f'{b & 0xFF:02x}' for b in band_response)
            try:
                result['bands'] = [
                    {'frequency': b.frequency, 'gain_db': b.gain_db,
                     'q': b.q, 'filter_type': b.filter_type}
                    for b in decode_eq_preset_data(band_response)
                ]
            except ValueError as e:
                result['bands'] = None
                result['bands_error'] = f'decode_error: {e}'

        try:
            self.send_command([readback.name_query, conn], endpoint)
        except usb.core.USBError as e:
            result['name'] = None
            result['name_error'] = f'usb_write_error: {e!r}'
            return result
        name_response = await self._await_raw_response(readback.name_query)
        if name_response is None:
            result['name'] = None
            result['name_error'] = 'timeout'
        else:
            result['name_raw'] = ' '.join(f'{b & 0xFF:02x}' for b in name_response)
            try:
                name, preset_type = decode_eq_preset_name(name_response)
                result['name'] = name
                result['preset_type'] = preset_type
            except ValueError as e:
                result['name'] = None
                result['name_error'] = f'decode_error: {e}'

        return result

    def _schedule_usb_permission_retry(self) -> None:
        """Retry acquiring the device before blaming the udev rules.

        A dongle that never leaves its port is enumerated during boot, and the
        daemon starts right behind it — early enough that the access rights are
        not always in place yet on the freshly created device node. The first
        acquisition of the session then fails with EACCES while every later one
        succeeds, which is why quitting ASM and reopening it "fixed" it and why
        the permissions dialog came back on every single boot even though the
        rules on disk were perfectly valid.

        Bailing out on that first failure also left the headset unmanaged until
        the user clicked something, since nothing retried on its own.

        Retries are scheduled off-thread (configure_virtual_sinks holds
        _detect_lock and runs on the USB event path — it must not sleep). The
        permission flag stays down while attempts remain, so the GUI is only
        told about a genuine, lasting permission problem.
        """
        budget = len(_USB_PERMISSION_RETRY_DELAYS)
        if self._usb_permission_attempt >= budget:
            # Out of fast retries: tell the user, and keep trying anyway. The
            # dialog is a way out, not the only one — see the note on
            # _USB_PERMISSION_WATCH_INTERVAL.
            delay = _USB_PERMISSION_WATCH_INTERVAL
            self.permission_error = True
            if not self._usb_permission_watching:
                self._usb_permission_watching = True
                self.logger.error(
                    "USB access still denied after %d retries — surfacing the "
                    "permissions dialog, and re-checking every %.0fs in case "
                    "the rules land later.", budget, delay,
                )
            else:
                self.logger.debug(
                    "USB access still denied — next check in %.0fs", delay,
                )
        else:
            delay = _USB_PERMISSION_RETRY_DELAYS[self._usb_permission_attempt]
            self._usb_permission_attempt += 1
            # Not a confirmed permission problem yet — don't prompt the user.
            self.permission_error = False
            self.logger.info(
                "USB access denied on acquisition, retrying in %.1fs (attempt %d/%d)",
                delay, self._usb_permission_attempt, budget,
            )

        def _retry() -> None:
            try:
                self.configure_virtual_sinks()
            except Exception as e:  # never kill the timer thread
                self.logger.warning("USB permission retry failed: %r", e)

        timer = threading.Timer(delay, _retry)
        timer.daemon = True
        timer.start()

    def release_all_interfaces(self, usb_device: TypedDevice, config: DeviceConfiguration) -> None:
        """Release every interface :meth:`kernel_detach` claimed.

        The claim/release pair must cover the same set. ``kernel_detach``
        claims *all* of :meth:`_all_used_interfaces` — command, status
        listeners and every dial candidate — because detaching without
        claiming lets the kernel rebind usbhid behind our back and every
        transfer then fails with EIO (see that method's docstring). Releasing
        only the command interface therefore leaves the rest claimed, and
        :meth:`kernel_attach` is asked to hand interfaces back to the kernel
        while this process still holds them.

        Best effort by design: an interface that was never successfully
        claimed raises, and that is not an error worth propagating on a
        teardown path — the goal is to give back whatever we hold before the
        kernel driver is re-attached.
        """
        for interface in self._all_used_interfaces(config):
            try:
                usb.util.release_interface(usb_device, interface)
            except (usb.core.USBError, ValueError, OSError) as e:
                # Not claimed, already gone, or device unplugged mid-teardown.
                self.logger.debug("Interface %s not released: %s", interface, e)

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

    # Xrun self-diagnostics (#183) ──────────────────────────────────────────
    #
    # The surround chain missing its deadline is audible as crackling, and
    # nothing in the UI explains it — users are left to guess, or to blame the
    # headset. The counters are cheap to read; the hard part is deciding when
    # to speak, because xruns also spike for reasons that have nothing to do
    # with ASM (a compile finishing, a game loading a level, the machine coming
    # out of suspend). A notification that fires on those teaches people to
    # dismiss it, and then it is worth less than nothing.
    #
    # So: only speak when the problem is *sustained* — several consecutive
    # samples, each with real activity — and only once per session. A single
    # burst, however large, stays silent.
    _XRUN_SAMPLE_PERIOD_S = 60.0
    _XRUN_MIN_PER_SAMPLE = 3      # a sample below this is noise, not a pattern
    _XRUN_CONSECUTIVE = 3         # ~3 minutes of steady xruns before speaking
    _XRUN_NODE_FRAGMENTS = ("virtual-surround-7.1-hesuvi", "sonar-")

    async def _xrun_watch_loop(self, period: float | None = None):
        period = period or self._XRUN_SAMPLE_PERIOD_S
        previous: dict[str, int] = {}
        streak = 0
        notified = False
        try:
            while not self._stopping:
                await asyncio.sleep(period)
                if notified:
                    continue
                # Nothing to suggest if the user already chose a larger buffer,
                # and nothing to blame the chain for if Spatial Audio is off.
                if getattr(self.general_settings, 'pipewire_quantum', 0):
                    continue
                try:
                    from arctis_sound_manager.pw_utils import get_xrun_counts
                    current = await asyncio.get_running_loop().run_in_executor(
                        None, get_xrun_counts, self._XRUN_NODE_FRAGMENTS)
                except Exception as exc:
                    self.logger.debug("xrun sample failed: %r", exc)
                    continue
                if not current:
                    # pw-top missing or nothing of ours in the graph: no
                    # information, which is not the same as no xruns.
                    continue

                # Counters are monotonic per node, and a node that was recreated
                # (filter-chain restart) restarts at 0 — a negative delta is a
                # new node, not negative xruns.
                delta = sum(max(0, n - previous.get(name, n)) for name, n in current.items())
                previous = current

                streak = streak + 1 if delta >= self._XRUN_MIN_PER_SAMPLE else 0
                if streak < self._XRUN_CONSECUTIVE:
                    continue

                self.logger.warning(
                    "Audio glitches: %d xruns/min sustained over %d samples on the "
                    "surround chain — suggesting stability mode (#183)",
                    delta, self._XRUN_CONSECUTIVE,
                )
                self._notify_xruns()
                notified = True
        except asyncio.CancelledError:
            raise

    def _notify_xruns(self) -> None:
        """Tell the user once, and say what to do about it."""
        import subprocess
        try:
            subprocess.run(
                ["notify-send", "-a", "Arctis Sound Manager",
                 "Audio glitches detected",
                 "The Spatial Audio processing is struggling to keep up, which "
                 "sounds like crackling. Settings → Audio stability (buffer "
                 "size) can fix it, at the cost of slightly more latency."],
                check=False, timeout=5,
            )
        except Exception as exc:
            # notify-send is optional — a headless or minimal session simply
            # gets the log line instead.
            self.logger.debug("notify-send unavailable: %r", exc)

    # Status-poll error reporting (#198) ──────────────────────────────────────
    #
    # These errors used to be dropped on the floor: errno 16/19/110 were all
    # skipped, and this loop runs every two seconds forever. A device whose
    # command channel never answers therefore produced *no log line at all*,
    # indefinitely — the status stayed empty, the battery never appeared, and
    # nothing anywhere said why. That is how #198 reached three rounds of
    # questions before anyone could see what was happening.
    #
    # They stay quiet-by-default for a good reason, though: EBUSY and ENODEV
    # are ordinary during a hotplug or a suspend/resume, and a warning every
    # two seconds is its own kind of useless. So: say it once when a streak
    # starts, once a minute while it lasts, and once when it clears — enough to
    # find in a journal, never enough to bury one.
    _STATUS_POLL_LOG_EVERY = 30          # polls, i.e. ~60s at period=2.0

    def _note_status_poll_error(self, exc: usb.core.USBError) -> None:
        errno_val = getattr(exc, 'errno', None)
        streak = getattr(self, '_status_poll_fail_streak', 0) + 1
        self._status_poll_fail_streak = streak

        if streak == 1 or streak % self._STATUS_POLL_LOG_EVERY == 0:
            hint = ""
            if errno_val == 110:
                # The one worth explaining: nothing is wrong with the cable or
                # the permissions, the device simply never answers.
                hint = (" — the device is not answering status requests, so no "
                        "battery or mode will be shown")
            elif errno_val == 16:
                # "Something" used to be unnamed. A bug report can now say
                # which driver is holding it — see INT-1 in
                # docs/HARDWARE-QUESTIONS.md for why that distinction will
                # start to matter once hid-steelseries exists (Linux 7.3+).
                driver = "unknown"
                usb_device = getattr(self, 'usb_device', None)
                device_config = getattr(self, 'device_config', None)
                if usb_device is not None and device_config is not None:
                    driver = self._interface_kernel_driver(
                        usb_device, device_config.command_interface_index[0])
                hint = f" — something else is holding the interface (driver={driver})"
            self.logger.warning(
                "Status poll USB error (x%d): %r%s", streak, exc, hint)

    def _note_status_poll_ok(self) -> None:
        if getattr(self, '_status_poll_fail_streak', 0):
            self.logger.info(
                "Status poll recovered after %d failed attempts",
                self._status_poll_fail_streak)
            self._status_poll_fail_streak = 0

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
                        self._note_status_poll_ok()
                    except usb.core.USBError as e:
                        self._note_status_poll_error(e)
                    except Exception as e:
                        self.logger.warning(f"Status poll failed: {e!r}")
                    # Keep the on-device EQ exclusive with the Sonar one. A
                    # no-op unless the mode changed since the last pass.
                    try:
                        self.reconcile_hardware_eq_mode()
                    except Exception as e:
                        self.logger.warning(f"EQ mode reconcile failed: {e!r}")
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
            self.release_all_interfaces(self.usb_device, self.device_config)

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
                    # Release every interface kernel_detach claimed, not just
                    # the command one: kernel_attach below hands them all back
                    # to the kernel, and it cannot succeed on an interface this
                    # process is still holding.
                    self.release_all_interfaces(self.usb_device, self.device_config)
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
