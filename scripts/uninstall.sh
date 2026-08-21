#!/usr/bin/env bash
# Arctis Sound Manager — uninstaller
#
# Detects every existing install (distro package, pipx, distrobox, and a
# `pip install --user` copy in ~/.local) and lets the user pick which one(s)
# to remove. Designed for the common case "I want to drop
# the system package and switch to pipx" (or vice-versa) without nuking the
# install I want to keep.
#
# Usage:
#   bash scripts/uninstall.sh                # interactive
#   bash scripts/uninstall.sh --all          # remove every detected install
#   bash scripts/uninstall.sh --pipx         # remove only the pipx install
#   bash scripts/uninstall.sh --pkg          # remove only the distro package
#   bash scripts/uninstall.sh --pip-user     # remove only the ~/.local pip copy
#   bash scripts/uninstall.sh --purge        # also wipe ~/.config/arctis_manager,
#                                            # PipeWire configs and udev rules
#   bash scripts/uninstall.sh --yes          # skip confirmations
#
# Run remote (no clone needed):
#   curl -fsSL https://raw.githubusercontent.com/loteran/Arctis-Sound-Manager/main/scripts/uninstall.sh | bash
set -euo pipefail

# `curl … | bash` leaves BASH_SOURCE unset: bash reads the script from stdin,
# so there is no file to take a directory from. Under `set -u` that alone
# aborts the script, and since bash 5.3 the `cd ""` that follows fails too,
# which is how the documented one-liner died on this very line, before
# printing anything, on every distro shipping a current bash.
# An empty SCRIPT_DIR is the honest answer: every use of it below already
# checks that the file it wants is actually there, and falls back to curl.
_self="${BASH_SOURCE[0]:-}"
if [ -n "$_self" ] && [ -f "$_self" ]; then
    SCRIPT_DIR="$(cd "$(dirname "$_self")" && pwd)"
else
    SCRIPT_DIR=""   # piped from curl, nothing local to delegate to
fi

#: Where this script lives when run remotely, used by the messages that tell
#: the user how to re-run it, and by the Distrobox delegation below.
RAW_BASE="https://raw.githubusercontent.com/loteran/Arctis-Sound-Manager/main/scripts"
SELF_URL="$RAW_BASE/uninstall.sh"

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
        --pip-user) SELECTED="pip-user" ;;
        --pkg)   SELECTED="pkg" ;;
        --all)   SELECTED="all" ;;
        --purge) PURGE=1 ;;
        --yes|-y) ASSUME_YES=1 ;;
        -h|--help)
            # Printed from the header above, stopping at the first line that
            # is no longer a comment. A fixed line range silently truncated
            # the usage block every time a line was added to it.
            #
            # $0 is "bash" when the script is piped in, and `sed` then reads
            # nothing at all, so `--help` printed an error instead of the
            # usage. Fetch the header from the same URL the user just curled.
            if [ -n "$_self" ] && [ -f "$_self" ]; then
                sed -n '2,${/^#/!q; s/^# \?//; p}' "$_self"
            else
                curl -fsSL "$SELF_URL" | sed -n '2,${/^#/!q; s/^# \?//; p}'
            fi
            exit 0 ;;
        *) err "Unknown argument: $1"; exit 2 ;;
    esac
    shift
done

# Where to read answers from. When the script itself arrives on stdin
# (`curl … | bash`), stdin is the pipe rather than the keyboard: `read` gets
# EOF instantly, every confirmation answers "no", and the uninstaller removes
# nothing while looking like it ran to completion. The terminal is still
# reachable as /dev/tty, so ask there.
#
# The test has to be an actual open. `[ -r /dev/tty ]` is true even with no
# controlling terminal: the device node is there and readable, and the open
# then fails with ENXIO, which left the answer variable unset and the script
# dying on `unbound variable` instead of saying what was wrong.
if [ -t 0 ]; then
    ANSWER_FROM="/dev/stdin"  # stdin is already the terminal
elif { exec 3</dev/tty; } 2>/dev/null; then
    exec 3<&-                 # only probing, the reads reopen it themselves
    ANSWER_FROM="/dev/tty"    # piped script, real terminal behind it
else
    ANSWER_FROM="none"        # no terminal at all (CI, `ssh -T`, a cron job)
fi

#: Every prompt goes through here, so there is exactly one place that can get
#: this wrong again.
ask() {
    # Refuse rather than assume: silently answering "no" is what made the piped
    # run look successful while leaving both installs in place.
    if [ "$ANSWER_FROM" = "none" ]; then
        echo >&2
        err "No terminal to answer on, and --yes was not given: refusing to guess."
        err "Re-run with --yes to confirm the removals up front:"
        err "  curl -fsSL $SELF_URL | bash -s -- --all --purge --yes"
        exit 3
    fi
    read -r "$1" < "$ANSWER_FROM"
}

