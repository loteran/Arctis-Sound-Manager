#!/usr/bin/env bash
# Arctis Sound Manager — uninstaller
#
# Detects every existing install (pipx + system packages) and lets the user
# pick which one(s) to remove. Designed for the common case "I want to drop
# the system package and switch to pipx" (or vice-versa) without nuking the
# install I want to keep.
#
# Usage:
#   bash scripts/uninstall.sh                # interactive
#   bash scripts/uninstall.sh --all          # remove every detected install
#   bash scripts/uninstall.sh --pipx         # remove only the pipx install
#   bash scripts/uninstall.sh --pkg          # remove only the distro package
#   bash scripts/uninstall.sh --purge        # also wipe ~/.config/arctis_manager,
#                                            # PipeWire configs and udev rules
#   bash scripts/uninstall.sh --yes          # skip confirmations
#
# Run remote (no clone needed):
#   curl -fsSL https://raw.githubusercontent.com/loteran/Arctis-Sound-Manager/main/scripts/uninstall.sh | bash
set -euo pipefail

# ── Colors ────────────────────────────────────────────────────────────────────
if [ -t 1 ] && command -v tput >/dev/null 2>&1; then
    BOLD=$(tput bold); DIM=$(tput dim); RESET=$(tput sgr0)
    RED=$(tput setaf 1); GREEN=$(tput setaf 2); YELLOW=$(tput setaf 3); BLUE=$(tput setaf 4)
else
    BOLD=""; DIM=""; RESET=""; RED=""; GREEN=""; YELLOW=""; BLUE=""
fi

step() { printf "\n${BOLD}${BLUE}==> %s${RESET}\n" "$*"; }
ok()   { printf "  ${GREEN}[ok]${RESET} %s\n" "$*"; }
warn() { printf "  ${YELLOW}[!] ${RESET}%s\n" "$*"; }
err()  { printf "  ${RED}[ERROR]${RESET} %s\n" "$*" >&2; }
info() { printf "  ${DIM}%s${RESET}\n" "$*"; }

# ── Args ──────────────────────────────────────────────────────────────────────
ASSUME_YES=0
PURGE=0
SELECTED=""   # "pipx", "pkg", "all" or empty (= ask)

while [ $# -gt 0 ]; do
    case "$1" in
        --pipx)  SELECTED="pipx" ;;
        --pkg)   SELECTED="pkg" ;;
        --all)   SELECTED="all" ;;
        --purge) PURGE=1 ;;
        --yes|-y) ASSUME_YES=1 ;;
        -h|--help)
            sed -n '2,18p' "$0" | sed 's/^# *//'
            exit 0 ;;
        *) err "Unknown argument: $1"; exit 2 ;;
    esac
    shift
done

confirm() {
    [ "$ASSUME_YES" -eq 1 ] && return 0
    printf "  %s [y/N]: " "$1"
    read -r ans
    case "$ans" in y|Y|yes|YES) return 0 ;; *) return 1 ;; esac
}

# ── Detect every install method present on this system ──────────────────────
step "Detecting Arctis Sound Manager installations"

declare -a PKG_INSTALLS=()      # rpm | pacman | apt
declare -A PKG_VERSIONS=()
PIPX_VERSION=""

if command -v rpm >/dev/null 2>&1 && rpm -q arctis-sound-manager >/dev/null 2>&1; then
    v=$(rpm -q --qf "%{VERSION}" arctis-sound-manager)
    PKG_INSTALLS+=("rpm")
    PKG_VERSIONS[rpm]="$v"
    info "rpm:    arctis-sound-manager $v"
fi

if command -v pacman >/dev/null 2>&1 && pacman -Q arctis-sound-manager >/dev/null 2>&1; then
    v=$(pacman -Q arctis-sound-manager | awk '{print $2}')
    PKG_INSTALLS+=("pacman")
    PKG_VERSIONS[pacman]="$v"
    info "pacman: arctis-sound-manager $v"
fi

if command -v dpkg >/dev/null 2>&1 && dpkg -s arctis-sound-manager >/dev/null 2>&1; then
    v=$(dpkg-query -W -f='${Version}' arctis-sound-manager 2>/dev/null || echo "?")
    PKG_INSTALLS+=("apt")
    PKG_VERSIONS[apt]="$v"
    info "apt:    arctis-sound-manager $v"
