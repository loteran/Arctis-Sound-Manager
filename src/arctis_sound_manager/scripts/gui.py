# Copyright (C) 2022 Giacomo Furlan (elegos) — original work
# Copyright (C) 2026 loteran — modifications
# SPDX-License-Identifier: GPL-3.0-or-later

import asyncio
import logging
import os
import signal
import sys
from argparse import ArgumentParser

log = logging.getLogger(__name__)


def _check_display_or_exit() -> None:
    """Refuse to start cleanly on a system with no graphical session.

    Without DISPLAY or WAYLAND_DISPLAY, Qt aborts with an opaque XCB error
    and a giant traceback. We pre-check and emit a focused message instead,
    so users running the GUI from SSH, a TTY, or a too-early autostart hook
    know exactly what's wrong and how to fix it.
    """
    if os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY'):
        return
    sys.stderr.write(
        "asm-gui: cannot start — no graphical session is active.\n"
        "\n"
        "Neither $DISPLAY (X11 / XLibre / Xorg) nor $WAYLAND_DISPLAY (Wayland)\n"
        "is set in this shell. asm-gui needs a running desktop session.\n"
        "\n"
        "Common causes:\n"
        "  1. Launched from a TTY or SSH without display forwarding.\n"
        "     Open a terminal inside your desktop session, or use ssh -X.\n"
        "  2. Autostart fired before the compositor was ready (typical on\n"
        "     minimal WMs like i3/openbox/XLibre on Artix + dinit).\n"
        "     Re-login, or run `asm-gui --systray &` once the WM is up.\n"
        "  3. asm-setup has not been run yet — run it from your desktop\n"
        "     session to configure autostart and PipeWire configs.\n"
        "\n"
        "Headless/CI only: set QT_QPA_PLATFORM=offscreen.\n"
    )
    sys.exit(2)


def _import_qt_or_exit():
    """Import PySide6 with a focused error if QtWayland or Qt platform plugins
    are missing — the most common breakage on Wayland-only distros (Fedora 41+,
    RHEL 9+) when the user installed PySide6 via pip without `qt6-wayland`."""
    try:
        from PySide6.QtCore import QTimer
        from PySide6.QtNetwork import QLocalServer, QLocalSocket
        from PySide6.QtWidgets import QApplication, QDialog
        return QTimer, QLocalServer, QLocalSocket, QApplication, QDialog
    except ImportError as e:
        sys.stderr.write(
            f"asm-gui: failed to import PySide6 ({e}).\n"
            "  - On Wayland-only distros, install the platform plugin:\n"
            "      Fedora/Nobara : sudo dnf install qt6-qtwayland\n"
            "      Arch/Cachy    : sudo pacman -S qt6-wayland\n"
            "      Debian/Ubuntu : sudo apt install qt6-wayland\n"
            "  - On X11, install qt6-base / python3-pyside6.qtwidgets.\n"
        )
        sys.exit(3)


_SERVER_NAME = "ArctisManagerGui"

# Basename of the installed desktop entry, without ".desktop". This is the
# identity the XDG portals key their per-application state to — see where it is
# applied in main().
DESKTOP_ENTRY_NAME = "ArctisManager"


def hand_off_to_running_instance(local_socket_cls, message: bytes,
                                 reply_timeout_ms: int = 5000) -> bool:
    """Give *message* to the instance that already owns this session.

    Returns True when there was one — the caller must then exit without
    starting a GUI of its own.

    A connection that gets *accepted* is proof enough that another instance is
    there: connecting to a Unix socket no process listens on is refused, it
    does not hang. So once we are connected we hand the command over and get
    out of the way, however long the reply takes.

    Waiting for "ok" is only about how quickly it was picked up. The command is
    already sitting in the socket buffer, and the running instance reads it
    once it catches up, whether or not we are still there to hear it.
    """
    socket = local_socket_cls()
    socket.connectToServer(_SERVER_NAME)
    if not socket.waitForConnected(1500):
        return False
    socket.write(message)
    socket.flush()
    socket.waitForBytesWritten(1000)
    socket.waitForReadyRead(reply_timeout_ms)
    socket.disconnectFromServer()
    return True


def claim_instance_server(local_server_cls, local_socket_cls, message: bytes):
    """Listen on the single-instance socket as the instance that owns it.

    Returns the listening server, or None when another instance won the race
    and has been handed *message* instead — the caller must then exit.

    listen() first, and reach for ``removeServer()`` only once it has refused.
    removeServer() unlinks the socket file whether or not somebody is listening
    on it, so calling it up front is how a live instance loses its socket to a
    launch that raced it — and how the session ends up with two GUIs.
    """
    server = local_server_cls()
    if server.listen(_SERVER_NAME):
        return server
    # Refused: either a live instance claimed the name in the moment between
    # our connect being refused and now, or the file was left behind by an
    # instance that was killed. Ask before assuming the second one.
    if hand_off_to_running_instance(local_socket_cls, message):
        return None
    local_server_cls.removeServer(_SERVER_NAME)
    if not server.listen(_SERVER_NAME):
        log.warning(
            "Could not listen on the single-instance socket (%s) — a later "
            "launch will not be able to reach this window.",
            server.errorString(),
        )
    return server


