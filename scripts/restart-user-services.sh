#!/bin/sh
# Restart ASM's user services after a package upgrade.
#
# Why this exists: a package manager replaces files on disk, it does not touch
# running processes. Python has already loaded the old modules into memory, so
# asm-daemon keeps executing the previous version until something restarts it —
# for most people, until their next reboot. Someone who upgrades *for* a fix
# then finds the bug still there, and reports it against a version they are not
# running.
#
# Called from the post-upgrade scriptlet of every packaging (pacman .install,
# RPM %post, deb postinst), which run as root while user sessions are live.
#
# Deliberately try-restart, not restart: it only touches services that are
# already running. Someone who stopped ASM on purpose does not get it started
# again by installing an update.
#
# Handles both init systems ASM supports for the user session (systemd and
# dinit — see service_control.py, the Python single source of truth for this
# mapping; this script mirrors it by hand because packaging scriptlets run
# before/outside any Python environment can be assumed usable). A box with
# neither has nothing here to restart into: the GUI's own upgrade check
# (runtime_staleness.py) still offers a restart on next launch.
#
# Copyright (C) 2026 loteran — SPDX-License-Identifier: GPL-3.0-or-later
set -u

# systemd unit names (as shipped: *.service). The tray unit is
# app-ArctisManager.service since v1.3.0 (service_control._SERVICE_MAP maps
# "arctis-gui" to it, so xdg-desktop-portal can derive an app id from the
# cgroup). The legacy name stays in the list because an upgrade can land
# before the GUI has migrated its own unit, and try-restart is a no-op on a
# unit that is not running.
SYSTEMD_SERVICES="arctis-manager.service arctis-video-router.service arctis-stream-guard.service app-ArctisManager.service arctis-gui.service"
# dinit service names have no suffix, and there is no dinit unit for the GUI —
# it autostarts via an XDG .desktop entry there instead (service_control.py's
# _SERVICE_MAP maps "arctis-gui" to None on dinit; mirrored here).
DINIT_SERVICES="arctis-manager arctis-video-router arctis-stream-guard"

# Detect the running init system the same way init_system.detect_init() does:
# PID 1's comm name first, falling back to which binary is on PATH.
detect_init() {
    comm="$(cat /proc/1/comm 2>/dev/null || true)"
    case "$comm" in
        dinit)    printf '%s\n' dinit;   return ;;
        systemd)  printf '%s\n' systemd; return ;;
    esac
    if command -v dinitctl >/dev/null 2>&1 && ! command -v systemctl >/dev/null 2>&1; then
        printf '%s\n' dinit
    elif command -v systemctl >/dev/null 2>&1; then
        printf '%s\n' systemd
    else
        printf '%s\n' unknown
    fi
}

restart_for_session_systemd() {
    uid="$1"
    user="$2"

    # daemon-reload first: the upgrade may have changed the unit files
    # themselves, and try-restart would otherwise re-launch the old definition.
    runuser -u "$user" -- env \
        XDG_RUNTIME_DIR="/run/user/${uid}" \
        DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/${uid}/bus" \
        systemctl --user daemon-reload >/dev/null 2>&1 || true

    # shellcheck disable=SC2086  # SYSTEMD_SERVICES is a deliberate word list
    runuser -u "$user" -- env \
        XDG_RUNTIME_DIR="/run/user/${uid}" \
        DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/${uid}/bus" \
        systemctl --user try-restart $SYSTEMD_SERVICES >/dev/null 2>&1 || true
}

restart_for_session_dinit() {
    uid="$1"
    user="$2"

    # dinitctl has no try-restart verb, so the same "leave it stopped if it
    # was stopped" guarantee is done by hand: check status first, and only
    # restart what is already STARTED (mirrors service_control.is_active()'s
    # own `dinitctl status` / "STARTED" check).
    for svc in $DINIT_SERVICES; do
        status="$(runuser -u "$user" -- env \
            XDG_RUNTIME_DIR="/run/user/${uid}" \
            dinitctl status "$svc" 2>/dev/null || true)"
        case "$status" in
            *STARTED*)
                runuser -u "$user" -- env \
                    XDG_RUNTIME_DIR="/run/user/${uid}" \
                    dinitctl restart "$svc" >/dev/null 2>&1 || true
                ;;
        esac
    done
}

restart_for_session() {
    uid="$1"
    user="$2"
    init="$3"

    # No session bus means no running user session for that user — nothing of
    # ours can be running there either.
    [ -S "/run/user/${uid}/bus" ] || return 0

    case "$init" in
        systemd) restart_for_session_systemd "$uid" "$user" ;;
        dinit)   restart_for_session_dinit "$uid" "$user" ;;
        *)       ;;  # unknown init: nothing we can safely drive
    esac
}

command -v loginctl >/dev/null 2>&1 || exit 0
command -v runuser  >/dev/null 2>&1 || exit 0

INIT="$(detect_init)"
if [ "$INIT" = "unknown" ]; then
    # No systemctl and no dinitctl: nothing on this host can restart a user
    # service. Not fatal — mirrors service_control's own "no usable init
    # manager" warning rather than failing the package transaction.
    echo "restart-user-services: no usable init manager (neither systemctl nor dinitctl found) — skipping" >&2
    exit 0
fi

# "loginctl list-users" prints: UID USER [LINGER] [STATE]
loginctl list-users --no-legend 2>/dev/null | while read -r uid user _rest; do
    case "$uid" in
        ''|*[!0-9]*) continue ;;   # header or malformed line
    esac
    restart_for_session "$uid" "$user" "$INIT"
done

# The GUI is not always a systemd service — it is commonly started by the
# desktop's autostart, and killing someone's window from a package transaction
# would be rude. It notices the upgrade on its own and offers to restart
# (see runtime_staleness.py).
exit 0
