# Copyright (C) 2022 Giacomo Furlan (elegos) — original work
# Copyright (C) 2026 loteran — modifications
# SPDX-License-Identifier: GPL-3.0-or-later

import asyncio
import json
import locale
import logging
import subprocess
from logging import Logger
from pathlib import Path
from threading import Thread
from time import sleep

from dbus_next.aio.message_bus import MessageBus
from dbus_next.constants import MessageType
from dbus_next.message import Message
from PySide6.QtCore import Signal, Slot
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from arctis_sound_manager.constants import (DBUS_BUS_NAME,
                                            DBUS_STATUS_INTERFACE_NAME,
                                            DBUS_STATUS_OBJECT_PATH)
from arctis_sound_manager.gui.base_app import QBaseDesktopApp
from arctis_sound_manager.gui.dbus_wrapper import DbusWrapper
from arctis_sound_manager.gui.main_app import QMainApp
from arctis_sound_manager.gui.tray_eq_presets import (
    SONAR_CHANNELS,
    SonarPresetApplier,
    apply_custom_preset,
    current_eq_mode,
    get_sonar_active_preset,
    list_custom_presets,
    list_sonar_channel_presets,
)
from arctis_sound_manager.gui.ui_utils import get_icon_pixmap
from arctis_sound_manager.i18n import I18n


