#!/usr/bin/env bash
# Arctis Sound Manager — Distrobox installer for SteamOS / Steam Deck
# Self-contained: no external dependencies, safe to run via curl | bash
#
# Usage:
#   bash <(curl -fsSL https://raw.githubusercontent.com/loteran/Arctis-Sound-Manager/main/scripts/distrobox/steamos.sh)
#   bash steamos.sh [--reinstall] [--no-services] [-h]
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
_IMAGE="docker.io/library/archlinux:latest"

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
Arctis Sound Manager — Distrobox installer for SteamOS / Steam Deck

Usage:
  bash steamos.sh [options]

Options:
  --reinstall    Remove and recreate the container, then reinstall
  --no-services  Skip enabling systemd services after install
  -h, --help     Show this help message

Note: udev rules are written to /etc/udev/rules.d/ which is reset on major
SteamOS updates. Re-run this script after every SteamOS version upgrade.

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
        log_error "On SteamOS, distrobox and podman are pre-installed."
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
# NOTE: on SteamOS /etc is read-only — call this only while steamos-readonly
# is disabled (i.e. from within install_udev_rules).
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
# Install ASM inside container via AUR
# ---------------------------------------------------------------------------
install_asm() {
    log_step "Installing ASM inside container (Arch / AUR)..."
    distrobox enter "$_CONTAINER" -- bash -lc '
        set -euo pipefail
        echo "[arch-install] Checking pacman keyring..."
        # --init unconditionally: it is idempotent, cheap, and it is what
        # creates the LOCAL SIGNING KEY that `pacman-key --lsign-key` needs
        # below. Testing pubring.gpg only proves the *public* keyring is
        # populated, which is exactly how an Arch distrobox image ships: --init
        # was skipped, and registering the repository then failed with
        # "There is no secret key available to sign with", leaving the
        # container with no ASM in it. Reported on Discord by binx9612.
        sudo pacman-key --init
        if ! sudo test -s /etc/pacman.d/gnupg/pubring.gpg; then
            # Populating is the expensive half, so it stays conditional.
            sudo pacman-key --populate archlinux
        fi
        echo "[arch-install] Updating system..."
        sudo pacman -Syu --noconfirm
        sudo pacman -S --needed --noconfirm curl || true

        # The signed binary repository is the primary Arch channel, and the
        # only one that keeps working while aur.archlinux.org git is in
        # maintenance — it has been since 1.2.19, which left Steam Deck users
        # stranded on an old version with no way to update (issue #175).
        # Adding it here also means later updates are a plain `pacman -Syu`
        # inside the container, with no PKGBUILD editing.
        # Registering the repository must never be fatal. This whole block runs
        # under `set -e`, so a failure in here (no network for the key, a
        # keyring pacman-key cannot sign into, gpg missing) would abort the
        # install *before* the AUR fallback below ever runs: the container gets
        # created, nothing is installed into it, and the user is left with a
        # distrobox terminal wondering why `asm-gui` is not found. Guarded so a
        # failure costs us the repository, never the installation.
        if ! grep -q "^\[arctis-sound-manager\]" /etc/pacman.conf; then
            echo "[arch-install] Adding the signed ASM repository..."
            if curl -fsSL https://github.com/loteran/Arctis-Sound-Manager/releases/download/pacman-repo/arctis-sound-manager.key -o /tmp/asm.key \
               && sudo pacman-key --add /tmp/asm.key \
               && sudo pacman-key --lsign-key "$(gpg --show-keys --with-colons /tmp/asm.key | awk -F: "/^fpr/ {print \$10; exit}")"; then
                printf "\n[arctis-sound-manager]\nServer = https://github.com/loteran/Arctis-Sound-Manager/releases/download/pacman-repo\n" \
                    | sudo tee -a /etc/pacman.conf >/dev/null
            else
                echo "[arch-install] Could not register the signed repository (see the error above)." >&2
                echo "[arch-install] The install below will fail; its output says what to check." >&2
            fi
            rm -f /tmp/asm.key
        fi

        # No AUR fallback here, deliberately. It reads like a safety net and is
        # not one: the AUR PKGBUILD fetches its tarball from GitHub Releases,
        # the same host as this repository, so it removes no dependency and
        # adds one (aur.archlinux.org, unreachable for weeks during the
        # maintenance window that motivated moving to this repo in #175). It
        # cannot help when GitHub is down, cannot help when the AUR is down,
        # and for a local keyring problem it spends several minutes building
        # base-devel and paru to work around something an error message
        # resolves. Failing here with the reason is more useful than a long
        # detour that hides it.
        echo "[arch-install] Installing arctis-sound-manager from the signed repository..."
        if ! sudo pacman -Sy --noconfirm arctis-sound-manager; then
            echo "[arch-install] FAILED to install arctis-sound-manager." >&2
            echo "[arch-install] The container exists but is empty, which is why asm-gui" >&2
            echo "[arch-install] would not be found. To see why, run inside the container:" >&2
            echo "[arch-install]   distrobox enter arctis-sound-manager" >&2
            echo "[arch-install]   grep -A2 arctis-sound-manager /etc/pacman.conf" >&2
            echo "[arch-install]   sudo pacman -Sy arctis-sound-manager" >&2
            echo "[arch-install] and report the output at https://github.com/loteran/Arctis-Sound-Manager/issues" >&2
            exit 1
        fi
        echo "[arch-install] Done."
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
# Write systemd units on host (Steam Deck: also targets gamescope-session)
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
WantedBy=graphical-session.target gamescope-session.target
EOF

    cat > "$_SYSTEMD_USER_DIR/app-ArctisManager.service" <<EOF
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
WantedBy=graphical-session.target gamescope-session.target
EOF

    cat > "$_SYSTEMD_USER_DIR/arctis-video-router.service" <<EOF
[Unit]
Description=Arctis Sound Manager — Media Router (Distrobox)
After=pipewire.service arctis-manager.service
Requires=pipewire.service

[Service]
Type=simple
ExecStart=/usr/bin/distrobox enter ${_CONTAINER} -- /usr/bin/asm-router
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
EOF
    log_ok "systemd units written"
}

