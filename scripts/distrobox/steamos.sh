#!/usr/bin/env bash
# Arctis Sound Manager — Distrobox installer for SteamOS (Valve Steam Deck)
# Usage: bash scripts/distrobox/steamos.sh [--reinstall] [--no-services] [-h]
set -euo pipefail

DISTROBOX_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/distrobox/_common.sh
source "$DISTROBOX_DIR/_common.sh"

trap 'log_error "Script FAILED at line $LINENO — see $ASM_LOG_FILE for details"' ERR

usage() {
    cat <<'EOF'
Arctis Sound Manager — Distrobox installer for SteamOS (Steam Deck)

Usage:
  bash scripts/distrobox/steamos.sh [options]

Options:
  --reinstall    Remove and recreate the container, then reinstall
  --no-services  Skip enabling systemd services after install
  -h, --help     Show this help message

The container uses Arch Linux (AUR) and mounts hidraw + PipeWire sockets.
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

log_info "=== ASM Distrobox install (SteamOS) started ($(date)) ==="

if ! [[ -f /etc/steamos-release ]] && ! command -v steamos-readonly &>/dev/null; then
    log_warn "This script targets SteamOS but the host does not appear to be SteamOS."
    log_warn "Continuing anyway — press Ctrl-C within 5 seconds to abort."
    sleep 5
fi

echo ""
echo "=========================================================="
echo "  IMPORTANT — SteamOS notes before continuing:"
echo ""
echo "  1. The default 'deck' user password is required for sudo."
echo "     If you have not set a password yet: passwd"
echo ""
echo "  2. udev rules are written to /etc/udev/rules.d/ which is"
echo "     reset on major SteamOS updates. Re-run this script after"
echo "     every SteamOS version upgrade to restore USB access."
echo "=========================================================="
echo ""

asm_check_host_prereqs

if [[ $DO_REINSTALL -eq 1 ]] && asm_container_exists; then
    log_step "Reinstall requested: removing existing container '$ASM_CONTAINER_NAME'..."
    distrobox rm --force "$ASM_CONTAINER_NAME"
    log_ok "Existing container removed"
fi

if asm_container_exists; then
    log_step "Container '$ASM_CONTAINER_NAME' already exists — upgrading ASM (skipping create)..."
else
    asm_create_container "$ASM_ARCH_IMAGE"
fi

asm_install_arch_in_container
asm_export_binaries
asm_write_systemd_units
asm_install_udev_rules "steamos"
asm_verify_pipewire

if [[ $SKIP_SERVICES -eq 0 ]]; then
    asm_enable_services
else
    log_info "Skipping service activation (--no-services)"
fi

asm_print_summary "SteamOS"
log_info "=== ASM Distrobox install (SteamOS) finished successfully ==="
