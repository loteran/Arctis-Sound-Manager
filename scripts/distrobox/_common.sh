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
ASM_HIDRAW_SYMLINK_RULES="/etc/udev/rules.d/90-asm-hidraw-symlink.rules"
ASM_HIDRAW_RUN_DIR="/run/asm-hidraw"

# Root of the ASM scripts/ directory (resolved relative to this file)
ASM_SCRIPT_DIR="${ASM_SCRIPT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

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
# P0-B: hidraw are now managed via /run/asm-hidraw (udev symlink dir, rslave).
# Kept as a no-op so callers don't need updating.
# ---------------------------------------------------------------------------
asm_list_hidraw_flags() {
    return 0
}

# ---------------------------------------------------------------------------
# asm_hidraw_volume_flag  (P0-B: stable hot-plug hidraw via udev symlink dir)
# Outputs a bare --volume=... flag (no --additional-flags= prefix).
# ---------------------------------------------------------------------------
asm_hidraw_volume_flag() {
    sudo mkdir -p "$ASM_HIDRAW_RUN_DIR"
    echo "--volume=$ASM_HIDRAW_RUN_DIR:$ASM_HIDRAW_RUN_DIR:rslave"
}

# ---------------------------------------------------------------------------
# asm_usb_bus_volume_flag  (P0-A: expose /dev/bus/usb so libusb/PyUSB works)
# Outputs a bare --volume=... flag (no --additional-flags= prefix).
# ---------------------------------------------------------------------------
asm_usb_bus_volume_flag() {
    if [[ -d /dev/bus/usb ]]; then
        echo "--volume=/dev/bus/usb:/dev/bus/usb:rslave"
    fi
}

# ---------------------------------------------------------------------------
# asm_pipewire_volume_flags  (fix B3 + P2-B: PipeWire sockets for container)
# Outputs one --volume=... per line for each PW socket
# ---------------------------------------------------------------------------
asm_pipewire_volume_flags() {
    local rt="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
    [[ -S "$rt/pipewire-0" ]]         && echo "--volume=$rt/pipewire-0:$rt/pipewire-0"
    [[ -S "$rt/pipewire-0-manager" ]] && echo "--volume=$rt/pipewire-0-manager:$rt/pipewire-0-manager"
    [[ -d "$rt/pulse" ]]              && echo "--volume=$rt/pulse:$rt/pulse"
}

# ---------------------------------------------------------------------------
# asm_container_exists
# ---------------------------------------------------------------------------
asm_container_exists() {
    distrobox list 2>/dev/null | grep -qw "$ASM_CONTAINER_NAME"
}

# ---------------------------------------------------------------------------
# asm_create_container <image>  (P0-A, P0-B, P2-B: USB bus + hidraw + PipeWire)
# ---------------------------------------------------------------------------
asm_create_container() {
    local image="${1:?asm_create_container requires an image argument}"
    log_step "Creating Distrobox container '$ASM_CONTAINER_NAME' (image: $image)..."

    log_info "hidraw hot-plug handled via $ASM_HIDRAW_RUN_DIR (udev rslave mount)"

    local create_cmd=(
        distrobox create
        --name "$ASM_CONTAINER_NAME"
        --image "$image"
        --home "$HOME"
        --pull
        --yes
    )

    # Collect all extra volume flags into one space-separated string, then pass
    # as a single --additional-flags "..." argument (distrobox requires two
    # separate argv elements; --additional-flags=VALUE is not accepted).
    local extra_flags=""
    local hidraw_flag usb_flag pw_flag
    hidraw_flag="$(asm_hidraw_volume_flag)"
    [[ -n "$hidraw_flag" ]] && extra_flags+=" $hidraw_flag"

    usb_flag="$(asm_usb_bus_volume_flag)"
    [[ -n "$usb_flag" ]] && extra_flags+=" $usb_flag"

    while IFS= read -r pw_flag; do
        [[ -n "$pw_flag" ]] && extra_flags+=" $pw_flag"
    done < <(asm_pipewire_volume_flags)

    [[ -n "$extra_flags" ]] && create_cmd+=(--additional-flags "${extra_flags# }")

    log_info "Running: ${create_cmd[*]}"
    "${create_cmd[@]}"
    log_ok "Container '$ASM_CONTAINER_NAME' created"
}

# ---------------------------------------------------------------------------
# asm_verify_container_health  (P2-A: ensure container responds before install)
# ---------------------------------------------------------------------------
asm_verify_container_health() {
    log_step "Verifying container health..."
    local timeout=30 elapsed=0
    until distrobox enter "$ASM_CONTAINER_NAME" -- true 2>/dev/null; do
        sleep 1
        elapsed=$((elapsed + 1))
        if [[ $elapsed -ge $timeout ]]; then
            log_error "Container '$ASM_CONTAINER_NAME' not responding after ${timeout}s."
            log_error "Inspect: distrobox enter $ASM_CONTAINER_NAME -- bash"
            log_error "Or delete and retry: bash scripts/distrobox-install.sh --reinstall"
            return 1
        fi
    done
    log_ok "Container healthy (${elapsed}s)"
}

