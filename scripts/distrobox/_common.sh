#!/usr/bin/env bash
# Arctis Sound Manager — shared library for distrobox per-distro scripts.
# Source this file — do NOT execute directly.
(return 0 2>/dev/null) || { echo "_common.sh is a library; source it, do not execute it." >&2; exit 1; }

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
ASM_CONTAINER_NAME="arctis-sound-manager"
ASM_LOG_FILE="${XDG_CACHE_HOME:-$HOME/.cache}/asm-distrobox-install.log"
ASM_SYSTEMD_USER_DIR="$HOME/.config/systemd/user"
ASM_UDEV_RULES_PATH="/etc/udev/rules.d/91-steelseries-arctis.rules"

# Container images — keep in sync with COPR build matrix
# fix B1: was ghcr.io/ublue-os/arch-distrobox (image does not exist)
ASM_ARCH_IMAGE="quay.io/toolbx-images/archlinux-toolbox:latest"
# fix B6: pin to :41 — :latest breaks COPR package compatibility
ASM_FEDORA_IMAGE="registry.fedoraproject.org/fedora-toolbox:41"

# ---------------------------------------------------------------------------
# Logging helpers  (mirrors distrobox-install.sh lines 41-46)
# ---------------------------------------------------------------------------
_log()        { local level="$1"; shift; echo "$(date '+%Y-%m-%d %H:%M:%S') [$level] $*" | tee -a "$ASM_LOG_FILE"; }
log_info()    { _log "INFO " "$@"; }
log_warn()    { _log "WARN " "$@" >&2; }
log_error()   { _log "ERROR" "$@" >&2; }
log_step()    { echo ""; _log "===>" "$@"; }
log_ok()      { _log " OK  " "$@"; }

