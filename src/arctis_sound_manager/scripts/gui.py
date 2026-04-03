import asyncio
import logging
import signal
import sys
from argparse import ArgumentParser

from PySide6.QtCore import QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication

from arctis_sound_manager.bug_reporter import read_crash_report, write_crash_report
from arctis_sound_manager.gui.systray_app import QSystrayApp
from arctis_sound_manager.systemd import ensure_systemd_unit

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

    app = QApplication(sys.argv)

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