# ---------------------------------------------------------------------------
# Install udev rules on host (with steamos-readonly disable/enable)
# ---------------------------------------------------------------------------
install_udev_rules() {
    log_step "Installing udev rules on host..."

    if command -v steamos-readonly &>/dev/null; then
        log_warn "SteamOS: disabling read-only filesystem temporarily..."
        # sudo, like every other privileged call in this script. Without it
        # the command fails as the ordinary user, install_udev_rules returns
        # 1, and the install stops after having already reported progress —
        # so it looks like the script half-worked (#188).
        sudo steamos-readonly disable || { log_error "steamos-readonly disable failed"; return 1; }
        trap 'sudo steamos-readonly enable 2>/dev/null || true' RETURN
    fi

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

    # Install tmpfiles rule while /etc is still writable (steamos-readonly disabled)
    install_hidraw_tmpfiles

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
    # trap RETURN re-enables steamos-readonly here
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
    for svc in arctis-manager.service arctis-video-router.service app-ArctisManager.service; do
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
    echo "  Arctis Sound Manager — Distrobox install complete (SteamOS)"
    echo "============================================================"
    echo "Container : $_CONTAINER"
    echo "Log file  : $_LOG"
    echo ""
    echo "Services:"
    for svc in arctis-manager arctis-video-router app-ArctisManager; do
        printf "  %-35s %s\n" "${svc}.service" \
            "$(systemctl --user is-active "${svc}.service" 2>/dev/null || echo inactive)"
    done
    echo ""
    echo "Note: udev rules reset on SteamOS upgrades — re-run this script after each update."
    echo ""
    echo "How to test:"
    echo "  journalctl --user -u arctis-manager.service -f"
    echo "  distrobox enter $_CONTAINER -- asm-gui"
    echo "============================================================"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
log_info "=== ASM Distrobox install (SteamOS) started ($(date)) ==="

if ! [[ -f /etc/steamos-release ]] && ! command -v steamos-readonly &>/dev/null; then
    log_warn "Host does not appear to be SteamOS — continuing anyway (Ctrl-C to abort)"
    sleep 5
fi

echo ""
echo "=========================================================="
echo "  IMPORTANT — SteamOS notes:"
echo "  1. Set a sudo password if not done yet: passwd"
echo "  2. udev rules reset on major SteamOS updates — re-run"
echo "     this script after each SteamOS version upgrade."
echo "=========================================================="
echo ""

check_prereqs

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
log_info "=== ASM Distrobox install (SteamOS) finished ==="
