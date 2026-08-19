# Copyright (C) 2022 Giacomo Furlan (elegos) — original work
# Copyright (C) 2026 loteran — modifications
# SPDX-License-Identifier: GPL-3.0-or-later

import argparse
import asyncio
import logging
import os
import signal
import sys

from arctis_sound_manager.bug_reporter import write_crash_report
from arctis_sound_manager.core import CoreEngine
from arctis_sound_manager.dbus_service import DbusManager
from arctis_sound_manager.scripts.dbus_awake import DbusAwake
from arctis_sound_manager.utils import project_version


def verify_setup() -> int:
    """Run preflight checks and exit with 0 (all green) or 1 (issues found).

    Doesn't touch USB or claim D-Bus — safe to call before launching the
    real daemon, in CI, or as a quick "is my install OK?" probe.
    """
    from arctis_sound_manager.log_setup import configure_logging
    configure_logging(default=logging.INFO, fmt='[%(levelname)7s] %(message)s')
    log = logging.getLogger('verify-setup')

    issues = 0
    log.info(f'Arctis Sound Manager v{project_version()}')

    # 1. Device YAMLs load and parse.
    try:
        from arctis_sound_manager.config import load_device_configurations
        configs = load_device_configurations()
        if not configs:
            log.error('No device configurations loaded — check ~/.config/arctis_manager/devices and the bundled devices/ folder.')
            issues += 1
        else:
            total_pids = sum(len(c.product_ids) for c in configs)
            log.info(f'Device YAMLs:    OK ({len(configs)} families, {total_pids} PIDs)')
    except Exception as e:
        log.error(f'Device YAMLs:    FAIL ({e!r})')
        issues += 1

    # 2. Comprehensive system deps check (Phase 3 — see ~/Bureau/ASM_PLAN_DEPS_CHECK.md).
    #
    # Subsumes the previous individual checks for udev rules, PulseAudio/PW
    # reachability, D-Bus session, and pyudev — all four are now part of
    # `system_deps_checker._build_checks()`. The single shared registry
    # ensures the GUI dialog (Phase 4) and `--verify-setup` agree on what
    # to verify and how to fix it.
    #
    # Catches the silent-failure traps the older preflight didn't cover —
    # missing LADSPA plugins (issue #23 root cause), HRIR file gone after a
    # cache wipe, filter-chain.service unreachable, gh CLI not authenticated,
    # etc. Each missing dep is logged with its severity tier and the exact
    # command to install it on this distro.
    try:
        from arctis_sound_manager.system_deps_checker import (
            run_all_checks, Severity, install_command_for, detect_distro,
        )
        results = run_all_checks()
        passed = sum(1 for r in results if r.ok)
        log.info(f'System deps:     {passed}/{len(results)} OK (distro={detect_distro()})')

        for r in results:
            if r.ok:
                continue
            argv = install_command_for(r.check)
            install_hint = (
                f'  Install:  sudo {" ".join(argv)}'
                if argv and argv[0] != 'asm-setup' and argv[0] != 'asm-cli'
                else (f'  Run:      {" ".join(argv)}' if argv else '  (no automatic install path on this distro)')
            )
            tier = r.check.severity
            line = f'{r.check.name} MISSING — breaks: {r.check.feature}'
            if tier is Severity.BLOCKING:
                log.error(f'  [BLOCKING] {line}')
                issues += 1
            elif tier is Severity.DEGRADED:
                log.warning(f'  [DEGRADED] {line}')
                issues += 1
            else:  # OPTIONAL
                log.info(f'  [OPTIONAL] {line}')
                # OPTIONAL doesn't bump `issues` — exit 0 stays achievable.
            log.info(install_hint)
            if r.check.user_action:
                log.info(f'  Note:     {r.check.user_action}')
    except Exception as e:
        log.error(f'System deps:     FAIL to run checker ({e!r})')
        issues += 1

    if issues == 0:
        log.info('All preflight checks passed.')
    else:
        log.error(f'{issues} preflight check(s) failed.')
    return 0 if issues == 0 else 1


# Companion services the daemon revives on startup, with the user-visible
# consequence of each staying dead — the log line has to say what silently
# stopped working, not just name a unit.
_COMPANION_SERVICES = (
    ('arctis-video-router', 'media router',
     'manual routing choices are remembered again'),
    ('arctis-stream-guard', 'stream guard',
     'muted channels stay out of screen shares again'),
)