confirm() {
    [ "$ASSUME_YES" -eq 1 ] && return 0
    printf "  %s [y/N]: " "$1"
    ask ans
    case "$ans" in y|Y|yes|YES) return 0 ;; *) return 1 ;; esac
}

# ── Detect every install method present on this system ──────────────────────
step "Detecting Arctis Sound Manager installations"

declare -a PKG_INSTALLS=()      # rpm | pacman | apt
declare -A PKG_VERSIONS=()
PIPX_VERSION=""

# Both names are checked: Terra (enabled by default on Nobara and Ultramarine)
# ships this project's source as python3-arctis-sound-manager. Looking only for
# our own name meant the uninstaller reported "nothing to do" on the very
# distros where people most often need it (discussion #190).
declare -a RPM_PKGS=()
if command -v rpm >/dev/null 2>&1; then
    for rpm_name in arctis-sound-manager python3-arctis-sound-manager; do
        if rpm -q "$rpm_name" >/dev/null 2>&1; then
            v=$(rpm -q --qf "%{VERSION}" "$rpm_name")
            RPM_PKGS+=("$rpm_name")
            PKG_VERSIONS[rpm]="$v"
            info "rpm:    $rpm_name $v"
        fi
    done
    [ "${#RPM_PKGS[@]}" -gt 0 ] && PKG_INSTALLS+=("rpm")
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

# Distrobox install (Bazzite, SteamOS, Silverblue…)
HAS_DISTROBOX=0
if command -v distrobox >/dev/null 2>&1 && distrobox list 2>/dev/null | grep -qw "arctis-sound-manager"; then
    HAS_DISTROBOX=1
    info "distrobox: container 'arctis-sound-manager' found"
elif [ -f "$HOME/.local/bin/asm-daemon" ] && grep -q "distrobox" "$HOME/.local/bin/asm-daemon" 2>/dev/null; then
    HAS_DISTROBOX=1
    info "distrobox: stubs detected in ~/.local/bin (container may be missing)"
fi

# A `pip install --user` copy. This one is treated as a real install rather
# than as information, because it behaves like one: it sits in ~/.local, it
# SHADOWS the distro package (Python finds user site-packages first), and it
# keeps starting at boot. Someone who removed the distro package and still has
# ASM running after a reboot has this and nothing else — and the script used to
# answer "nothing to do" three lines after printing the offending binary
# (discussion #190).
declare -a PIP_USER_DIRS=()
while IFS= read -r d; do
    [ -n "$d" ] && PIP_USER_DIRS+=("$d")
done < <(find "$HOME/.local/lib" -maxdepth 3 -type d -name "arctis_sound_manager" \
             -path "*/site-packages/*" 2>/dev/null || true)

HAS_PIP_USER=0
if [ "${#PIP_USER_DIRS[@]}" -gt 0 ]; then
    HAS_PIP_USER=1
    for d in "${PIP_USER_DIRS[@]}"; do info "pip --user: $d"; done
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

if [ "$HAS_PKG" -eq 0 ] && [ "$HAS_PIPX" -eq 0 ] && [ "$HAS_DISTROBOX" -eq 0 ] \
   && [ "$HAS_PIP_USER" -eq 0 ]; then
    ok "No Arctis Sound Manager installation detected — nothing to do."
    exit 0
fi

