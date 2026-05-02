#!/usr/bin/env bash
# Arctis Sound Manager — Distrobox installer for immutable distros
# Targets: Bazzite, SteamOS, Fedora Silverblue/Kinoite, and any rpm-ostree host
#
# Usage:
#   bash scripts/distrobox-install.sh [options]
#
# Options:
#   --base arch|fedora   Container base image (default: arch)
#   --reinstall          Remove and recreate the container, then reinstall
#   --uninstall          Fully uninstall (disable services, delete exports, remove container)
#   --no-services        Skip enabling systemd services after install
#   -h, --help           Show this help message
#
# The container is named "arctis-sound-manager" by default.
# Log file: ~/.cache/asm-distrobox-install.log

set -euo pipefail
trap 'log_error "Script FAILED at line $LINENO — see $LOG_FILE for details"' ERR

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
CONTAINER_NAME="arctis-sound-manager"
ARCH_IMAGE="ghcr.io/ublue-os/arch-distrobox:latest"
FEDORA_IMAGE="ghcr.io/ublue-os/fedora-toolbox:latest"
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"
LOG_FILE="${XDG_CACHE_HOME:-$HOME/.cache}/asm-distrobox-install.log"

# State flags (set by parse_args / detect functions)
BASE_IMAGE="arch"
DO_REINSTALL=0
DO_UNINSTALL=0
SKIP_SERVICES=0
IS_IMMUTABLE=0
IS_STEAMOS=0

# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------
_log() { local level="$1"; shift; local msg="$*"; echo "$(date '+%Y-%m-%d %H:%M:%S') [$level] $msg" | tee -a "$LOG_FILE"; }
log_info()    { _log "INFO " "$@"; }
log_warn()    { _log "WARN " "$@" >&2; }
log_error()   { _log "ERROR" "$@" >&2; }
log_step()    { echo ""; _log "===>" "$@"; }
log_ok()      { _log " OK  " "$@"; }

# ---------------------------------------------------------------------------
# parse_args
# ---------------------------------------------------------------------------
parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --base)
                shift
                BASE_IMAGE="${1:-arch}"
                if [[ "$BASE_IMAGE" != "arch" && "$BASE_IMAGE" != "fedora" ]]; then
                    log_error "--base must be 'arch' or 'fedora' (got: $BASE_IMAGE)"
                    exit 1
                fi
                ;;
            --reinstall)  DO_REINSTALL=1 ;;
            --uninstall)  DO_UNINSTALL=1 ;;
            --no-services) SKIP_SERVICES=1 ;;
            -h|--help)    usage; exit 0 ;;
            *)
                log_error "Unknown argument: $1"
                usage
                exit 1
                ;;
        esac
        shift
    done
}

usage() {
    cat <<'EOF'
Arctis Sound Manager — Distrobox installer for immutable distros

Usage:
  bash scripts/distrobox-install.sh [options]

Options:
  --base arch|fedora   Container base image (default: arch)
                       arch   → full AUR deps incl. noise-suppression-for-voice
                       fedora → COPR, no noise-suppression
  --reinstall          Remove and recreate the container from scratch
  --uninstall          Fully uninstall ASM (services, exports, container)
  --no-services        Skip enabling systemd services after install
  -h, --help           Show this help message

Notes:
  - Ubuntu is NOT supported as a base image: PySide6 >= 6.10.1 is required
    and is not available in Ubuntu repos.
  - The container exposes /dev/hidraw* devices explicitly so HID access works.
  - Idempotent: safe to re-run for upgrades (existing container is updated,
    not recreated — unless --reinstall is passed).
  - Log: ~/.cache/asm-distrobox-install.log
EOF
}