def main():
    parser = ArgumentParser()
    parser.add_argument('--systray', action='store_true',
                        help='Start systray without opening window (for autostart at login)')
    parser.add_argument('--verbose', '-v', action='count', default=0, help='Increase verbosity (up to -vvvv)')
    parser.add_argument('--no-enforce-systemd', action='store_true', help='Do not enforce systemd unit')
    parser.add_argument('url', nargs='?', default=None,
                        help='arctis-asm:// URL to handle (invoked by xdg-open)')
    args = parser.parse_args()

    # Default base level depends on -v flags (CRITICAL→…→DEBUG), but ARCTIS_LOG_LEVEL
    # always wins so users can crank verbosity for bug reports without restarting the GUI.
    base_level = logging.CRITICAL
    for _ in range(args.verbose):
        base_level -= 10
    if base_level < logging.DEBUG:
        base_level = logging.DEBUG

    from arctis_sound_manager.log_setup import resolve_level
    log_level = resolve_level(default=base_level)
    logging.basicConfig(level=log_level, format='%(name)20s %(levelname)8s | %(message)s')

    _check_display_or_exit()
    QTimer, QLocalServer, QLocalSocket, QApplication, QDialog = _import_qt_or_exit()

    from arctis_sound_manager.bug_reporter import read_crash_report, write_crash_report
    from arctis_sound_manager.gui.systray_app import QSystrayApp
    from arctis_sound_manager.systemd import ensure_systemd_unit

    try:
        app = QApplication(sys.argv)
    except Exception as e:
        sys.stderr.write(
            f"asm-gui: QApplication() failed to initialize ({e}).\n"
            "  - Check that the Qt platform plugin matches your session "
            "(WAYLAND_DISPLAY → wayland, DISPLAY → xcb).\n"
            "  - Try forcing a backend: QT_QPA_PLATFORM=xcb asm-gui\n"
        )
        sys.exit(4)

    # ── Desktop identity ──────────────────────────────────────────────────────
    #
    # The portals identify an application by its desktop entry, and Qt only
    # reports one when it is told which. Without this the session ran as app id
    # "" — KDE logged "Could not register app ID: App info not found for ''" —
    # and both portal features that key their state to the app id failed
    # silently:
    #
    #   * ScreenCast restore tokens are stored per app, so the token saved
    #     after every capture never restored: the screen picker appeared on
    #     every single clip, forever, instead of once.
    #   * GlobalShortcuts binds per app, so the shortcut was granted by the
    #     user and then never routed back — Alt+F did nothing at all.
    #
    # The name must match the installed entry (ArctisManager.desktop) with no
    # extension; anything else is as good as none.
    app.setDesktopFileName(DESKTOP_ENTRY_NAME)
    app.setApplicationName("Arctis Sound Manager")

    # ── Crash handler ─────────────────────────────────────────────────────────
    def _gui_crash_handler(exc_type, exc_value, exc_tb):
        write_crash_report(exc_type, exc_value, exc_tb, source='gui')
        sys.__excepthook__(exc_type, exc_value, exc_tb)
    sys.excepthook = _gui_crash_handler

    # ── Single-instance guard ──────────────────────────────────────────────────
    # This used to give up after 500 ms of silence, delete the socket file and
    # start a second full GUI. At login that is exactly what happens: the first
    # instance starts listening immediately but does not reach its event loop
    # until the tray, D-Bus and PipeWire setup are done, several seconds later.
    # The second launch — the systemd user unit and the desktop entry (or the
    # desktop's session restore) fire within a second of each other — connected,
    # heard nothing back, took the socket away and ran a whole second GUI
    # alongside the first. Two windows opening one after the other, two tray
    # icons, both driving the same daemon.
    if args.url:
        msg = b"url:" + args.url.encode()
    else:
        msg = b"show" if not args.systray else b"alive"
    if hand_off_to_running_instance(QLocalSocket, msg):
        return

    # ── First instance: start systray + IPC server ────────────────────────────
    server = claim_instance_server(QLocalServer, QLocalSocket, msg)
    if server is None:
        return

    app.setQuitOnLastWindowClosed(False)
    q_object = QSystrayApp(app, log_level)

    def _on_new_connection():
        conn = server.nextPendingConnection()
        if conn:
            conn.waitForReadyRead(300)
            data = bytes(conn.readAll())
            conn.write(b"ok")
            conn.flush()
            conn.waitForBytesWritten(300)
            conn.disconnectFromServer()
            if data == b"show":
                q_object.open_main_window()
            elif data.startswith(b"url:"):
                url = data[4:].decode(errors="replace")
                q_object.import_preset_url(url)

    server.newConnection.connect(_on_new_connection)

    # ── First-run setup (pipx installs that never ran asm-setup) ──────────────
    # Distro packages (deb/rpm/AUR) install /etc/xdg/autostart/asm-first-run.desktop
    # which auto-runs asm-setup on first login. Pipx installs don't get that, so
    # the user has to either run asm-setup manually OR end up with broken audio.
    # Detect the missing flag and trigger setup from inside the GUI itself.
    from pathlib import Path as _Path
    setup_done_flag = _Path.home() / ".config" / "arctis_manager" / ".setup_done"
    setup_was_missing = not setup_done_flag.exists()
    if setup_was_missing:
        from arctis_sound_manager.gui.first_run_dialog import FirstRunDialog
        def _first_run():
            FirstRunDialog().exec()
        QTimer.singleShot(300, _first_run)

    # ── Crash report from previous session ────────────────────────────────────
    crash = read_crash_report()
    if crash:
        from arctis_sound_manager.gui.report_dialog import ReportBugDialog
        def _show_crash():
            ReportBugDialog(traceback_str=crash.get('traceback'), is_crash=True).exec()
        QTimer.singleShot(1500, _show_crash)

    # ── udev rules check ──────────────────────────────────────────────────────
    # Run AFTER the first-run dialog has had time to install rules (delay 2500ms
    # vs 300ms for first-run). On a normal launch (setup already done), this
    # fires at 500ms as before.
    from arctis_sound_manager.udev_checker import get_udev_rules_status
    udev_delay_ms = 2500 if setup_was_missing else 500
    def _check_udev():
        status = get_udev_rules_status()
        if status == 'ok':
            return
        if status == 'missing':
            from arctis_sound_manager.gui.udev_dialog import UdevRulesDialog
            UdevRulesDialog().exec()
        elif status == 'outdated':
            # Rules file exists but new device YAMLs added PIDs not yet in it.
            # Silently re-write — one pkexec prompt from the OS, no Qt dialog.
            import shutil
            import subprocess
            cli = shutil.which('asm-cli')
            if cli:
                subprocess.run([cli, 'udev', 'write-rules', '--force', '--reload'], check=False)
            else:
                from arctis_sound_manager.gui.udev_dialog import UdevRulesDialog
                UdevRulesDialog().exec()
    QTimer.singleShot(udev_delay_ms, _check_udev)

    # ── Telemetry consent (first launch only) ─────────────────────────────────
    from arctis_sound_manager.telemetry import get_consent, set_consent
    if get_consent() is None:
        from arctis_sound_manager.gui.telemetry_dialog import TelemetryConsentDialog
        def _ask_telemetry():
            dlg = TelemetryConsentDialog()
            set_consent(dlg.exec() == QDialog.Accepted)
        QTimer.singleShot(2000, _ask_telemetry)

    # ── System deps self-healing dialog (Phase 4 of ASM_PLAN_DEPS_CHECK) ──
    # Re-runs the system_deps_checker registry shared with --verify-setup;
    # if any BLOCKING or DEGRADED dep is missing, offers a one-click pkexec
    # install per matching distro. The OPTIONAL `gh` CLI never triggers
    # the dialog. Skip-marker written by the user is honoured so we don't
    # nag on every launch — it expires automatically on ASM upgrade.
    def _check_deps():
        from arctis_sound_manager.gui.system_deps_dialog import (
            SystemDepsDialog, should_show_dialog,
        )
        if should_show_dialog():
            SystemDepsDialog().exec()
    QTimer.singleShot(2500, _check_deps)

    # -- Preset sync (new Sonar presets added since install) ------------------
    def _sync_presets():
        from arctis_sound_manager.preset_sync import PresetSyncWorker
        # force=True: check on every launch, not once a day. New Sonar preset
        # packs land at unpredictable times, and with the daily cache alone a
        # check that ran shortly before a batch was published left the user
        # without it until the next day, with no way to ask for one.
        w = PresetSyncWorker(force=True)
        w.new_presets_added.connect(
            lambda n: log.info("Preset sync: %d new preset(s) available.", n)
        )
        w.finished.connect(w.deleteLater)
        _sync_presets._worker = w  # prevent GC until thread finishes
        w.start()

    QTimer.singleShot(6000, _sync_presets)

    # Open the window once the event loop is running.
    if args.url:
        _url_to_handle = args.url
        QTimer.singleShot(500, lambda: q_object.import_preset_url(_url_to_handle))
    elif not args.systray:
        QTimer.singleShot(0, q_object.open_main_window)

    if not args.no_enforce_systemd:
        ensure_systemd_unit(True)

    timer = QTimer()
    timer.timeout.connect(lambda: None)
    timer.start(500)

    def stop_app(*_) -> None:
        q_object.sig_stop()
        if timer.isActive():
            timer.stop()
        server.close()
        QLocalServer.removeServer(_SERVER_NAME)

    signal.signal(signal.SIGINT, stop_app)
    signal.signal(signal.SIGTERM, stop_app)

    asyncio.run(q_object.start())


if __name__ == '__main__':
    main()