# Distrobox install: delegate to the dedicated uninstaller
if [ "$HAS_DISTROBOX" -eq 1 ] && [ "$HAS_PKG" -eq 0 ] && [ "$HAS_PIPX" -eq 0 ] \
   && [ "$HAS_PIP_USER" -eq 0 ]; then
    warn "Distrobox install detected. The regular uninstaller cannot remove it."
    DISTROBOX_UNINSTALL="$SCRIPT_DIR/distrobox/uninstall.sh"
    if [ -f "$DISTROBOX_UNINSTALL" ]; then
        info "Delegating to: $DISTROBOX_UNINSTALL"
        exec bash "$DISTROBOX_UNINSTALL" "$@"
    else
        # Running from curl pipe — BASH_SOURCE[0] is stdin, SCRIPT_DIR is not the repo.
        # Download both scripts to a temp dir and run from there.
        warn "Running from curl — fetching Distrobox uninstaller..."
        _tmp=$(mktemp -d)
        trap 'rm -rf "$_tmp"' EXIT
        _base="$RAW_BASE"
        curl -fsSL "$_base/distrobox/_common.sh"  -o "$_tmp/_common.sh"  || { err "Failed to fetch _common.sh";  exit 1; }
        curl -fsSL "$_base/distrobox/uninstall.sh" -o "$_tmp/uninstall.sh" || { err "Failed to fetch uninstall.sh"; exit 1; }
        exec bash "$_tmp/uninstall.sh" "$@"
    fi
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
        ask choice
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
    elif [ "$HAS_PKG" -eq 1 ]; then
        if ! confirm "Remove distro package(s): ${PKG_INSTALLS[*]} ?"; then
            info "Cancelled."; exit 0
        fi
        SELECTED="pkg"
    else
        # Only a pip --user copy is left. Nothing to choose between, but it
        # still has to be offered — this is the case that used to exit early.
        if ! confirm "Remove the pip --user copy in ~/.local ?"; then
            info "Cancelled."; exit 0
        fi
        SELECTED="all"
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
if [ "$SELECTED" = "pip-user" ] && [ "$HAS_PIP_USER" -eq 0 ]; then
    err "--pip-user requested but no pip --user copy detected."
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
            if pipx uninstall arctis-sound-manager; then
                ok "pipx removed"
            else
                err "pipx could not remove it — it is still installed"
            fi
        else
            warn "skipped pipx removal"
        fi
    fi
fi

# ── Remove a pip --user copy ─────────────────────────────────────────────────
# This is the one that makes ASM look uninstallable: it shadows the distro
# package, survives `dnf remove`, and keeps starting at boot. pip is asked
# first so its dist-info goes too; whatever it leaves (or if pip is not there
# at all) is removed by hand.
if [ "$SELECTED" = "pip-user" ] || [ "$SELECTED" = "all" ]; then
    if [ "$HAS_PIP_USER" -eq 1 ]; then
        step "Removing pip --user copy"
        if confirm "Remove arctis-sound-manager from ~/.local ?"; then
            if command -v python3 >/dev/null 2>&1; then
                python3 -m pip uninstall -y arctis-sound-manager >/dev/null 2>&1 \
                    || true   # not pip-installed, or no pip: the rm below is the real work
            fi
            for d in "${PIP_USER_DIRS[@]}"; do
                rm -rf "$d"
                info "removed $d"
                # The dist-info sits beside the package and keeps the copy
                # "installed" as far as pip and ASM's own multi-install
                # detection are concerned.
                rm -rf "$(dirname "$d")"/arctis_sound_manager-*.dist-info
                rm -rf "$(dirname "$d")"/arctis_sound_manager-*.egg-info
            done
            # The console scripts. Distrobox stubs share these names, so they
            # are left alone when a container is in play — removing them there
            # would break an install this script is not uninstalling.
            if [ "$HAS_DISTROBOX" -eq 0 ]; then
                for b in asm-daemon asm-cli asm-gui asm-router asm-stream-guard \
                         asm-clipd asm-setup asm-diag-dinit; do
                    if [ -e "$HOME/.local/bin/$b" ]; then
                        rm -f "$HOME/.local/bin/$b"
                        info "removed ~/.local/bin/$b"
                    fi
                done
            else
                warn "~/.local/bin left alone — a Distrobox install shares those names"
            fi
            ok "pip --user copy removed"
        else
            warn "skipped pip --user removal"
        fi
    fi
fi

# ── Uninstall distro packages ────────────────────────────────────────────────
if [ "$SELECTED" = "pkg" ] || [ "$SELECTED" = "all" ]; then
    for m in "${PKG_INSTALLS[@]}"; do
        step "Removing distro package ($m)"
        case "$m" in
            rpm)
                for rpm_name in "${RPM_PKGS[@]}"; do
                    if confirm "Run 'sudo dnf remove -y $rpm_name' ?"; then
                        if sudo dnf remove -y "$rpm_name"; then
                            ok "$rpm_name removed"
                        else
                            err "dnf could not remove $rpm_name — it is still installed"
                        fi
                    else
                        warn "skipped $rpm_name removal"
                    fi
                done
                ;;
            apt)
                if confirm "Run 'sudo apt-get remove -y arctis-sound-manager' ?"; then
                    if sudo apt-get remove -y arctis-sound-manager; then
                        ok "apt removed"
                    else
                        err "apt could not remove the package — it is still installed"
                    fi
                else
                    warn "skipped apt removal"
                fi
                ;;
            pacman)
                if confirm "Run 'sudo pacman -Rns --noconfirm arctis-sound-manager' ?"; then
                    if sudo pacman -Rns --noconfirm arctis-sound-manager; then
                        ok "pacman removed"
                    else
                        err "pacman could not remove the package — it is still installed"
                    fi
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
