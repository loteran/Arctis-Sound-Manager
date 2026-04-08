import asyncio
import logging
import signal
import sys
from types import FrameType

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

    core_loop = asyncio.create_task(core_engine.start())
    dbus_manager_loop = asyncio.create_task(DbusAwake.get_instance().start(core_engine))

    await dbus_manager.start(core_engine)

    await dbus_manager.wait_for_stop()
    await core_loop

    dbus_manager_loop.cancel()

def sigterm_handler(
        sig: int,
        frame: FrameType|None = None
    ) -> None:
    DbusManager.getInstance().stop()

def main():
    def _crash_handler(exc_type, exc_value, exc_tb):
        logging.getLogger('Daemon').critical(
            'Unhandled exception — writing crash report', exc_info=(exc_type, exc_value, exc_tb)
        )
        write_crash_report(exc_type, exc_value, exc_tb, source='daemon')
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = _crash_handler

    signal.signal(signal.SIGINT, sigterm_handler)
    signal.signal(signal.SIGTERM, sigterm_handler)

    asyncio.run(main_async())

if __name__ == '__main__':
    main()