fi

if command -v pipx >/dev/null 2>&1 && pipx list --short 2>/dev/null | grep -q "^arctis-sound-manager"; then
    PIPX_VERSION=$(pipx list --short 2>/dev/null | awk '/^arctis-sound-manager/ {print $2}')
    info "pipx:   arctis-sound-manager $PIPX_VERSION"
fi

# Orphan binaries in PATH (catches manual `pip install --user` etc.)
ORPHAN_BINS=$(command -v -a asm-daemon 2>/dev/null || true)
if [ -n "$ORPHAN_BINS" ]; then
    info "asm-daemon binaries in PATH:"
    while IFS= read -r p; do info "    $p"; done <<<"$ORPHAN_BINS"
fi

HAS_PKG=0
[ "${#PKG_INSTALLS[@]}" -gt 0 ] && HAS_PKG=1
HAS_PIPX=0
[ -n "$PIPX_VERSION" ] && HAS_PIPX=1

if [ "$HAS_PKG" -eq 0 ] && [ "$HAS_PIPX" -eq 0 ]; then
    ok "No Arctis Sound Manager installation detected — nothing to do."
    exit 0
fi

# ── Decide what to remove ────────────────────────────────────────────────────
if [ -z "$SELECTED" ]; then
    step "What do you want to uninstall?"
    if [ "$HAS_PKG" -eq 1 ] && [ "$HAS_PIPX" -eq 1 ]; then
        cat <<EOF
  1) pipx only         (keep the distro package)
  2) distro package(s) only  (${PKG_INSTALLS[*]})
  3) both
  q) cancel

EOF
        printf "  Choice [3]: "
        read -r choice
        case "${choice:-3}" in
            1) SELECTED="pipx" ;;
            2) SELECTED="pkg" ;;
            3) SELECTED="all" ;;
            q|Q) info "Cancelled."; exit 0 ;;
            *) err "Invalid choice"; exit 2 ;;
        esac
    elif [ "$HAS_PIPX" -eq 1 ]; then
        if ! confirm "Remove pipx install ($PIPX_VERSION)?"; then
            info "Cancelled."; exit 0
        fi
        SELECTED="pipx"
    else
        if ! confirm "Remove distro package(s): ${PKG_INSTALLS[*]} ?"; then
            info "Cancelled."; exit 0
        fi
        SELECTED="pkg"
    fi
fi

# Sanity check selection vs what's actually installed
if [ "$SELECTED" = "pipx" ] && [ "$HAS_PIPX" -eq 0 ]; then
    err "--pipx requested but no pipx install detected."
    exit 1
fi
if [ "$SELECTED" = "pkg" ] && [ "$HAS_PKG" -eq 0 ]; then
    err "--pkg requested but no distro package install detected."
    exit 1
fi

# ── Stop user services first (relevant for both branches) ───────────────────
step "Stopping ASM user services"
for svc in arctis-manager.service arctis-gui.service arctis-video-router.service; do
    if systemctl --user list-unit-files "$svc" 2>/dev/null | grep -q "${svc%.service}"; then
        systemctl --user stop "$svc" 2>/dev/null || true
        info "stopped $svc"
    fi
done
# disable so they don't auto-start on next login when the unit file is gone
for svc in arctis-manager.service arctis-gui.service arctis-video-router.service; do
    systemctl --user disable "$svc" 2>/dev/null || true
done
ok "user services stopped"

# ── Uninstall pipx ───────────────────────────────────────────────────────────
if [ "$SELECTED" = "pipx" ] || [ "$SELECTED" = "all" ]; then
    if [ "$HAS_PIPX" -eq 1 ]; then
        step "Removing pipx install"
        if confirm "Run 'pipx uninstall arctis-sound-manager' ?"; then
            pipx uninstall arctis-sound-manager || warn "pipx uninstall failed"
            ok "pipx removed"
        else
            warn "skipped pipx removal"
        fi
    fi
fi