def _ensure_media_router_running(logger) -> None:
    """Start the companion services that are enabled but not running.

    Quitting ASM from the tray stops arctis-manager, the companion services and
    filter-chain together, on purpose — the system is meant to behave as if ASM
    were not installed. Relaunching the app brought back the daemon and the
    filter-chain, but nothing brought back the router: it stayed dead for the
    rest of the session while ASM looked perfectly healthy. What silently
    stopped working is the part that watches for manual stream moves and
    remembers them as per-app routing overrides, so users saw their apps drift
    back to the default channel with no explanation.

    The stream guard fails the same way but louder: a guard that is silently
    dead means the next screen share broadcasts every channel to the call.

    Only started when the unit is *enabled*: a user who deliberately disabled
    one must not have it resurrected on every daemon start. Failures are logged
    and swallowed, per service — the daemon's own job comes first, and one
    unavailable unit must not stop the others from being revived.
    """
    from arctis_sound_manager import service_control as sc

    try:
        if not sc.manager_available():
            return
    except Exception as exc:
        logger.warning('could not check the service manager: %r', exc)
        return

    for unit, label, consequence in _COMPANION_SERVICES:
        try:
            if sc.is_active(unit):
                continue
            if not sc.is_enabled(unit):
                logger.debug('%s is disabled — leaving it stopped', label)
                continue
            logger.warning(
                '%s is enabled but not running (stopped with a previous ASM '
                'session?) — starting it so %s', label, consequence
            )
            sc.start(unit, timeout=10)
        except Exception as exc:
            logger.warning('could not check/start the %s: %r', label, exc)


async def main_async():
    from arctis_sound_manager.log_setup import configure_logging
    configure_logging(default=logging.INFO,
                      fmt='[%(levelname)7s] %(name)20s: %(message)s')

    logger = logging.getLogger('Daemon')
    logger.info('-------------------------------')
    logger.info('- Arctis Sound Manager is starting. -')
    logger.info(f'-{"v " + project_version():>27}  -')
    logger.info('-------------------------------')

    # Repair installs whose device profiles are pinned to an old release before
    # anything reads them. asm-setup used to copy every packaged profile into
    # ~/.config/arctis_manager/devices/, which takes precedence over the
    # packaged folder and is never refreshed by an upgrade — so profile fixes
    # silently never reached existing users. Runs here because the daemon is the
    # only thing that starts for everyone, every session; asm-setup fires once,
    # at first GUI launch.
    try:
        from arctis_sound_manager.constants import (HOME_CONFIG_FOLDER,
                                                    SRC_CONFIG_FOLDER)
        from arctis_sound_manager.device_override_reconcile import (
            log_report, reconcile_home_device_overrides)
        log_report(
            reconcile_home_device_overrides(HOME_CONFIG_FOLDER, SRC_CONFIG_FOLDER),
            HOME_CONFIG_FOLDER,
        )
    except Exception as exc:
        logger.warning('could not reconcile device profile overrides: %r', exc)

    # Same reasoning as the block above, for the tray unit renamed in v1.3.0:
    # anyone whose "launch at login" pointed at the packaged arctis-gui.service
    # lost it, because the upgrade removed that file and nothing enabled its
    # replacement (#191). The GUI failing to start *is* the symptom, so this
    # cannot live there — the daemon is what still runs.
    try:
        from arctis_sound_manager.autostart import migrate_legacy_gui_autostart
        migrate_legacy_gui_autostart()
    except Exception as exc:
        logger.warning('could not migrate the tray autostart unit: %r', exc)

    from arctis_sound_manager.udev_checker import is_udev_rules_valid
    if not is_udev_rules_valid():
        logger.warning('udev rules are missing or invalid — USB access may fail (errno 13). Run: sudo asm-cli udev write-rules --force --reload')

    _ensure_media_router_running(logger)

    dbus_manager = DbusManager.getInstance()
    core_engine = CoreEngine()

    core_loop = asyncio.create_task(core_engine.start(), name='core-loop')
    dbus_awake_loop = asyncio.create_task(DbusAwake.get_instance().start(core_engine), name='dbus-awake')
    watchdog_loop = asyncio.create_task(core_engine._loopback_watchdog(), name='loopback-watchdog')

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
        for task in (core_loop, dbus_awake_loop, watchdog_loop):
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
        for task in (core_loop, dbus_awake_loop, watchdog_loop):
            if not task.done():
                task.cancel()
        await asyncio.gather(core_loop, dbus_awake_loop, watchdog_loop, return_exceptions=True)


def main():
    parser = argparse.ArgumentParser(prog='asm-daemon')
    parser.add_argument(
        '--verify-setup', action='store_true',
        help='Run preflight checks (devices YAMLs, udev rules, PulseAudio/PipeWire, '
             'D-Bus session, USB monitor backend) and exit with 0 if everything is OK.',
    )
    args, _unknown = parser.parse_known_args()

    if args.verify_setup:
        sys.exit(verify_setup())

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
