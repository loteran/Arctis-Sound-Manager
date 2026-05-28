#!/usr/bin/env bash
# Arctis Sound Manager — Distrobox installer for Fedora Silverblue / Kinoite
# Self-contained: no external dependencies, safe to run via curl | bash
#
# Usage:
#   bash <(curl -fsSL https://raw.githubusercontent.com/loteran/Arctis-Sound-Manager/main/scripts/distrobox/silverblue.sh)
#   bash silverblue.sh [--reinstall] [--no-services] [-h]
set -euo pipefail

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_CONTAINER="arctis-sound-manager"
_LOG="${XDG_CACHE_HOME:-$HOME/.cache}/asm-distrobox-install.log"
_SYSTEMD_USER_DIR="$HOME/.config/systemd/user"
_UDEV_RULES="/etc/udev/rules.d/91-steelseries-arctis.rules"
_HIDRAW_SYMLINK_RULES="/etc/udev/rules.d/90-asm-hidraw-symlink.rules"
_HIDRAW_RUN_DIR="/run/asm-hidraw"
_HIDRAW_TMPFILES_CONF="/etc/tmpfiles.d/asm-hidraw.conf"
_IMAGE="registry.fedoraproject.org/fedora-toolbox:41"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
_log()      { local l="$1"; shift; echo "$(date '+%Y-%m-%d %H:%M:%S') [$l] $*" | tee -a "$_LOG"; }
log_info()  { _log "INFO " "$@"; }
log_warn()  { _log "WARN " "$@" >&2; }
log_error() { _log "ERROR" "$@" >&2; }
log_step()  { echo ""; _log "====>" "$@"; }
log_ok()    { _log " OK  " "$@"; }

# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------
usage() {
    cat <<'EOF'
Arctis Sound Manager — Distrobox installer for Fedora Silverblue / Kinoite

Usage:
  bash silverblue.sh [options]

Options:
  --reinstall    Remove and recreate the container, then reinstall
  --no-services  Skip enabling systemd services after install
  -h, --help     Show this help message

Note: uses Fedora 41 + ASM COPR. The rnnoise plugin (ClearCast) is installed
automatically via uriesk/noise-suppression-for-voice COPR during ASM setup.

Log: ~/.cache/asm-distrobox-install.log
EOF
}

# ---------------------------------------------------------------------------
# Arg parsing
# ---------------------------------------------------------------------------
DO_REINSTALL=0
SKIP_SERVICES=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --reinstall)   DO_REINSTALL=1 ;;
        --no-services) SKIP_SERVICES=1 ;;
        -h|--help)     usage; exit 0 ;;
        *) log_error "Unknown argument: $1"; usage; exit 1 ;;
    esac
    shift
done

trap 'log_error "Script FAILED at line $LINENO — see $_LOG for details"' ERR