# ---------------------------------------------------------------------------
# asm_install_arch_in_container  (B5 + P1-C + P3-A + P0-A)
# ---------------------------------------------------------------------------
asm_install_arch_in_container() {
    log_step "Installing ASM inside container (Arch / AUR)..."

    distrobox enter "$ASM_CONTAINER_NAME" -- bash -lc '
        set -euo pipefail

        # P1-C: init keyring only if not already initialised
        echo "[arch-install] Checking pacman keyring..."
        if [[ ! -d /etc/pacman.d/gnupg ]] || ! sudo test -s /etc/pacman.d/gnupg/pubring.gpg; then
            echo "[arch-install] Initialising pacman keyring (first time)..."
            sudo pacman-key --init
            sudo pacman-key --populate archlinux
        else
            echo "[arch-install] Keyring already initialised — skipping"
        fi

        echo "[arch-install] Updating system..."
        sudo pacman -Syu --noconfirm

        if ! command -v paru &>/dev/null; then
            echo "[arch-install] Installing paru (AUR helper)..."
            # P0-A: include libusb and hidapi so PyUSB can open the headset
            sudo pacman -S --needed --noconfirm base-devel git libusb hidapi
            # P3-A: trap ensures tmpdir is cleaned even if makepkg fails
            tmpdir=$(mktemp -d)
            trap "rm -rf \"$tmpdir\"" EXIT
            git clone https://aur.archlinux.org/paru-bin.git "$tmpdir/paru"
            (cd "$tmpdir/paru" && makepkg -si --noconfirm)
            trap - EXIT
            rm -rf "$tmpdir"
        fi

        echo "[arch-install] Installing arctis-sound-manager from AUR..."
        paru -S --noconfirm arctis-sound-manager

        echo "[arch-install] Done."
    '
}

# ---------------------------------------------------------------------------
# asm_install_fedora_in_container  (P0-A: add libusbx + hidapi for PyUSB)
# ---------------------------------------------------------------------------
asm_install_fedora_in_container() {
    log_step "Installing ASM inside container (Fedora / COPR)..."

    distrobox enter "$ASM_CONTAINER_NAME" -- bash -lc '
        set -euo pipefail

        echo "[fedora-install] Updating system..."
        sudo dnf upgrade -y

        # P0-A: libusb + hidapi so PyUSB can open the headset
        echo "[fedora-install] Installing USB libraries..."
        sudo dnf install -y libusbx hidapi

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
# asm_write_systemd_units <mode>  (P1-D: gamescope-session.target is conditional)
# mode: desktop (default) | steamdeck
# ---------------------------------------------------------------------------
asm_write_systemd_units() {
    local mode="${1:-desktop}"
    log_step "Writing host systemd user units (mode=$mode)..."
    mkdir -p "$ASM_SYSTEMD_USER_DIR"

    # gamescope-session.target only exists on Steam Deck / Bazzite Game Mode
    local wanted_by="graphical-session.target"
    if [[ "$mode" == "steamdeck" ]]; then
        wanted_by="graphical-session.target gamescope-session.target"
    fi

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
WantedBy=${wanted_by}
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
WantedBy=${wanted_by}
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
# asm_install_udev_rules [mode]  (B2 + P0-B + P2-C + P2-D)
# mode "steamos" → wraps write operations with steamos-readonly disable/enable
# ---------------------------------------------------------------------------
asm_install_udev_rules() {
    local mode="${1:-}"
    log_step "Installing udev rules on host..."

    # P2-D: guarantee steamos-readonly is re-enabled even if we exit early
    if [[ "$mode" == "steamos" ]] && command -v steamos-readonly &>/dev/null; then
        log_warn "SteamOS: disabling read-only filesystem temporarily..."
        if ! steamos-readonly disable; then
            log_error "steamos-readonly disable failed — cannot write udev rules."
            return 1
        fi
        trap 'steamos-readonly enable 2>/dev/null || true' RETURN
        log_info "steamos-readonly disabled (will re-enable on function return)"
    fi

    # P0-B: install the hot-plug symlink rule (static file, not generated)
    local hotplug_src="$ASM_SCRIPT_DIR/distrobox/udev-helpers/90-asm-hidraw-symlink.rules"
    if [[ -f "$hotplug_src" ]]; then
        sudo install -m644 "$hotplug_src" "$ASM_HIDRAW_SYMLINK_RULES"
        log_ok "Hot-plug hidraw rule installed: $ASM_HIDRAW_SYMLINK_RULES"
    else
        log_warn "Hot-plug rule not found at $hotplug_src — hidraw hot-plug will not work"
    fi

    # Generated device rules (ASM-specific, per device YAML)
    local rules_tmp
    rules_tmp="$(mktemp /tmp/91-steelseries-arctis.rules.XXXXXX)"

    log_info "Generating udev rules via asm-cli inside container..."
    distrobox enter "$ASM_CONTAINER_NAME" -- bash -lc \
        "asm-cli udev dump-rules" > "$rules_tmp"

    if [[ ! -s "$rules_tmp" ]]; then
        log_warn "asm-cli udev dump-rules produced empty output — skipping device rules"
        rm -f "$rules_tmp"
    else
        log_info "Rules generated: $(wc -l < "$rules_tmp") lines"
        sudo install -m644 "$rules_tmp" "$ASM_UDEV_RULES_PATH"
        rm -f "$rules_tmp"
    fi

    sudo udevadm control --reload-rules
    # P2-C: trigger only SteelSeries devices, not every USB device on the system
    sudo udevadm trigger --subsystem-match=usb --attr-match=idVendor=1038 \
        || sudo udevadm trigger --subsystem-match=hidraw

    log_ok "udev rules installed and reloaded"
    # trap RETURN re-enables steamos-readonly here if mode==steamos
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
# asm_reload_pipewire_host  (P3-B: pick up filter-chain configs written by asm-setup)
# ---------------------------------------------------------------------------
asm_reload_pipewire_host() {
    log_step "Reloading PipeWire on host (picks up new filter-chain configs)..."
    if systemctl --user is-active pipewire.service &>/dev/null; then
        systemctl --user restart pipewire pipewire-pulse wireplumber 2>/dev/null \
            && log_ok "PipeWire restarted" \
            || log_warn "PipeWire restart partially failed — check manually"
    else
        log_warn "pipewire.service not active — skipping restart"
    fi
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
