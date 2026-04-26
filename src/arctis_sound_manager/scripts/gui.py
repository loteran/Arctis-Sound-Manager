import asyncio
import logging
import os
import signal
import sys
from argparse import ArgumentParser


def _check_display_or_exit() -> None:
    """Refuse to start cleanly on a system with no graphical session.

    Without DISPLAY or WAYLAND_DISPLAY, Qt aborts with an opaque XCB error
    and a giant traceback. We pre-check and emit a focused message instead,
    so users running the GUI from SSH or a TTY know exactly what's wrong.
    """
    if os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY'):
        return
    sys.stderr.write(
        "asm-gui: no graphical session detected (DISPLAY and WAYLAND_DISPLAY are unset).\n"
        "  - Run from a desktop session, or set QT_QPA_PLATFORM=offscreen for headless tests.\n"
        "  - On a remote host, enable X11 forwarding (ssh -X) or use Wayland forwarding.\n"
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


def main():
    parser = ArgumentParser()
    parser.add_argument('--systray', action='store_true',
                        help='Start systray without opening window (for autostart at login)')
    parser.add_argument('--verbose', '-v', action='count', default=0, help='Increase verbosity (up to -vvvv)')
    parser.add_argument('--no-enforce-systemd', action='store_true', help='Do not enforce systemd unit')
    args = parser.parse_args()

    log_level = logging.CRITICAL
    for _ in range(args.verbose):
        log_level -= 10
    if log_level < logging.DEBUG:
        log_level = logging.DEBUG

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

    # ── Crash handler ─────────────────────────────────────────────────────────
    def _gui_crash_handler(exc_type, exc_value, exc_tb):
        write_crash_report(exc_type, exc_value, exc_tb, source='gui')
        sys.__excepthook__(exc_type, exc_value, exc_tb)
    sys.excepthook = _gui_crash_handler

    # ── Single-instance guard ──────────────────────────────────────────────────
    socket = QLocalSocket()
    socket.connectToServer(_SERVER_NAME)
    if socket.waitForConnected(300):
        # Send the command and wait for "ok" to confirm the instance is alive.
        msg = b"show" if not args.systray else b"alive"
        socket.write(msg)
        socket.flush()
        if socket.waitForReadyRead(500):
            socket.disconnectFromServer()
            return
        # No response → stale socket file, clean up and continue.
        socket.disconnectFromServer()
        QLocalServer.removeServer(_SERVER_NAME)

    # ── First instance: start systray + IPC server ────────────────────────────
    QLocalServer.removeServer(_SERVER_NAME)
    server = QLocalServer()
    server.listen(_SERVER_NAME)

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

    server.newConnection.connect(_on_new_connection)

    # ── Crash report from previous session ────────────────────────────────────
    crash = read_crash_report()
    if crash:
        from arctis_sound_manager.gui.report_dialog import ReportBugDialog
        def _show_crash():
            ReportBugDialog(traceback_str=crash.get('traceback'), is_crash=True).exec()
        QTimer.singleShot(1500, _show_crash)

    # ── udev rules check ──────────────────────────────────────────────────────
    from arctis_sound_manager.udev_checker import is_udev_rules_valid
    if not is_udev_rules_valid():
        from arctis_sound_manager.gui.udev_dialog import UdevRulesDialog
        def _check_udev():
            UdevRulesDialog().exec()
        QTimer.singleShot(500, _check_udev)

    # ── Telemetry consent (first launch only) ─────────────────────────────────
    from arctis_sound_manager.telemetry import get_consent, set_consent
    if get_consent() is None:
        from arctis_sound_manager.gui.telemetry_dialog import TelemetryConsentDialog
        def _ask_telemetry():
            dlg = TelemetryConsentDialog()
            set_consent(dlg.exec() == QDialog.Accepted)
        QTimer.singleShot(2000, _ask_telemetry)

    # Open the window once the event loop is running.
    if not args.systray:
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
