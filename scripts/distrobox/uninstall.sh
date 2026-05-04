#!/usr/bin/env bash
# Arctis Sound Manager — Distrobox uninstaller
# Usage: bash scripts/distrobox/uninstall.sh [--keep-container] [--remove-udev] [-h]
set -euo pipefail

DISTROBOX_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/distrobox/_common.sh
source "$DISTROBOX_DIR/_common.sh"

trap 'log_error "Script FAILED at line $LINENO — see $ASM_LOG_FILE for details"' ERR

usage() {
    cat <<'EOF'
Arctis Sound Manager — Distrobox uninstaller

Usage:
  bash scripts/distrobox/uninstall.sh [options]

Options:
  --keep-container  Disable services and remove exports but leave the container
  --remove-udev     Also remove udev rules from /etc/udev/rules.d/
  -h, --help        Show this help message
EOF
}

KEEP_CONTAINER=0
REMOVE_UDEV=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --keep-container) KEEP_CONTAINER=1 ;;
        --remove-udev)    REMOVE_UDEV=1 ;;
        -h|--help)        usage; exit 0 ;;
        *)
            log_error "Unknown argument: $1"
            usage
            exit 1
            ;;
    esac
    shift
done

do_uninstall() {
    log_step "Uninstalling ASM Distrobox setup..."

    local services=(arctis-gui.service arctis-video-router.service arctis-manager.service)
    for svc in "${services[@]}"; do
        if systemctl --user is-enabled "$svc" &>/dev/null; then
            log_info "Disabling $svc..."
            systemctl --user disable --now "$svc" 2>>"$ASM_LOG_FILE" || true
        fi
        local unit_file="$ASM_SYSTEMD_USER_DIR/$svc"
        if [[ -f "$unit_file" ]]; then
            rm -f "$unit_file"
            log_info "Removed unit file: $unit_file"
        fi
    done
    systemctl --user daemon-reload

    log_info "Removing exported binaries..."
    if asm_container_exists; then
        local binaries=(asm-daemon asm-gui asm-cli asm-setup asm-router)
        for bin in "${binaries[@]}"; do
            distrobox enter "$ASM_CONTAINER_NAME" -- distrobox-export \
                --bin "/usr/bin/$bin" \
                --export-path "$HOME/.local/bin" \
                --delete \
                2>>"$ASM_LOG_FILE" || true
        done
        distrobox enter "$ASM_CONTAINER_NAME" -- distrobox-export \
            --app arctis-sound-manager \
            --delete \
            2>>"$ASM_LOG_FILE" || true
    fi

    for bin in asm-daemon asm-gui asm-cli asm-setup asm-router; do
        local stub="$HOME/.local/bin/$bin"
        if [[ -f "$stub" ]] && grep -q "distrobox" "$stub" 2>/dev/null; then
            rm -f "$stub"
            log_info "Removed distrobox stub: $stub"
        fi
    done

    if [[ $KEEP_CONTAINER -eq 0 ]]; then
        if asm_container_exists; then
            log_info "Removing container '$ASM_CONTAINER_NAME'..."
            distrobox rm --force "$ASM_CONTAINER_NAME" 2>>"$ASM_LOG_FILE" || true
            log_ok "Container removed"
        else
            log_info "Container '$ASM_CONTAINER_NAME' not found — nothing to remove"
        fi
    else
        log_info "Container '$ASM_CONTAINER_NAME' kept (--keep-container)"
    fi

    if [[ $REMOVE_UDEV -eq 1 ]]; then
        if [[ -f "$ASM_UDEV_RULES_PATH" ]]; then
            sudo rm -f "$ASM_UDEV_RULES_PATH"
            sudo udevadm control --reload-rules
            log_ok "udev rules removed and reloaded"
        else
            log_info "No udev rules found at $ASM_UDEV_RULES_PATH"
        fi
    else
        echo ""
        echo "Note: udev rules at $ASM_UDEV_RULES_PATH were NOT removed."
        echo "Remove manually if desired: bash scripts/distrobox/uninstall.sh --remove-udev"
    fi

    log_ok "Uninstall complete"
}

log_info "=== ASM Distrobox uninstall started ($(date)) ==="
asm_check_host_prereqs
do_uninstall
log_info "=== ASM Distrobox uninstall finished ==="
