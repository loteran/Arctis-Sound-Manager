#!/usr/bin/env bash
# Arctis Sound Manager — Distrobox installer for Fedora Silverblue / Kinoite
# Usage: bash scripts/distrobox/silverblue.sh [--reinstall] [--no-services] [-h]
set -euo pipefail

DISTROBOX_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/distrobox/_common.sh
source "$DISTROBOX_DIR/_common.sh"

trap 'log_error "Script FAILED at line $LINENO — see $ASM_LOG_FILE for details"' ERR

usage() {
    cat <<'EOF'
Arctis Sound Manager — Distrobox installer for Fedora Silverblue / Kinoite

Usage:
  bash scripts/distrobox/silverblue.sh [options]

Options:
  --reinstall    Remove and recreate the container, then reinstall
  --no-services  Skip enabling systemd services after install
  -h, --help     Show this help message

The container uses Fedora 41 (COPR) and mounts hidraw + PipeWire sockets.
Note: noise-suppression-for-voice is not available via COPR on Fedora.
Log: ~/.cache/asm-distrobox-install.log
EOF
}

DO_REINSTALL=0
SKIP_SERVICES=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --reinstall)   DO_REINSTALL=1 ;;
        --no-services) SKIP_SERVICES=1 ;;
        -h|--help)     usage; exit 0 ;;
        *)
            log_error "Unknown argument: $1"
            usage
            exit 1
            ;;
    esac
    shift
done

log_info "=== ASM Distrobox install (Silverblue) started ($(date)) ==="

if ! grep -qiE 'silverblue|kinoite' /etc/os-release 2>/dev/null; then
    log_warn "This script targets Silverblue/Kinoite but the host does not appear to match."
    log_warn "Continuing anyway — press Ctrl-C within 5 seconds to abort."
    sleep 5
fi

asm_check_host_prereqs

if [[ $DO_REINSTALL -eq 1 ]] && asm_container_exists; then
    log_step "Reinstall requested: removing existing container '$ASM_CONTAINER_NAME'..."
    distrobox rm --force "$ASM_CONTAINER_NAME"
    log_ok "Existing container removed"
fi

if asm_container_exists; then
    log_step "Container '$ASM_CONTAINER_NAME' already exists — upgrading ASM (skipping create)..."
else
    asm_create_container "$ASM_FEDORA_IMAGE"
fi

asm_install_fedora_in_container
asm_export_binaries
asm_write_systemd_units
asm_install_udev_rules ""
asm_verify_pipewire

if [[ $SKIP_SERVICES -eq 0 ]]; then
    asm_enable_services
else
    log_info "Skipping service activation (--no-services)"
fi

asm_print_summary "Silverblue"
log_info "=== ASM Distrobox install (Silverblue) finished successfully ==="
