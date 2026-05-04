#!/usr/bin/env bash
# Arctis Sound Manager — clean reinstall
#
# Detects every existing install (pipx + system packages). If more than one
# is found, asks which one(s) to remove BEFORE reinstalling. Single installs
# are upgraded in place.
#
# Run interactively:
#   curl -fsSL https://raw.githubusercontent.com/loteran/Arctis-Sound-Manager/main/scripts/clean-reinstall.sh | bash
#
# Run with a specific method (no prompts):
#   bash clean-reinstall.sh --method pipx
#   bash clean-reinstall.sh --method dnf
#   bash clean-reinstall.sh --method apt
#   bash clean-reinstall.sh --method pacman
#
# Skip confirmations (useful for scripts):
#   bash clean-reinstall.sh --method pipx --yes
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
METHOD=""
ASSUME_YES=0
while [ $# -gt 0 ]; do
    case "$1" in
        --method) METHOD="$2"; shift 2 ;;
        --yes|-y) ASSUME_YES=1; shift ;;
        --help|-h)
            sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) err "Unknown argument: $1"; exit 2 ;;
    esac
done

# All interactive reads go through /dev/tty so the script works with curl|bash
_read() { read -r "$@" </dev/tty; }

confirm() {
    [ "$ASSUME_YES" -eq 1 ] && return 0
    local prompt="$1" ans
    printf "%s [y/N] " "$prompt"
    _read ans
    [[ "$ans" =~ ^[Yy]$ ]]
}

# ── Removal helper ────────────────────────────────────────────────────────────
_remove_one() {
    local m="$1"
    case "$m" in
        rpm)
            info "running: sudo dnf remove -y arctis-sound-manager"
            sudo dnf remove -y arctis-sound-manager || warn "dnf remove failed"
            ;;
        pacman)
            info "running: sudo pacman -Rns --noconfirm arctis-sound-manager"
            sudo pacman -Rns --noconfirm arctis-sound-manager || warn "pacman remove failed"
            ;;
        apt)
            info "running: sudo apt-get remove -y arctis-sound-manager"
            sudo apt-get remove -y arctis-sound-manager || warn "apt-get remove failed"
            ;;
        pipx)
            info "running: pipx uninstall arctis-sound-manager"
            pipx uninstall arctis-sound-manager || warn "pipx uninstall failed"
            ;;
    esac
}

# ── Detection ─────────────────────────────────────────────────────────────────
step "Detecting current ASM installations"

declare -a INSTALLED_METHODS=()
declare -A INSTALLED_VERSIONS=()

if command -v rpm >/dev/null 2>&1 && rpm -q arctis-sound-manager >/dev/null 2>&1; then
    v=$(rpm -q --qf "%{VERSION}" arctis-sound-manager)
    INSTALLED_METHODS+=("rpm")
    INSTALLED_VERSIONS[rpm]="$v"
    info "rpm:    arctis-sound-manager $v"
fi

if command -v pacman >/dev/null 2>&1 && pacman -Q arctis-sound-manager >/dev/null 2>&1; then
    v=$(pacman -Q arctis-sound-manager | awk '{print $2}')
    INSTALLED_METHODS+=("pacman")
    INSTALLED_VERSIONS[pacman]="$v"
    info "pacman: arctis-sound-manager $v"
fi

if command -v dpkg >/dev/null 2>&1 && dpkg -s arctis-sound-manager >/dev/null 2>&1; then
    v=$(dpkg-query -W -f='${Version}' arctis-sound-manager 2>/dev/null || echo "?")
    INSTALLED_METHODS+=("apt")
    INSTALLED_VERSIONS[apt]="$v"
    info "apt:    arctis-sound-manager $v"
fi

if command -v pipx >/dev/null 2>&1 && pipx list --short 2>/dev/null | grep -q "^arctis-sound-manager"; then
    v=$(pipx list --short 2>/dev/null | awk '/^arctis-sound-manager/ {print $2}')
    INSTALLED_METHODS+=("pipx")
    INSTALLED_VERSIONS[pipx]="$v"
    info "pipx:   arctis-sound-manager $v"
fi

ORPHAN_BINS=$(command -v -a asm-daemon 2>/dev/null || true)
if [ -n "$ORPHAN_BINS" ]; then
    info "asm-daemon binaries in PATH:"
    while IFS= read -r p; do info "    $p"; done <<<"$ORPHAN_BINS"
fi