class QSystrayApp(QBaseDesktopApp):
    new_status = Signal(object)

    logger: Logger

    app: QApplication
    tray_icon: QSystemTrayIcon
    menu: QMenu
    dbus_bus: MessageBus

    last_device_status: dict[str, dict[str, dict[str, str|int]]]

    def __init__(self, app: QApplication, log_level: int):
        super().__init__(app)

        self.logger = logging.getLogger('SystrayApp')
        self.logger.setLevel(log_level)

        self.app = app

        pixmap = get_icon_pixmap()
        self.tray_icon = QSystemTrayIcon(QIcon(pixmap), parent=self.app)
        self.tray_icon.setToolTip('Arctis Sound Manager')

        lang_code, _ = locale.getdefaultlocale()
        lang_code = lang_code.split('_')[0] if lang_code else 'en'

        self.last_device_status = {}
        DbusWrapper.show_splash()

        self._sonar_applier = SonarPresetApplier(self)
        self._sonar_applier.done.connect(self._on_sonar_preset_applied)

        self.menu = QMenu()
        # Connect signals once on the persistent menu object
        self.menu.aboutToShow.connect(self.start_polling)
        self.menu.aboutToHide.connect(self.stop_polling)
        self.tray_icon.setContextMenu(self.menu)
        self.menu_setup()
        self.do_polling = False

        self.new_status.connect(self.on_new_status)
        self.dbus_poll_thread = Thread(target=self.poll_dbus_thread, daemon=True)
        self.dbus_poll_thread.start()

    def start_polling(self):
        # Rebuild the menu NOW, before the popup window is created by Qt.
        # This avoids calling menu.clear() while the popup Wayland surface is
        # already alive (which causes a use-after-free SIGSEGV in Qt Wayland).
        self.menu_setup()
        self.do_polling = True

    def stop_polling(self):
        self.do_polling = False

    def poll_dbus_thread(self):
        while not self.is_stopping():
            if self.do_polling:
                asyncio.run(self.dbus_poll())
                sleep(2)
            else:
                # Wait for do_polling faster
                sleep(.5)

    async def dbus_poll(self):
        self.logger.debug('Polling dbus...')

        dbus_bus = await MessageBus().connect()
        try:
            reply = await dbus_bus.call(Message(
                destination=DBUS_BUS_NAME,
                path=DBUS_STATUS_OBJECT_PATH,
                interface=DBUS_STATUS_INTERFACE_NAME,
                member='GetStatus',
                message_type=MessageType.METHOD_CALL
            ))

            if reply is None:
                self.logger.error('Error getting status: no reply')
                return

            if reply.message_type == MessageType.ERROR:
                self.logger.error('Error getting status: %s', reply.body)
                return

            self.new_status.emit(json.loads(reply.body[0]) or {})
        except Exception as e:
            self.logger.error('Error polling dbus: %s', e)
        finally:
            dbus_bus.disconnect()

    def on_new_status(self, status: dict[str, dict[str, dict[str, str|int]]]):
        if self.last_device_status == status:
            return

        self.last_device_status = status
        # Do NOT call menu_setup() here: the menu popup window may already be
        # visible (Wayland surface exists) and clear()-ing it while paint events
        # are queued causes a use-after-free SIGSEGV in QWaylandWindow.
        # menu_setup() is called in start_polling() instead, just before Qt
        # creates the popup surface.

    async def start(self):
        self.logger.info('Starting Systray app.')
        self.tray_icon.show()

        from arctis_sound_manager.init_system import detect_init
        if detect_init() == "dinit":
            subprocess.run(["dinitctl", "start", "pipewire-filter-chain"], check=False)
        else:
            subprocess.run(
                ["systemctl", "--user", "start", "filter-chain"],
                capture_output=True,
            )

        # Save the current default sink, then claim it for ASM so apps that
        # open after ASM starts route to Arctis_Game instead of EasyEffects
        # or whatever was default before. Restored in sig_stop().
        result = subprocess.run(
            ["pactl", "get-default-sink"], capture_output=True, text=True
        )
        self._previous_default_sink = result.stdout.strip()
        subprocess.run(
            ["pactl", "set-default-sink", "Arctis_Game"], capture_output=True
        )

        self.dbus_bus = await MessageBus().connect()

        # Pre-fetch status immediately so the tray menu shows headset info
        # on the very first click (without waiting for the 2s poll cycle).
        self.do_polling = True
        await self.dbus_poll()

        self.app.exec()

    def menu_setup(self) -> None:
        self.menu.clear()
        self._menu_actions = {}
        # Keep explicit Python refs to ALL QActions so PySide6 GC doesn't
        # destroy them before KDE dbusmenu reads them.
        self._menu_action_refs: list = []

        def _add(action):
            self._menu_action_refs.append(action)
            self.menu.addAction(action)
            return action

        def _sep():
            a = self.menu.addSeparator()
            self._menu_action_refs.append(a)
            return a

        # Open App
        self._menu_actions['open_app'] = _add(QAction(I18n.translate('ui', 'open_app')))
        self._menu_actions['open_app'].triggered.connect(self.open_main_window)

        # Profiles
        try:
            from arctis_sound_manager.profile_manager import Profile, active_profile_name
            _sep()
            profiles = Profile.list_all()
            if profiles:
                active = active_profile_name()
                for profile in profiles:
                    marker = "● " if profile.name == active else "    "
                    a = _add(QAction(f"{marker}{profile.name}"))
                    a.triggered.connect(lambda _=False, p=profile: self._on_tray_profile(p))
            else:
                _add(QAction(f"— {I18n.translate('ui', 'no_profiles_saved')} —"))
        except Exception as e:
            self.logger.error('profiles section failed: %s', e, exc_info=True)

        # EQ presets (nested submenu)
        try:
            mode = current_eq_mode()
            label = I18n.translate('ui', 'eq_presets') + f" ({('Sonar' if mode == 'sonar' else 'Custom EQ')})"
            eq_menu = QMenu(label)
            self._menu_action_refs.append(eq_menu)

            if mode == "custom":
                presets = list_custom_presets()
                if presets:
                    for preset_name in presets:
                        a = eq_menu.addAction(f"    {preset_name}")
                        self._menu_action_refs.append(a)
                        a.triggered.connect(
                            lambda _=False, n=preset_name: self._on_tray_eq_preset("custom", "", n)
                        )
                else:
                    eq_menu.addAction(f"— {I18n.translate('ui', 'no_presets_saved')} —")
            else:
                no_presets_label = f"— {I18n.translate('ui', 'no_presets_saved')} —"
                for ch_key, _ch_label in SONAR_CHANNELS:
                    ch_menu = QMenu(I18n.translate('ui', ch_key))
                    self._menu_action_refs.append(ch_menu)
                    favs = list_sonar_channel_presets(ch_key)
                    if favs:
                        active = get_sonar_active_preset(ch_key)
                        for preset_name in favs:
                            marker = "● " if preset_name == active else "    "
                            a = ch_menu.addAction(f"{marker}{preset_name}")
                            self._menu_action_refs.append(a)
                            a.triggered.connect(
                                lambda _=False, ch=ch_key, n=preset_name: self._on_tray_eq_preset("sonar", ch, n)
                            )
                    else:
                        ch_menu.addAction(no_presets_label)
                    eq_menu.addMenu(ch_menu)

            _sep()
            self.menu.addMenu(eq_menu)
        except Exception as e:
            self.logger.error('EQ presets section failed: %s', e, exc_info=True)

        # Output Routing (per-channel physical sink selection)
        try:
            import pulsectl
            import json as _json
            from pathlib import Path as _Path
            _outputs_file = _Path.home() / ".config" / "arctis_manager" / "channel_output_devices.json"
            _ch_outputs: dict = {}
            if _outputs_file.exists():
                try:
                    _ch_outputs = _json.loads(_outputs_file.read_text())
                except Exception:
                    pass

            with pulsectl.Pulse("asm-tray-routing") as _pulse:
                _sinks = _pulse.sink_list()
            _physical_sinks = [
                s for s in _sinks
                if s.name.startswith("alsa_output") and "SteelSeries" not in s.name
            ]

            _routing_menu = QMenu(I18n.translate('ui', 'output_routing'))
            self._menu_action_refs.append(_routing_menu)

            _ch_labels = [
                ("game", I18n.translate("ui", "game")),
                ("chat", I18n.translate("ui", "chat")),
                ("media", I18n.translate("ui", "media")),
            ]
            _default_label = I18n.translate("ui", "default_output")
            for _ch_key, _ch_label in _ch_labels:
                _ch_menu = QMenu(_ch_label)
                self._menu_action_refs.append(_ch_menu)
                _current_sink = _ch_outputs.get(_ch_key, "")

                _a_def = _ch_menu.addAction(("● " if not _current_sink else "    ") + _default_label)
                self._menu_action_refs.append(_a_def)
                _a_def.triggered.connect(
                    lambda _=False, ch=_ch_key: self._on_tray_channel_output(ch, "")
                )

                for _snk in _physical_sinks:
                    _nick = _snk.proplist.get("node.description") or _snk.proplist.get("node.nick") or _snk.name
                    _marker = "● " if _snk.name == _current_sink else "    "
                    _a = _ch_menu.addAction(f"{_marker}{_nick}")
                    self._menu_action_refs.append(_a)
                    _a.triggered.connect(
                        lambda _=False, ch=_ch_key, name=_snk.name: self._on_tray_channel_output(ch, name)
                    )

                _routing_menu.addMenu(_ch_menu)

            _sep()
            self.menu.addMenu(_routing_menu)
        except Exception as _e:
            self.logger.debug('Output routing section failed: %s', _e)

        # Headset status (power only)
        for _, status_obj in self.last_device_status.items():
            if not status_obj:
                continue
            power = status_obj.get('headset_power_status')
            if power:
                _sep()
                self._menu_actions['headset_status'] = _add(QAction(
                    f"{I18n.translate('ui', 'headset_status')}: "
                    f"{I18n.translate('status_values', power['value'])}"
                ))

        _sep()

        # Exit
        self._menu_actions['exit'] = _add(QAction(I18n.translate('ui', 'exit')))
        self._menu_actions['exit'].triggered.connect(self.sig_stop)

    def _on_tray_profile(self, profile) -> None:
        from arctis_sound_manager.profile_manager import apply_profile
        apply_profile(profile)
        # Rebuild menu to update active marker
        self.menu_setup()
        # Note: EQ re-apply only happens when GUI is open

    def _on_tray_eq_preset(self, mode: str, channel: str, name: str) -> None:
        if mode == "custom":
            ok = apply_custom_preset(name)
            if not ok:
                self.logger.warning("Custom preset '%s' not found", name)
        else:
            if self._sonar_applier.is_running():
                return
            self.tray_icon.setToolTip(
                f"{I18n.translate('ui', 'applying_preset')} {name}"
            )
            self._sonar_applier.apply(channel, name)

    def _on_tray_channel_output(self, channel: str, sink_name: str) -> None:
        try:
            import json as _json
            from pathlib import Path as _Path
            _outputs_file = _Path.home() / ".config" / "arctis_manager" / "channel_output_devices.json"
            _ch_outputs: dict = {}
            if _outputs_file.exists():
                try:
                    _ch_outputs = _json.loads(_outputs_file.read_text())
                except Exception:
                    pass
            if sink_name:
                _ch_outputs[channel] = sink_name
            else:
                _ch_outputs.pop(channel, None)
            _outputs_file.parent.mkdir(parents=True, exist_ok=True)
            _tmp = _outputs_file.with_suffix(".tmp")
            _tmp.write_text(_json.dumps(_ch_outputs))
            _tmp.replace(_outputs_file)
        except Exception as e:
            self.logger.warning("Failed to save channel output: %s", e)
        self.menu_setup()

    @Slot(bool, str, str)
    def _on_sonar_preset_applied(self, ok: bool, channel: str, name: str) -> None:
        if ok:
            self.tray_icon.setToolTip("Arctis Sound Manager")
            self.menu_setup()
            try:
                if hasattr(self, '_main_app'):
                    self._main_app._equalizer_page._sonar_page.notify_external_preset_change(
                        channel, name
                    )
            except Exception as e:
                self.logger.warning("Could not refresh sonar page after tray apply: %s", e)
        else:
            self.tray_icon.setToolTip(
                f"{I18n.translate('ui', 'preset_apply_failed')}: {name}"
            )

    def is_stopping(self):
        return hasattr(self, '_stopping') and self._stopping

    def open_main_window(self):
        if not hasattr(self, '_main_app'):
            self._main_app = QMainApp(self.app, self.logger.level)

        self._main_app.main_window.show()
        self._main_app.main_window.raise_()
        self._main_app.main_window.activateWindow()

    def import_preset_url(self, url: str) -> None:
        """Handle an arctis-asm:// deep link — open the import dialog pre-filled."""
        self.open_main_window()
        from PySide6.QtCore import QTimer

        def _open_dialog():
            from arctis_sound_manager.gui.preset_import_dialog import PresetImportDialog
            parent = self._main_app.main_window if hasattr(self, '_main_app') else None
            dlg = PresetImportDialog("game", parent)
            dlg._url_edit.setText(url)
            QTimer.singleShot(100, dlg._on_import)
            dlg.exec()

        QTimer.singleShot(300, _open_dialog)

    @Slot()
    def sig_stop(self):
        if self.is_stopping():
            return

        if hasattr(self, '_main_app'):
            self._main_app.sig_stop()

        self._stopping = True
        self.logger.debug('Received shutdown signal, shutting down.')

        # Restore whichever default sink was active before ASM started,
        # so EasyEffects (or hardware) takes over immediately after exit.
        prev = getattr(self, '_previous_default_sink', '')
        if prev and not prev.startswith(('Arctis_', 'effect_input.')):
            subprocess.run(
                ["pactl", "set-default-sink", prev], capture_output=True, timeout=2
            )

        # Stop all ASM services and schedule a pipewire restart
        # so the system behaves as if ASM was not installed.
        from arctis_sound_manager.init_system import detect_init
        if detect_init() == "dinit":
            for svc in ["arctis-manager", "arctis-video-router", "pipewire-filter-chain"]:
                subprocess.run(["dinitctl", "stop", svc], check=False)
        else:
            try:
                subprocess.run(
                    ["systemctl", "--user", "stop",
                     "arctis-manager.service", "arctis-video-router.service", "filter-chain"],
                    capture_output=True, timeout=10,
                )
            except subprocess.TimeoutExpired:
                self.logger.warning("systemctl stop timed out — killing services")
                subprocess.run(
                    ["systemctl", "--user", "kill",
                     "arctis-manager.service", "arctis-video-router.service", "filter-chain"],
                    capture_output=True,
                )
        # Deferred restart: the app exits first, then pipewire restarts
        # without ASM configs (filter-chain is already stopped).
        if detect_init() == "dinit":
            subprocess.Popen(["bash", "-c",
                "sleep 1 && dinitctl restart pipewire "
                "&& dinitctl restart wireplumber "
                "&& dinitctl restart pipewire-pulse"],
                start_new_session=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        else:
            subprocess.Popen(
                ["bash", "-c",
                 "sleep 1 && systemctl --user restart pipewire wireplumber pipewire-pulse"],
                start_new_session=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )

        self.app.quit()