# ---------------------------------------------------------------------------
# Prerequisites check
# ---------------------------------------------------------------------------
check_prereqs() {
    mkdir -p "$(dirname "$_LOG")"
    log_step "Checking host prerequisites..."
    local missing=()
    command -v distrobox &>/dev/null || missing+=("distrobox")
    command -v podman &>/dev/null || command -v docker &>/dev/null || missing+=("podman (or docker)")
    command -v systemctl &>/dev/null || missing+=("systemctl")
    if [[ ${#missing[@]} -gt 0 ]]; then
        log_error "Missing required host tools: ${missing[*]}"
        log_error "On Silverblue: sudo rpm-ostree install distrobox podman && systemctl reboot"
        return 1
    fi
    systemctl --user status &>/dev/null || log_warn "systemd --user not running — continuing anyway"
    log_ok "Host prerequisites satisfied"
}

# ---------------------------------------------------------------------------
# Container helpers
# ---------------------------------------------------------------------------
container_exists() {
    distrobox list 2>/dev/null | grep -qw "$_CONTAINER"
}

create_container() {
    log_step "Creating container '$_CONTAINER' (image: $_IMAGE)..."
    local cmd=(distrobox create --name "$_CONTAINER" --image "$_IMAGE" --home "$HOME" --pull --yes)

    ensure_hidraw_dir
    cmd+=("--volume" "$_HIDRAW_RUN_DIR:$_HIDRAW_RUN_DIR:rslave")
    [[ -d /dev/bus/usb ]] && cmd+=("--volume" "/dev/bus/usb:/dev/bus/usb:rslave")

    local rt="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
    [[ -S "$rt/pipewire-0" ]]         && cmd+=("--volume" "$rt/pipewire-0:$rt/pipewire-0")
    [[ -S "$rt/pipewire-0-manager" ]] && cmd+=("--volume" "$rt/pipewire-0-manager:$rt/pipewire-0-manager")
    [[ -d "$rt/pulse" ]]              && cmd+=("--volume" "$rt/pulse:$rt/pulse")

    log_info "Running: ${cmd[*]}"
    "${cmd[@]}"
    log_ok "Container '$_CONTAINER' created"
}

verify_container_health() {
    log_step "Verifying container health..."
    local timeout=30 elapsed=0
    until distrobox enter "$_CONTAINER" -- true 2>/dev/null; do
        sleep 1; elapsed=$((elapsed + 1))
        if [[ $elapsed -ge $timeout ]]; then
            log_error "Container not responding after ${timeout}s."
            log_error "Inspect: distrobox enter $_CONTAINER -- bash"
            return 1
        fi
    done
    log_ok "Container healthy (${elapsed}s)"
}

# ---------------------------------------------------------------------------
# Persistent /run/asm-hidraw provisioning
# ---------------------------------------------------------------------------
# /run is a tmpfs wiped at every boot. Without this, the container bind-mount
# fails on the next boot with: crun: cannot stat /run/asm-hidraw
# systemd-tmpfiles runs at sysinit.target, well before distrobox starts.
install_hidraw_tmpfiles() {
    log_step "Installing systemd-tmpfiles rule for $_HIDRAW_RUN_DIR..."
    sudo tee "$_HIDRAW_TMPFILES_CONF" >/dev/null <<'TMPF'
# Arctis Sound Manager — recreate /run/asm-hidraw at every boot so the
# Distrobox container bind-mount source exists before crun starts the container.
d /run/asm-hidraw 0755 root root - -
TMPF
    sudo systemd-tmpfiles --create "$_HIDRAW_TMPFILES_CONF" \
        || sudo mkdir -p "$_HIDRAW_RUN_DIR"
    log_ok "tmpfiles rule: $_HIDRAW_TMPFILES_CONF"
}

ensure_hidraw_dir() {
    [[ -d "$_HIDRAW_RUN_DIR" ]] && return 0
    sudo mkdir -p "$_HIDRAW_RUN_DIR"
    sudo chmod 0755 "$_HIDRAW_RUN_DIR"
}

# ---------------------------------------------------------------------------
# Install ASM inside container via Fedora COPR
# ---------------------------------------------------------------------------
install_asm() {
    log_step "Installing ASM inside container (Fedora / COPR)..."
    distrobox enter "$_CONTAINER" -- bash -lc '
        set -euo pipefail
        echo "[fedora-install] Updating system..."
        sudo dnf upgrade -y
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
# Export binaries to host
# ---------------------------------------------------------------------------
export_binaries() {
    log_step "Exporting ASM binaries to host..."
    for bin in asm-daemon asm-gui asm-cli asm-setup asm-router; do
        distrobox enter "$_CONTAINER" -- distrobox-export \
            --bin "/usr/bin/$bin" --export-path "$HOME/.local/bin" 2>>"$_LOG" \
            || log_warn "Could not export $bin"
    done
    distrobox enter "$_CONTAINER" -- distrobox-export --app /usr/share/applications/ArctisManager.desktop 2>>"$_LOG" \
        || log_warn "Could not export desktop entry"
    log_ok "Binaries at $HOME/.local/bin/"
}

# ---------------------------------------------------------------------------
# Write systemd units on host
# ---------------------------------------------------------------------------
write_systemd_units() {
    log_step "Writing host systemd user units..."
    mkdir -p "$_SYSTEMD_USER_DIR"

    cat > "$_SYSTEMD_USER_DIR/arctis-manager.service" <<EOF
[Unit]
Description=Arctis Sound Manager (Distrobox)
After=pipewire.service pipewire-pulse.service
Wants=pipewire.service
ConditionPathIsDirectory=/run/asm-hidraw
StartLimitInterval=1min
StartLimitBurst=5

[Service]
Type=simple
ExecStart=/usr/bin/distrobox enter ${_CONTAINER} -- /usr/bin/asm-daemon
Restart=on-failure
RestartSec=5

[Install]
WantedBy=graphical-session.target
EOF

    cat > "$_SYSTEMD_USER_DIR/arctis-gui.service" <<EOF
[Unit]
Description=Arctis Sound Manager — System Tray (Distrobox)
After=graphical-session.target arctis-manager.service
Wants=arctis-manager.service

[Service]
Type=simple
ExecStart=/usr/bin/distrobox enter ${_CONTAINER} -- /usr/bin/asm-gui --systray
Restart=on-failure
RestartSec=5

[Install]
WantedBy=graphical-session.target
EOF

    cat > "$_SYSTEMD_USER_DIR/arctis-video-router.service" <<EOF
[Unit]
Description=Arctis Sound Manager — Media Router (Distrobox)
After=pipewire.service arctis-manager.service
Requires=pipewire.service

[Service]
Type=simple
ExecStart=/usr/bin/distrobox enter ${_CONTAINER} -- /usr/bin/asm-router
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
EOF
    log_ok "systemd units written"
}

# ---------------------------------------------------------------------------
# Install udev rules on host
# ---------------------------------------------------------------------------
install_udev_rules() {
    log_step "Installing udev rules on host..."

    sudo tee "$_HIDRAW_SYMLINK_RULES" >/dev/null <<'RULES'
# Arctis Sound Manager — hot-plug hidraw symlink rule
ACTION!="add|change|remove", GOTO="asm_hidraw_end"
SUBSYSTEM!="hidraw",          GOTO="asm_hidraw_end"
ACTION=="add|change", ATTRS{idVendor}=="1038", \
    RUN+="/bin/sh -c 'mkdir -p /run/asm-hidraw && ln -sf /dev/$kernel /run/asm-hidraw/$kernel'"
ACTION=="remove", SUBSYSTEM=="hidraw", \
    RUN+="/bin/sh -c 'rm -f /run/asm-hidraw/$kernel'"
LABEL="asm_hidraw_end"
RULES
    log_ok "Hot-plug hidraw rule: $_HIDRAW_SYMLINK_RULES"

    local rules_tmp
    rules_tmp="$(mktemp /tmp/91-steelseries-arctis.rules.XXXXXX)"
    distrobox enter "$_CONTAINER" -- bash -lc "asm-cli udev dump-rules" > "$rules_tmp"
    if [[ -s "$rules_tmp" ]]; then
        sudo install -m644 "$rules_tmp" "$_UDEV_RULES"
        log_ok "Device rules: $_UDEV_RULES"
    else
        log_warn "asm-cli udev dump-rules empty — skipping device rules"
    fi
    rm -f "$rules_tmp"

    sudo udevadm control --reload-rules
    sudo udevadm trigger --subsystem-match=usb --attr-match=idVendor=1038 \
        || sudo udevadm trigger --subsystem-match=hidraw
    log_ok "udev rules reloaded"
}

# ---------------------------------------------------------------------------
# Verify PipeWire access from container
# ---------------------------------------------------------------------------
verify_pipewire() {
    log_step "Verifying PipeWire access from container..."
    distrobox enter "$_CONTAINER" -- bash -lc 'pactl info &>/dev/null' \
        && log_ok "PipeWire accessible from container" \
        || log_warn "pactl info failed — may work after reboot"
}

# ---------------------------------------------------------------------------
# Enable services
# ---------------------------------------------------------------------------
enable_services() {
    log_step "Enabling ASM systemd user services..."
    systemctl --user daemon-reload
    for svc in arctis-manager.service arctis-video-router.service arctis-gui.service; do
        systemctl --user enable --now "$svc" 2>>"$_LOG" \
            && log_ok "Enabled: $svc" \
            || log_warn "Could not enable $svc (may need a desktop session restart)"
    done
}

# ---------------------------------------------------------------------------
# Reload PipeWire on host
# ---------------------------------------------------------------------------
reload_pipewire() {
    log_step "Reloading PipeWire on host..."
    if systemctl --user is-active pipewire.service &>/dev/null; then
        systemctl --user restart pipewire pipewire-pulse wireplumber 2>/dev/null \
            && log_ok "PipeWire restarted" \
            || log_warn "PipeWire restart partially failed — check manually"
    else
        log_warn "pipewire.service not active — skipping"
    fi
}

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print_summary() {
    echo ""
    echo "============================================================"
    echo "  Arctis Sound Manager — Distrobox install complete (Silverblue)"
    echo "============================================================"
    echo "Container : $_CONTAINER"
    echo "Log file  : $_LOG"
    echo ""
    echo "Services:"
    for svc in arctis-manager arctis-video-router arctis-gui; do
        printf "  %-35s %s\n" "${svc}.service" \
            "$(systemctl --user is-active "${svc}.service" 2>/dev/null || echo inactive)"
    done
    echo ""
    echo "How to test:"
    echo "  journalctl --user -u arctis-manager.service -f"
    echo "  distrobox enter $_CONTAINER -- asm-gui"
    echo "============================================================"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
log_info "=== ASM Distrobox install (Silverblue) started ($(date)) ==="

if ! grep -qiE 'silverblue|kinoite' /etc/os-release 2>/dev/null; then
    log_warn "Host does not appear to be Silverblue/Kinoite — continuing anyway (Ctrl-C to abort)"
    sleep 5
fi

check_prereqs
install_hidraw_tmpfiles

if [[ $DO_REINSTALL -eq 1 ]] && container_exists; then
    log_step "Reinstall: removing existing container..."
    distrobox rm --force "$_CONTAINER"
    log_ok "Container removed"
fi

if container_exists; then
    log_step "Container already exists — upgrading ASM (skipping create)..."
else
    create_container
fi

verify_container_health || exit 1
install_asm
export_binaries
write_systemd_units
install_udev_rules
verify_pipewire

[[ $SKIP_SERVICES -eq 0 ]] && enable_services || log_info "Skipping service activation (--no-services)"
[[ "${ASM_RESTART_PIPEWIRE:-1}" == "1" ]] && reload_pipewire

print_summary
log_info "=== ASM Distrobox install (Silverblue) finished ==="