# ── Handle duplicate installs ─────────────────────────────────────────────────
# When multiple installs coexist, ask which one(s) to remove FIRST, before
# touching the "which method to use going forward" question.
if [ "${#INSTALLED_METHODS[@]}" -gt 1 ]; then
    warn "${#INSTALLED_METHODS[@]} install methods active simultaneously — this is the cause of the bug."
    printf "\n  What do you want to do?\n\n"

    declare -a KEEP_OPTIONS=()
    local_i=1
    for m in "${INSTALLED_METHODS[@]}"; do
        others=""
        for o in "${INSTALLED_METHODS[@]}"; do
            [ "$o" = "$m" ] && continue
            others="${others:+$others + }$o"
        done
        printf "  %d) Remove %s  (keep %s %s)\n" \
            "$local_i" "$others" "$m" "${INSTALLED_VERSIONS[$m]}"
        KEEP_OPTIONS+=("$m")
        local_i=$((local_i + 1))
    done
    printf "  a) Remove all, then reinstall clean\n"
    printf "  q) Cancel\n\n"
    if [ "$ASSUME_YES" -eq 1 ]; then
        dup_choice="a"
        info "Non-interactive (--yes): removing all, then reinstalling"
    else
        printf "  Choice [a]: "
        _read dup_choice || dup_choice=""
        dup_choice="${dup_choice:-a}"
    fi

    case "$dup_choice" in
        q)
            info "Cancelled."
            exit 0
            ;;
        a)
            # Fall through — remove all below, then reinstall
            ;;
        [0-9]*)
            if [ "$dup_choice" -ge 1 ] && [ "$dup_choice" -le "${#KEEP_OPTIONS[@]}" ] 2>/dev/null; then
                KEEP_METHOD="${KEEP_OPTIONS[$((dup_choice - 1))]}"
                step "Stopping ASM services"
                for svc in arctis-manager.service arctis-gui.service arctis-video-router.service filter-chain.service; do
                    systemctl --user stop "$svc" 2>/dev/null || true
                done
                ok "services stopped"
                step "Removing duplicate installs (keeping $KEEP_METHOD ${INSTALLED_VERSIONS[$KEEP_METHOD]})"
                for m in "${INSTALLED_METHODS[@]}"; do
                    [ "$m" = "$KEEP_METHOD" ] && continue
                    _remove_one "$m"
                done
                ok "Duplicates removed — keeping ${KEEP_METHOD} ${INSTALLED_VERSIONS[$KEEP_METHOD]}."
                step "Restarting services"
                systemctl --user restart arctis-manager.service arctis-gui.service 2>/dev/null || true
                ok "Done. No reinstall needed."
                exit 0
            else
                err "Invalid choice '$dup_choice'"; exit 2
            fi
            ;;
        *)
            err "Invalid choice '$dup_choice'"; exit 2
            ;;
    esac
fi

# ── Method selection ─────────────────────────────────────────────────────────
step "Choose the install method to use going forward"

DEFAULT_METHOD="pipx"
if [ -f /etc/os-release ]; then
    . /etc/os-release
    case "${ID,,} ${ID_LIKE:-,,}" in
        *fedora*|*rhel*|*nobara*) DEFAULT_METHOD="dnf" ;;
        *debian*|*ubuntu*|*mint*) DEFAULT_METHOD="apt" ;;
        *arch*|*cachyos*|*manjaro*|*endeavour*|*garuda*) DEFAULT_METHOD="pacman" ;;
    esac
fi

if [ -z "$METHOD" ]; then
    if [ "$ASSUME_YES" -eq 1 ]; then
        METHOD="$DEFAULT_METHOD"
        info "Non-interactive: using auto-detected method '${METHOD}'"
    else
        cat <<EOF
  1) pipx     (recommended — no sudo for self-update, cross-distro)
  2) dnf      (Fedora / Nobara / RHEL — system-wide via COPR)
  3) apt      (Debian / Ubuntu / Mint — system-wide via PPA)
  4) pacman   (Arch / CachyOS / Manjaro — system-wide via AUR)

EOF
        printf "  Choice [default: %s]: " "$DEFAULT_METHOD"
        _read choice || choice=""
        case "${choice:-$DEFAULT_METHOD}" in
            1|pipx)   METHOD="pipx" ;;
            2|dnf)    METHOD="dnf" ;;
            3|apt)    METHOD="apt" ;;
            4|pacman) METHOD="pacman" ;;
            *) err "Invalid choice '${choice}'"; exit 2 ;;
        esac
    fi
fi
ok "Will install using: ${BOLD}${METHOD}${RESET}"