# ── Uninstall distro packages ────────────────────────────────────────────────
if [ "$SELECTED" = "pkg" ] || [ "$SELECTED" = "all" ]; then
    for m in "${PKG_INSTALLS[@]}"; do
        step "Removing distro package ($m)"
        case "$m" in
            rpm)
                if confirm "Run 'sudo dnf remove -y arctis-sound-manager' ?"; then
                    sudo dnf remove -y arctis-sound-manager || warn "dnf remove failed"
                    ok "rpm removed"
                else
                    warn "skipped rpm removal"
                fi
                ;;
            apt)
                if confirm "Run 'sudo apt-get remove -y arctis-sound-manager' ?"; then
                    sudo apt-get remove -y arctis-sound-manager || warn "apt remove failed"
                    ok "apt removed"
                else
                    warn "skipped apt removal"
                fi
                ;;
            pacman)
                if confirm "Run 'sudo pacman -Rns --noconfirm arctis-sound-manager' ?"; then
                    sudo pacman -Rns --noconfirm arctis-sound-manager || warn "pacman remove failed"
                    ok "pacman removed"
                else
                    warn "skipped pacman removal"
                fi
                ;;
        esac
    done
fi

# ── Optional: purge user state ───────────────────────────────────────────────
if [ "$PURGE" -eq 1 ]; then
    step "Purging user configs and PipeWire/udev artefacts"
    info "Audio profiles in ~/.config/arctis_manager/profiles/ and the active"
    info "profile pointer are PRESERVED so a future reinstall picks them back up."
    if confirm "Wipe everything else (settings, PipeWire/HRIR, user systemd units, udev /etc) ?"; then
        # ── Inside ~/.config/arctis_manager: surgical removal that keeps
        #     profiles/ and .active_profile so the user's audio profiles
        #     survive a full uninstall+reinstall cycle.
        ASM_DIR="$HOME/.config/arctis_manager"
        if [ -d "$ASM_DIR" ]; then
            shopt -s dotglob nullglob
            for entry in "$ASM_DIR"/*; do
                base=$(basename "$entry")
                case "$base" in
                    profiles|.active_profile)
                        info "preserved: $entry"
                        continue ;;
                esac
                rm -rf "$entry"
            done
            shopt -u dotglob nullglob
        fi
        rm -f  "$HOME/.config/pipewire/pipewire.conf.d/10-arctis-virtual-sinks.conf"
        rm -f  "$HOME/.config/pipewire/filter-chain.conf.d/sink-virtual-surround-7.1-hesuvi.conf"
        rm -rf "$HOME/.config/pipewire/filter-chain.conf.d"/sonar-*.conf
        rm -rf "$HOME/.local/share/pipewire/hrir_hesuvi"
        rm -f  "$HOME/.config/systemd/user/arctis-"*".service"
        rm -f  "$HOME/.config/systemd/user/filter-chain.service"
        # /etc rules left to the package manager when removing pkg, but the user
        # may also have a manual copy written by `asm-cli udev write-rules`.
        if [ -f /etc/udev/rules.d/91-steelseries-arctis.rules ]; then
            if confirm "Also remove /etc/udev/rules.d/91-steelseries-arctis.rules (sudo) ?"; then
                sudo rm -f /etc/udev/rules.d/91-steelseries-arctis.rules
                sudo udevadm control --reload-rules 2>/dev/null || true
            fi
        fi
        systemctl --user daemon-reload 2>/dev/null || true
        ok "user state purged (profiles preserved)"
    else
        warn "skipped purge"
    fi

    # If the user explicitly wants a clean slate including profiles, give them
    # a separate path — never mix with the default --purge.
    if confirm "Also delete saved audio profiles and the active-profile pointer ?"; then
        rm -rf "$HOME/.config/arctis_manager/profiles"
        rm -f  "$HOME/.config/arctis_manager/.active_profile"
        ok "profiles deleted"
    fi
fi

# ── Final report ────────────────────────────────────────────────────────────
step "Done"
REMAINING=""
command -v -a asm-daemon 2>/dev/null | while IFS= read -r p; do
    [ -n "$p" ] && info "still in PATH: $p"
done
ok "Uninstall finished. To check what's left: which -a asm-daemon"