# ---------------------------------------------------------------------------
# asm_check_host_prereqs
# ---------------------------------------------------------------------------
asm_check_host_prereqs() {
    mkdir -p "$(dirname "$ASM_LOG_FILE")"
    log_step "Checking host prerequisites..."

    local missing=()
    command -v distrobox &>/dev/null || missing+=("distrobox")

    if ! command -v podman &>/dev/null && ! command -v docker &>/dev/null; then
        missing+=("podman (or docker)")
    fi

    if ! command -v systemctl &>/dev/null; then
        missing+=("systemctl")
    elif ! systemctl --user status &>/dev/null; then
        log_warn "systemd --user does not appear to be running (will try to continue)"
    fi

    if [[ ${#missing[@]} -gt 0 ]]; then
        log_error "Missing required host tools: ${missing[*]}"
        log_error "On Bazzite/SteamOS, distrobox and podman are pre-installed."
        log_error "On Silverblue: sudo rpm-ostree install distrobox podman && systemctl reboot"
        return 1
    fi

    log_ok "Host prerequisites satisfied (distrobox, podman/docker, systemctl)"
}

# ---------------------------------------------------------------------------
# asm_list_hidraw_flags
# Outputs one --additional-flags=--device=X per line for each /dev/hidrawN
# ---------------------------------------------------------------------------
asm_list_hidraw_flags() {
    for dev in /dev/hidraw*; do
        [[ -e "$dev" ]] && echo "--additional-flags=--device=$dev"
    done
}

# ---------------------------------------------------------------------------
# asm_pipewire_volume_flags  (fix B3: PipeWire sockets missing from container)
# Outputs one --additional-flags=--volume=... per line for each PW socket
# ---------------------------------------------------------------------------
asm_pipewire_volume_flags() {
    local rt="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
    if [[ -S "$rt/pipewire-0" ]]; then
        echo "--additional-flags=--volume=$rt/pipewire-0:$rt/pipewire-0"
    fi
    if [[ -d "$rt/pulse" ]]; then
        echo "--additional-flags=--volume=$rt/pulse:$rt/pulse"
    fi
}

# ---------------------------------------------------------------------------
# asm_container_exists
# ---------------------------------------------------------------------------
asm_container_exists() {
    distrobox list 2>/dev/null | grep -qw "$ASM_CONTAINER_NAME"
}

# ---------------------------------------------------------------------------
# asm_create_container <image>  (fix B3: passes PipeWire sockets to container)
# ---------------------------------------------------------------------------
asm_create_container() {
    local image="${1:?asm_create_container requires an image argument}"
    log_step "Creating Distrobox container '$ASM_CONTAINER_NAME' (image: $image)..."

    local hidraw_count
    hidraw_count=$(ls /dev/hidraw* 2>/dev/null | wc -l || echo 0)
    if [[ "$hidraw_count" -eq 0 ]]; then
        log_warn "No /dev/hidraw* devices found. Plug in your headset before running ASM."
        log_warn "If hidraw devices appear later, recreate the container with --reinstall."
    else
        log_info "Passing $hidraw_count hidraw device(s) to container"
    fi

    local create_cmd=(
        distrobox create
        --name "$ASM_CONTAINER_NAME"
        --image "$image"
        --home "$HOME"
        --pull
        --yes
    )

    while IFS= read -r flag; do
        [[ -n "$flag" ]] && create_cmd+=("$flag")
    done < <(asm_list_hidraw_flags)

    while IFS= read -r flag; do
        [[ -n "$flag" ]] && create_cmd+=("$flag")
    done < <(asm_pipewire_volume_flags)

    log_info "Running: ${create_cmd[*]}"
    "${create_cmd[@]}"
    log_ok "Container '$ASM_CONTAINER_NAME' created"
}

# ---------------------------------------------------------------------------
# asm_install_arch_in_container  (fix B5: pacman-key --init before makepkg)
# ---------------------------------------------------------------------------
asm_install_arch_in_container() {
    log_step "Installing ASM inside container (Arch / AUR)..."

    distrobox enter "$ASM_CONTAINER_NAME" -- bash -lc '
        set -euo pipefail

        echo "[arch-install] Initialising pacman keyring..."
        sudo pacman-key --init
        sudo pacman-key --populate archlinux

        echo "[arch-install] Updating system..."
        sudo pacman -Syu --noconfirm

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

# ---------------------------------------------------------------------------
# asm_install_fedora_in_container
# ---------------------------------------------------------------------------
asm_install_fedora_in_container() {
    log_step "Installing ASM inside container (Fedora / COPR)..."

    distrobox enter "$ASM_CONTAINER_NAME" -- bash -lc '
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
# asm_export_binaries
# ---------------------------------------------------------------------------
asm_export_binaries() {
    log_step "Exporting ASM binaries to host..."

    local binaries=(asm-daemon asm-gui asm-cli asm-setup asm-router)
    for bin in "${binaries[@]}"; do
        log_info "Exporting binary: $bin"
        distrobox enter "$ASM_CONTAINER_NAME" -- distrobox-export \
            --bin "/usr/bin/$bin" \
            --export-path "$HOME/.local/bin" \
            2>>"$ASM_LOG_FILE" || \
        log_warn "Could not export $bin (may already be exported or path differs)"
    done

    log_info "Exporting desktop entry..."
    distrobox enter "$ASM_CONTAINER_NAME" -- distrobox-export \
        --app arctis-sound-manager \
        2>>"$ASM_LOG_FILE" || \
    log_warn "Could not export desktop entry (may already be exported or not found)"

    log_ok "Binary and desktop exports done — binaries at $HOME/.local/bin/"
}

# ---------------------------------------------------------------------------
# asm_write_systemd_units  (fix B4: WantedBy includes gamescope-session.target)
# ---------------------------------------------------------------------------
asm_write_systemd_units() {
    log_step "Writing host systemd user units..."
    mkdir -p "$ASM_SYSTEMD_USER_DIR"

    cat > "$ASM_SYSTEMD_USER_DIR/arctis-manager.service" <<EOF
[Unit]
Description=Arctis Sound Manager (Distrobox)
After=pipewire.service pipewire-pulse.service
Wants=pipewire.service
StartLimitInterval=1min
StartLimitBurst=5

[Service]
Type=simple
ExecStart=/usr/bin/distrobox enter ${ASM_CONTAINER_NAME} -- /usr/bin/asm-daemon
Restart=on-failure
RestartSec=5

[Install]
WantedBy=graphical-session.target gamescope-session.target
EOF
    log_ok "Written: $ASM_SYSTEMD_USER_DIR/arctis-manager.service"

    cat > "$ASM_SYSTEMD_USER_DIR/arctis-gui.service" <<EOF
[Unit]
Description=Arctis Sound Manager — System Tray (Distrobox)
After=graphical-session.target arctis-manager.service
Wants=arctis-manager.service

[Service]
Type=simple
ExecStart=/usr/bin/distrobox enter ${ASM_CONTAINER_NAME} -- /usr/bin/asm-gui --systray
Restart=on-failure
RestartSec=5

[Install]
WantedBy=graphical-session.target gamescope-session.target
EOF
    log_ok "Written: $ASM_SYSTEMD_USER_DIR/arctis-gui.service"

    cat > "$ASM_SYSTEMD_USER_DIR/arctis-video-router.service" <<EOF
[Unit]
Description=Arctis Sound Manager — Media Router (Distrobox)
After=pipewire.service arctis-manager.service
Requires=pipewire.service

[Service]
Type=simple
ExecStart=/usr/bin/distrobox enter ${ASM_CONTAINER_NAME} -- /usr/bin/asm-router
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
EOF
    log_ok "Written: $ASM_SYSTEMD_USER_DIR/arctis-video-router.service"
}

# ---------------------------------------------------------------------------
# asm_install_udev_rules [mode]  (fix B2: sudo direct, no distrobox-host-exec)
# distrobox-host-exec only exists INSIDE a container; these scripts run on host.
# mode "steamos" → wrap with steamos-readonly disable/enable
# ---------------------------------------------------------------------------
asm_install_udev_rules() {
    local mode="${1:-}"
    log_step "Installing udev rules on host..."

    local rules_tmp
    rules_tmp="$(mktemp /tmp/91-steelseries-arctis.rules.XXXXXX)"

    log_info "Generating udev rules via asm-cli inside container..."
    distrobox enter "$ASM_CONTAINER_NAME" -- bash -lc \
        "asm-cli udev dump-rules" > "$rules_tmp"

    if [[ ! -s "$rules_tmp" ]]; then
        log_warn "asm-cli udev dump-rules produced empty output — skipping udev install"
        rm -f "$rules_tmp"
        return 0
    fi

    log_info "Rules generated: $(wc -l < "$rules_tmp") lines"

    if [[ "$mode" == "steamos" ]] && command -v steamos-readonly &>/dev/null; then
        log_warn "SteamOS: disabling read-only filesystem temporarily..."
        steamos-readonly disable || {
            log_error "steamos-readonly disable failed."
            log_error "Manual: steamos-readonly disable && sudo install -m644 $rules_tmp $ASM_UDEV_RULES_PATH"
            rm -f "$rules_tmp"
            return 1
        }
        log_info "steamos-readonly disabled"
    fi

    log_info "Installing rules to $ASM_UDEV_RULES_PATH (requires sudo)..."
    sudo install -m644 "$rules_tmp" "$ASM_UDEV_RULES_PATH"
    sudo udevadm control --reload-rules
    sudo udevadm trigger --subsystem-match=usb

    if [[ "$mode" == "steamos" ]] && command -v steamos-readonly &>/dev/null; then
        steamos-readonly enable 2>/dev/null || true
        log_info "steamos-readonly re-enabled"
    fi

    rm -f "$rules_tmp"
    log_ok "udev rules installed and reloaded"
}

# ---------------------------------------------------------------------------
# asm_verify_pipewire
# ---------------------------------------------------------------------------
asm_verify_pipewire() {
    log_step "Verifying PipeWire access from container..."

    if distrobox enter "$ASM_CONTAINER_NAME" -- bash -lc 'pactl info &>/dev/null'; then
        log_ok "pactl: PulseAudio/PipeWire accessible from container"
    else
        log_warn "pactl info failed inside container — PipeWire may not be accessible"
        log_warn "This is often resolved after a reboot (session bus not available during install)"
    fi

    if distrobox enter "$ASM_CONTAINER_NAME" -- bash -lc 'pw-cli info 0 &>/dev/null'; then
        log_ok "pw-cli: PipeWire native socket accessible from container"
    else
        log_warn "pw-cli info 0 failed — PipeWire native socket not accessible"
        log_warn "Ensure PIPEWIRE_RUNTIME_DIR is set; may work after full session restart"
    fi
}

# ---------------------------------------------------------------------------
# asm_enable_services
# ---------------------------------------------------------------------------
asm_enable_services() {
    log_step "Enabling ASM systemd user services..."
    systemctl --user daemon-reload

    local services=(arctis-manager.service arctis-video-router.service arctis-gui.service)
    for svc in "${services[@]}"; do
        if systemctl --user enable --now "$svc" 2>>"$ASM_LOG_FILE"; then
            log_ok "Enabled and started: $svc"
        else
            log_warn "Could not enable $svc (may need a full desktop session restart)"
        fi
    done
}

# ---------------------------------------------------------------------------
# asm_print_summary <distro_label>
# ---------------------------------------------------------------------------
asm_print_summary() {
    local label="${1:-Unknown}"
    echo ""
    echo "============================================================"
    echo "  Arctis Sound Manager — Distrobox install complete ($label)"
    echo "============================================================"
    echo ""
    echo "Container : $ASM_CONTAINER_NAME"
    echo "Log file  : $ASM_LOG_FILE"
    echo ""
    echo "Services running:"
    for svc in arctis-manager arctis-video-router arctis-gui; do
        local status
        status=$(systemctl --user is-active "${svc}.service" 2>/dev/null || echo "inactive")
        printf "  %-35s %s\n" "${svc}.service" "$status"
    done
    echo ""
    echo "How to test:"
    echo "  journalctl --user -u arctis-manager.service -f"
    echo "  distrobox enter $ASM_CONTAINER_NAME -- asm-gui"
    echo "  export PATH=\"\$HOME/.local/bin:\$PATH\" && asm-gui"
    echo "  bash scripts/distrobox/uninstall.sh"
    echo "============================================================"
}