# ---------------------------------------------------------------------------
# check_host_prereqs
# ---------------------------------------------------------------------------
check_host_prereqs() {
    log_step "Checking host prerequisites..."

    local missing=()

    if ! command -v distrobox &>/dev/null; then
        missing+=("distrobox")
    fi

    if ! command -v podman &>/dev/null && ! command -v docker &>/dev/null; then
        missing+=("podman (or docker)")
    fi

    if ! command -v systemctl &>/dev/null; then
        missing+=("systemctl")
    else
        # Verify user systemd is running
        if ! systemctl --user status &>/dev/null; then
            log_warn "systemd --user does not appear to be running (will try to continue)"
        fi
    fi

    if [[ ${#missing[@]} -gt 0 ]]; then
        log_error "Missing required host tools: ${missing[*]}"
        log_error "On Bazzite/SteamOS, distrobox and podman are pre-installed."
        log_error "On Silverblue: sudo rpm-ostree install distrobox podman && systemctl reboot"
        exit 1
    fi

    log_ok "Host prerequisites satisfied (distrobox, podman/docker, systemctl)"
}

# ---------------------------------------------------------------------------
# detect_immutable_host
# ---------------------------------------------------------------------------
detect_immutable_host() {
    log_step "Detecting host type..."

    # rpm-ostree based (Silverblue, Kinoite, Bazzite, etc.)
    if command -v rpm-ostree &>/dev/null; then
        IS_IMMUTABLE=1
        log_info "rpm-ostree detected: immutable host (Silverblue/Kinoite/Bazzite family)"
    fi

    # SteamOS detection (Valve's read-only /etc)
    if command -v steamos-readonly &>/dev/null || [[ -f /etc/steamos-release ]]; then
        IS_IMMUTABLE=1
        IS_STEAMOS=1
        log_info "SteamOS detected"
    fi

    # Generic: check if / or /usr is read-only
    if [[ $IS_IMMUTABLE -eq 0 ]]; then
        if ! touch /usr/.asm_rw_test 2>/dev/null; then
            IS_IMMUTABLE=1
            log_info "Read-only /usr detected: treating as immutable host"
        else
            rm -f /usr/.asm_rw_test
        fi
    fi

    if [[ $IS_IMMUTABLE -eq 1 ]]; then
        log_info "Immutable host confirmed — ASM will run inside Distrobox"
    else
        log_warn "Host does not appear to be immutable."
        log_warn "You could install ASM natively (AUR / COPR / PPA)."
        log_warn "Proceeding with Distrobox install anyway..."
    fi

    # Warn if ASM is already installed natively
    if command -v asm-daemon &>/dev/null; then
        log_warn "asm-daemon found in PATH ($(command -v asm-daemon)) — ASM is already installed natively."
        log_warn "The Distrobox install will coexist but may conflict. Consider removing the native install first."
    fi
}

# ---------------------------------------------------------------------------
# choose_base_image
# ---------------------------------------------------------------------------
choose_base_image() {
    log_step "Selecting container base image..."

    if [[ "$BASE_IMAGE" == "arch" ]]; then
        SELECTED_IMAGE="$ARCH_IMAGE"
        log_info "Base: Arch Linux ($ARCH_IMAGE)"
        log_info "Install method: AUR (paru -S arctis-sound-manager)"
        log_info "Includes: noise-suppression-for-voice, all LADSPA plugins"
    else
        SELECTED_IMAGE="$FEDORA_IMAGE"
        log_info "Base: Fedora ($FEDORA_IMAGE)"
        log_info "Install method: COPR (dnf copr enable loteran/arctis-sound-manager)"
        log_warn "Note: noise-suppression-for-voice is not available via COPR on Fedora"
    fi
}

# ---------------------------------------------------------------------------
# list_hidraw_devices
# Returns "--device=/dev/hidrawN ..." for each present /dev/hidraw*
# ---------------------------------------------------------------------------
list_hidraw_devices() {
    local devices=()
    for dev in /dev/hidraw*; do
        [[ -e "$dev" ]] && devices+=("--additional-flags=--device=$dev")
    done
    echo "${devices[*]:-}"
}

# ---------------------------------------------------------------------------
# container_exists
# ---------------------------------------------------------------------------
container_exists() {
    distrobox list 2>/dev/null | grep -q "^$CONTAINER_NAME\s\|^\s*$CONTAINER_NAME\s\| $CONTAINER_NAME " || \
    distrobox list 2>/dev/null | grep -qw "$CONTAINER_NAME"
}

# ---------------------------------------------------------------------------
# create_container
# ---------------------------------------------------------------------------
create_container() {
    log_step "Creating Distrobox container '$CONTAINER_NAME'..."

    local hidraw_flags
    hidraw_flags="$(list_hidraw_devices)"
    local hidraw_count
    hidraw_count=$(ls /dev/hidraw* 2>/dev/null | wc -l || echo 0)

    if [[ $hidraw_count -eq 0 ]]; then
        log_warn "No /dev/hidraw* devices found. Plug in your headset before running ASM."
        log_warn "If hidraw devices appear later, recreate the container with --reinstall."
    else
        log_info "Passing $hidraw_count hidraw device(s) to container: $(ls /dev/hidraw* 2>/dev/null | tr '\n' ' ')"
    fi

    # Build distrobox create command
    local create_cmd=(
        distrobox create
        --name "$CONTAINER_NAME"
        --image "$SELECTED_IMAGE"
        --home "$HOME"
        --pull
        --yes
    )

    # Append hidraw device flags if any
    if [[ -n "$hidraw_flags" ]]; then
        # Split the flags properly
        read -r -a hidraw_array <<< "$hidraw_flags"
        create_cmd+=("${hidraw_array[@]}")
    fi

    log_info "Running: ${create_cmd[*]}"
    "${create_cmd[@]}"
    log_ok "Container '$CONTAINER_NAME' created"
}

# ---------------------------------------------------------------------------
# install_asm_in_container
# ---------------------------------------------------------------------------
install_asm_in_container() {
    log_step "Installing ASM inside the container (mode: $BASE_IMAGE)..."

    if [[ "$BASE_IMAGE" == "arch" ]]; then
        _install_arch
    else
        _install_fedora
    fi
}

_install_arch() {
    log_info "Arch install: updating system and installing via AUR (paru)..."

    distrobox enter "$CONTAINER_NAME" -- bash -lc '
        set -euo pipefail

        echo "[arch-install] Updating system..."
        sudo pacman -Syu --noconfirm

        # Install paru (AUR helper) if not present
        if ! command -v paru &>/dev/null; then
            echo "[arch-install] Installing paru (AUR helper)..."
            sudo pacman -S --noconfirm base-devel git
            tmpdir=$(mktemp -d)
            git clone https://aur.archlinux.org/paru-bin.git "$tmpdir/paru"
            (cd "$tmpdir/paru" && makepkg -si --noconfirm)
            rm -rf "$tmpdir"
        fi

        echo "[arch-install] Installing arctis-sound-manager from AUR..."
        paru -S --noconfirm arctis-sound-manager

        echo "[arch-install] Done."
    '
}

_install_fedora() {
    log_info "Fedora install: enabling COPR and installing..."

    distrobox enter "$CONTAINER_NAME" -- bash -lc '
        set -euo pipefail

        echo "[fedora-install] Updating system..."
        sudo dnf upgrade -y

        echo "[fedora-install] Enabling COPR: loteran/arctis-sound-manager..."
        sudo dnf copr enable -y loteran/arctis-sound-manager

        echo "[fedora-install] Installing arctis-sound-manager..."
        sudo dnf install -y arctis-sound-manager

        echo "[fedora-install] Done."
    '
}

# ---------------------------------------------------------------------------
# export_binaries
# Exports asm-* binaries and desktop entry to the host via distrobox-export
# ---------------------------------------------------------------------------
export_binaries() {
    log_step "Exporting ASM binaries to host..."

    local binaries=(asm-daemon asm-gui asm-cli asm-setup asm-router)

    for bin in "${binaries[@]}"; do
        log_info "Exporting binary: $bin"
        distrobox enter "$CONTAINER_NAME" -- distrobox-export \
            --bin "/usr/bin/$bin" \
            --export-path "$HOME/.local/bin" \
            2>>"$LOG_FILE" || \
        log_warn "Could not export $bin (may already be exported or path differs)"
    done

    # Export desktop entry
    log_info "Exporting desktop entry..."
    distrobox enter "$CONTAINER_NAME" -- distrobox-export \
        --app arctis-sound-manager \
        2>>"$LOG_FILE" || \
    log_warn "Could not export desktop entry (may already be exported or not found)"

    log_ok "Binary and desktop exports done"
    log_info "Exported binaries available at: $HOME/.local/bin/"
}

# ---------------------------------------------------------------------------
# write_host_systemd_units
# Writes systemd user unit files directly on the host.
# We do NOT use distrobox-export --service because it has race conditions with
# PipeWire ordering (the generated unit doesn't have After=pipewire.service).
# ---------------------------------------------------------------------------
write_host_systemd_units() {
    log_step "Writing host systemd user units..."

    mkdir -p "$SYSTEMD_USER_DIR"

    # --- arctis-manager.service (daemon) ---
    cat > "$SYSTEMD_USER_DIR/arctis-manager.service" <<EOF
[Unit]
Description=Arctis Sound Manager (Distrobox)
After=pipewire.service pipewire-pulse.service
Wants=pipewire.service
StartLimitInterval=1min
StartLimitBurst=5

[Service]
Type=simple
ExecStart=/usr/bin/distrobox enter ${CONTAINER_NAME} -- /usr/bin/asm-daemon
Restart=on-failure
RestartSec=5

[Install]
WantedBy=graphical-session.target
EOF
    log_ok "Written: $SYSTEMD_USER_DIR/arctis-manager.service"

    # --- arctis-gui.service (system tray) ---
    cat > "$SYSTEMD_USER_DIR/arctis-gui.service" <<EOF
[Unit]
Description=Arctis Sound Manager — System Tray (Distrobox)
After=graphical-session.target arctis-manager.service
Wants=arctis-manager.service

[Service]
Type=simple
ExecStart=/usr/bin/distrobox enter ${CONTAINER_NAME} -- /usr/bin/asm-gui --systray
Restart=on-failure
RestartSec=5

[Install]
WantedBy=graphical-session.target
EOF
    log_ok "Written: $SYSTEMD_USER_DIR/arctis-gui.service"

    # --- arctis-video-router.service (media auto-routing) ---
    cat > "$SYSTEMD_USER_DIR/arctis-video-router.service" <<EOF
[Unit]
Description=Arctis Sound Manager — Media Router (Distrobox)
After=pipewire.service arctis-manager.service
Requires=pipewire.service

[Service]
Type=simple
ExecStart=/usr/bin/distrobox enter ${CONTAINER_NAME} -- /usr/bin/asm-router
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
EOF
    log_ok "Written: $SYSTEMD_USER_DIR/arctis-video-router.service"
}

# ---------------------------------------------------------------------------
# install_udev_rules
# Generates rules inside the container then installs them on the host via
# distrobox-host-exec (which runs commands as the real host user, with sudo).
# ---------------------------------------------------------------------------
install_udev_rules() {
    log_step "Installing udev rules on host..."

    local rules_tmp
    rules_tmp="$(mktemp /tmp/91-steelseries-arctis.rules.XXXXXX)"

    # Generate rules from inside the container
    log_info "Generating udev rules via asm-cli inside container..."
    distrobox enter "$CONTAINER_NAME" -- bash -lc \
        "asm-cli udev dump-rules" > "$rules_tmp"

    if [[ ! -s "$rules_tmp" ]]; then
        log_warn "asm-cli udev dump-rules produced empty output — skipping udev install"
        rm -f "$rules_tmp"
        return 0
    fi

    log_info "Rules generated: $(wc -l < "$rules_tmp") lines"

    # Handle SteamOS read-only /etc
    if [[ $IS_STEAMOS -eq 1 ]]; then
        log_warn "SteamOS detected: /etc may be read-only. Attempting steamos-readonly disable..."
        if command -v steamos-readonly &>/dev/null; then
            distrobox-host-exec steamos-readonly disable || {
                log_error "steamos-readonly disable failed. Cannot install udev rules."
                log_error "Manual fix: steamos-readonly disable && sudo install -m644 $rules_tmp /etc/udev/rules.d/91-steelseries-arctis.rules"
                rm -f "$rules_tmp"
                return 1
            }
            log_info "steamos-readonly disabled temporarily"
        else
            log_warn "steamos-readonly command not found — trying install anyway"
        fi
    fi

    # Install rules on the host using distrobox-host-exec
    log_info "Installing rules to /etc/udev/rules.d/ (requires sudo on host)..."
    distrobox-host-exec sudo install -m644 "$rules_tmp" /etc/udev/rules.d/91-steelseries-arctis.rules
    distrobox-host-exec sudo udevadm control --reload-rules
    distrobox-host-exec sudo udevadm trigger --subsystem-match=usb

    # Re-enable SteamOS read-only if we disabled it
    if [[ $IS_STEAMOS -eq 1 ]] && command -v steamos-readonly &>/dev/null; then
        distrobox-host-exec steamos-readonly enable 2>/dev/null || true
        log_info "steamos-readonly re-enabled"
    fi

    rm -f "$rules_tmp"
    log_ok "udev rules installed and reloaded"
}

# ---------------------------------------------------------------------------
# verify_pipewire
# Verifies PipeWire is accessible from inside the container.
# ---------------------------------------------------------------------------
verify_pipewire() {
    log_step "Verifying PipeWire access from container..."

    if distrobox enter "$CONTAINER_NAME" -- bash -lc 'pactl info &>/dev/null'; then
        log_ok "pactl: PulseAudio/PipeWire accessible from container"
    else
        log_warn "pactl info failed inside container — PipeWire may not be accessible"
        log_warn "This is often resolved after a reboot (session bus not available during install)"
    fi

    if distrobox enter "$CONTAINER_NAME" -- bash -lc 'pw-cli info 0 &>/dev/null'; then
        log_ok "pw-cli: PipeWire native socket accessible from container"
    else
        log_warn "pw-cli info 0 failed inside container — PipeWire native socket not accessible"
        log_warn "Ensure PIPEWIRE_RUNTIME_DIR is set correctly; may work after full session restart"
    fi
}

# ---------------------------------------------------------------------------
# enable_services
# ---------------------------------------------------------------------------
enable_services() {
    if [[ $SKIP_SERVICES -eq 1 ]]; then
        log_info "Skipping service activation (--no-services)"
        return 0
    fi

    log_step "Enabling ASM systemd user services..."

    systemctl --user daemon-reload

    local services=(arctis-manager.service arctis-video-router.service arctis-gui.service)
    for svc in "${services[@]}"; do
        if systemctl --user enable --now "$svc" 2>>"$LOG_FILE"; then
            log_ok "Enabled and started: $svc"
        else
            log_warn "Could not enable $svc (may need a full desktop session restart)"
        fi
    done
}

# ---------------------------------------------------------------------------
# do_uninstall
# ---------------------------------------------------------------------------
do_uninstall() {
    log_step "Uninstalling ASM Distrobox setup..."

    # Disable and stop services
    local services=(arctis-gui.service arctis-video-router.service arctis-manager.service)
    for svc in "${services[@]}"; do
        if systemctl --user is-enabled "$svc" &>/dev/null; then
            log_info "Disabling $svc..."
            systemctl --user disable --now "$svc" 2>>"$LOG_FILE" || true
        fi
        local unit_file="$SYSTEMD_USER_DIR/$svc"
        if [[ -f "$unit_file" ]]; then
            rm -f "$unit_file"
            log_info "Removed unit file: $unit_file"
        fi
    done
    systemctl --user daemon-reload

    # Remove exported binaries
    log_info "Removing exported binaries..."
    if container_exists; then
        local binaries=(asm-daemon asm-gui asm-cli asm-setup asm-router)
        for bin in "${binaries[@]}"; do
            distrobox enter "$CONTAINER_NAME" -- distrobox-export \
                --bin "/usr/bin/$bin" \
                --export-path "$HOME/.local/bin" \
                --delete \
                2>>"$LOG_FILE" || true
        done
        # Remove desktop entry export
        distrobox enter "$CONTAINER_NAME" -- distrobox-export \
            --app arctis-sound-manager \
            --delete \
            2>>"$LOG_FILE" || true
    fi

    # Remove leftover stubs from ~/.local/bin
    for bin in asm-daemon asm-gui asm-cli asm-setup asm-router; do
        local stub="$HOME/.local/bin/$bin"
        if [[ -f "$stub" ]] && grep -q "distrobox" "$stub" 2>/dev/null; then
            rm -f "$stub"
            log_info "Removed distrobox stub: $stub"
        fi
    done

    # Remove the container
    if container_exists; then
        log_info "Removing container '$CONTAINER_NAME'..."
        distrobox rm --force "$CONTAINER_NAME" 2>>"$LOG_FILE" || true
        log_ok "Container removed"
    else
        log_info "Container '$CONTAINER_NAME' not found — nothing to remove"
    fi

    log_ok "Uninstall complete"
    echo ""
    echo "Note: udev rules at /etc/udev/rules.d/91-steelseries-arctis.rules were NOT removed."
    echo "Remove manually if desired: sudo rm /etc/udev/rules.d/91-steelseries-arctis.rules"
}

# ---------------------------------------------------------------------------
# print_summary
# ---------------------------------------------------------------------------
print_summary() {
    echo ""
    echo "============================================================"
    echo "  Arctis Sound Manager — Distrobox install complete"
    echo "============================================================"
    echo ""
    echo "Container : $CONTAINER_NAME  (base: $BASE_IMAGE)"
    echo "Log file  : $LOG_FILE"
    echo ""
    echo "Services running:"
    for svc in arctis-manager arctis-video-router arctis-gui; do
        local status
        status=$(systemctl --user is-active "${svc}.service" 2>/dev/null || echo "inactive")
        printf "  %-35s %s\n" "${svc}.service" "$status"
    done
    echo ""
    echo "How to test:"
    echo "  # Check daemon logs:"
    echo "  journalctl --user -u arctis-manager.service -f"
    echo ""
    echo "  # Run ASM GUI manually:"
    echo "  distrobox enter $CONTAINER_NAME -- asm-gui"
    echo ""
    echo "  # Or use the exported binary (after PATH reload):"
    echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
    echo "  asm-gui"
    echo ""
    echo "  # Check diagnostics from inside container:"
    echo "  distrobox enter $CONTAINER_NAME -- asm-daemon --verify-setup"
    echo ""
    echo "  # Restart the daemon:"
    echo "  systemctl --user restart arctis-manager.service"
    echo ""
    echo "  # Uninstall:"
    echo "  bash scripts/distrobox-install.sh --uninstall"
    echo "============================================================"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    # Ensure log directory exists
    mkdir -p "$(dirname "$LOG_FILE")"
    log_info "=== ASM Distrobox install started ($(date)) ==="
    log_info "Args: $*"

    parse_args "$@"

    if [[ $DO_UNINSTALL -eq 1 ]]; then
        check_host_prereqs
        do_uninstall
        exit 0
    fi

    check_host_prereqs
    detect_immutable_host
    choose_base_image

    if [[ $DO_REINSTALL -eq 1 ]] && container_exists; then
        log_step "Reinstall requested: removing existing container '$CONTAINER_NAME'..."
        distrobox rm --force "$CONTAINER_NAME"
        log_ok "Existing container removed"
    fi

    if container_exists; then
        log_step "Container '$CONTAINER_NAME' already exists — upgrading ASM (skipping create)..."
        install_asm_in_container
    else
        create_container
        install_asm_in_container
    fi

    export_binaries
    write_host_systemd_units
    install_udev_rules
    verify_pipewire
    enable_services
    print_summary

    log_info "=== ASM Distrobox install finished successfully ==="
}

main "$@"