# ── Stop services ────────────────────────────────────────────────────────────
step "Stopping ASM services"
for svc in arctis-manager.service arctis-gui.service arctis-video-router.service filter-chain.service; do
    if systemctl --user list-unit-files "$svc" 2>/dev/null | grep -q "${svc%.service}"; then
        systemctl --user stop "$svc" 2>/dev/null || true
        info "stopped $svc"
    fi
done
ok "services stopped"

# ── Uninstall all detected installs ──────────────────────────────────────────
if [ "${#INSTALLED_METHODS[@]}" -gt 0 ]; then
    step "Removing existing installations"
    if confirm "  Proceed with removal of: ${INSTALLED_METHODS[*]} ?"; then
        for m in "${INSTALLED_METHODS[@]}"; do
            _remove_one "$m"
        done
        ok "removal complete"
    else
        warn "Skipped removal — proceeding with install on top of existing copies (not recommended)"
    fi
fi

# ── Reset setup flag so first-run dialog re-runs asm-setup ──────────────────
rm -f "$HOME/.config/arctis_manager/.setup_done"
info "Cleared ~/.config/arctis_manager/.setup_done — first-run setup will re-trigger"

# ── Install latest via chosen method ─────────────────────────────────────────
step "Installing latest release via ${METHOD}"
case "$METHOD" in
    pipx)
        if ! command -v pipx >/dev/null 2>&1; then
            err "pipx not found. Install it first (e.g. 'sudo dnf install pipx' or 'sudo apt install pipx')."
            exit 1
        fi
        LATEST_TAG=$(curl -fsSL https://api.github.com/repos/loteran/Arctis-Sound-Manager/releases/latest \
                     | grep '"tag_name"' | head -n1 | cut -d'"' -f4)
        [ -n "$LATEST_TAG" ] || { err "Could not query latest release tag"; exit 1; }
        VER="${LATEST_TAG#v}"
        WHL_URL="https://github.com/loteran/Arctis-Sound-Manager/releases/download/${LATEST_TAG}/arctis_sound_manager-${VER}-py3-none-any.whl"
        info "Installing $WHL_URL"
        pipx install "arctis-sound-manager @ ${WHL_URL}" --force
        ;;
    dnf)
        if ! command -v dnf >/dev/null 2>&1; then err "dnf not found"; exit 1; fi
        sudo dnf copr enable -y loteran/arctis-sound-manager 2>/dev/null || true
        sudo dnf install -y arctis-sound-manager
        ;;
    apt)
        if ! command -v apt-get >/dev/null 2>&1; then err "apt not found"; exit 1; fi
        warn "APT install requires the upstream PPA. See README for setup if not configured."
        sudo apt-get update
        sudo apt-get install -y arctis-sound-manager
        ;;
    pacman)
        if command -v paru >/dev/null 2>&1; then
            paru -S --noconfirm arctis-sound-manager
        elif command -v yay >/dev/null 2>&1; then
            yay -S --noconfirm arctis-sound-manager
        else
            err "Need an AUR helper (paru or yay) for pacman install. Install one first."
            exit 1
        fi
        ;;
    *)
        err "Unsupported method: $METHOD"; exit 2 ;;
esac
ok "package installed via ${METHOD}"

# ── Run asm-setup ────────────────────────────────────────────────────────────
step "Running asm-setup (PipeWire, udev rules, services)"
export PATH="$HOME/.local/bin:$PATH"
if ! command -v asm-setup >/dev/null 2>&1; then
    err "asm-setup not in PATH. Open a new terminal and run 'asm-setup' manually."
    exit 1
fi
asm-setup

# ── Verify ───────────────────────────────────────────────────────────────────
step "Verifying installation"
INSTALLED_VERSION=$(asm-cli --version 2>/dev/null || echo "?")
info "asm-cli reports: $INSTALLED_VERSION"
if systemctl --user is-active --quiet arctis-manager.service; then
    ok "arctis-manager.service is active"
else
    warn "arctis-manager.service is not active. Run: systemctl --user restart arctis-manager.service"
fi

NEW_BINS=$(command -v -a asm-daemon 2>/dev/null | sort -u || true)
if [ "$(echo "$NEW_BINS" | wc -l)" -gt 1 ]; then
    warn "Multiple asm-daemon binaries still in PATH:"
    echo "$NEW_BINS" | sed 's/^/      /'
    warn "Re-run this script after removing the unwanted copies, or adjust PATH."
fi

step "Done!"
cat <<EOF

Open the GUI:  ${BOLD}asm-gui${RESET}
or find "Arctis Sound Manager" in your application menu.

If your headset is currently plugged in and was not picked up immediately,
unplug and replug the dongle once — udev permissions only apply on (re)connect.
EOF
