import asyncio
import logging
import signal
import sys

from arctis_sound_manager.bug_reporter import write_crash_report
from arctis_sound_manager.core import CoreEngine
from arctis_sound_manager.dbus_service import DbusManager
from arctis_sound_manager.scripts.dbus_awake import DbusAwake
from arctis_sound_manager.utils import project_version


async def main_async():
    logging.basicConfig(level=logging.INFO, format='[%(levelname)7s] %(name)20s: %(message)s')

    logger = logging.getLogger('Daemon')
    logger.info('-------------------------------')
    logger.info('- Arctis Sound Manager is starting. -')
    logger.info(f'-{"v " + project_version():>27}  -')
    logger.info('-------------------------------')

    from arctis_sound_manager.udev_checker import is_udev_rules_valid
    if not is_udev_rules_valid():
        logger.warning('udev rules are missing or invalid — USB access may fail (errno 13). Run: sudo asm-cli udev write-rules --force --reload')

    dbus_manager = DbusManager.getInstance()
    core_engine = CoreEngine()

    core_loop = asyncio.create_task(core_engine.start(), name='core-loop')
    dbus_awake_loop = asyncio.create_task(DbusAwake.get_instance().start(core_engine), name='dbus-awake')

    # Wire SIGINT/SIGTERM into the asyncio loop so the handler can both flip
    # the stop flags AND cancel the long-lived tasks. Plain signal.signal()
    # only fires the python handler between asyncio await points, which is
    # why a stuck await (e.g. blocked usb read) previously kept the daemon
    # alive after Ctrl-C.
    loop = asyncio.get_running_loop()

    def _shutdown(sig_name: str) -> None:
        logger.info(f'Received {sig_name} — shutting down.')
        try:
            core_engine.stop()
        except Exception as e:
            logger.warning(f'core_engine.stop() raised: {e!r}')
        try:
            dbus_manager.stop()
        except Exception as e:
            logger.warning(f'dbus_manager.stop() raised: {e!r}')
        for task in (core_loop, dbus_awake_loop):
            if not task.done():
                task.cancel()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _shutdown, sig.name)
        except (NotImplementedError, RuntimeError):
            # Windows / restricted runtimes — fall back to the plain handler;
            # at least the flags will flip.
            signal.signal(sig, lambda *_: _shutdown(signal.Signals(sig).name))

    try:
        await dbus_manager.start(core_engine)
        await dbus_manager.wait_for_stop()
    finally:
        # Best-effort: make sure background tasks really exit.
        for task in (core_loop, dbus_awake_loop):
            if not task.done():
                task.cancel()
        await asyncio.gather(core_loop, dbus_awake_loop, return_exceptions=True)


def main():
    def _crash_handler(exc_type, exc_value, exc_tb):
        logging.getLogger('Daemon').critical(
            'Unhandled exception — writing crash report', exc_info=(exc_type, exc_value, exc_tb)
        )
        write_crash_report(exc_type, exc_value, exc_tb, source='daemon')
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = _crash_handler

    asyncio.run(main_async())


if __name__ == '__main__':
    main()
