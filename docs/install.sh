#!/usr/bin/env bash
# Arctis Sound Manager — universal installer
#
# One command, any distribution:
#   curl -fsSL https://loteran.github.io/Arctis-Sound-Manager/install.sh | bash
#
# It detects your distribution and installs ASM from the matching native
# package (AUR / COPR / PPA), or inside a Distrobox container on immutable
# distros (Bazzite, Silverblue, SteamOS). It then runs `asm-setup`, which
# configures the udev rules, PipeWire and the systemd services.
#
# Prefer to read it first? Download, inspect, then run:
#   curl -fsSL https://loteran.github.io/Arctis-Sound-Manager/install.sh -o asm-install.sh
#   less asm-install.sh
#   bash asm-install.sh
#
# Flags:
#   --dry-run   print every command without running it
#   --yes       skip the confirmation prompt
#   --force     reinstall/continue even if ASM is already present
#   --help
#
# Copyright (C) 2026 loteran — SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail

REPO_URL="https://github.com/loteran/Arctis-Sound-Manager"
COPR="loteran/arctis-sound-manager"
PPA="ppa:loteran/arctis-sound-manager"
PKG="arctis-sound-manager"

DRY_RUN=0
ASSUME_YES=0
FORCE=0

for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        --yes|-y)  ASSUME_YES=1 ;;
        --force)   FORCE=1 ;;
        -h|--help)
            awk 'NR>1 && /^#/ {sub(/^# ?/,""); print; next} NR>1 {exit}' "$0"
            exit 0 ;;
        *) printf 'Unknown option: %s (try --help)\n' "$arg" >&2; exit 2 ;;
    esac
done

# ── logging ────────────────────────────────────────────────────────────────
say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n'  "$*" >&2; }
die()  { printf '\033[1;31m[x]\033[0m %s\n'  "$*" >&2; exit 1; }

# Echo a command, then run it (or just echo it in --dry-run mode).
run() {
    printf '    \033[2m$ %s\033[0m\n' "$*"
    [ "$DRY_RUN" -eq 1 ] && return 0
    "$@"
}

confirm() {
    [ "$ASSUME_YES" -eq 1 ] && return 0
    [ "$DRY_RUN" -eq 1 ]    && return 0
    [ -r /dev/tty ] || return 0   # non-interactive (piped, no tty): proceed
    printf '    Proceed? [Y/n] '
    local ans=""
    read -r ans < /dev/tty || ans=""
    case "$ans" in [nN]*) die "Aborted." ;; esac
}

# ── distro detection ─────────────────────────────────────────────────────────
# Prints one of: bazzite silverblue steamos arch fedora debian unknown
detect() {
    if [ -f /etc/steamos-release ] || command -v steamos-readonly >/dev/null 2>&1; then
        echo steamos; return
    fi
    local id like name
    id="$(   [ -f /etc/os-release ] && . /etc/os-release && echo "${ID:-}")"
    like="$( [ -f /etc/os-release ] && . /etc/os-release && echo "${ID_LIKE:-}")"
    name="$( [ -f /etc/os-release ] && . /etc/os-release && \
             echo "${ID:-}${VARIANT_ID:-}${NAME:-}" | tr '[:upper:]' '[:lower:]')"

    case "$name" in
        *bazzite*)                            echo bazzite;    return ;;
        *silverblue*|*kinoite*|*sericea*|*onyx*) echo silverblue; return ;;
    esac
    case " $id $like " in
        *" arch "*|*" archlinux "*|*cachyos*|*manjaro*|*endeavouros*|*artix*|*garuda*)
            echo arch;   return ;;
        *" fedora "*|*nobara*|*" rhel "*|*centos*)
            echo fedora; return ;;
        *" debian "*|*" ubuntu "*|*linuxmint*|*pop*|*elementary*|*zorin*|*neon*)
            echo debian; return ;;
    esac
    echo unknown
}

# ── install paths ─────────────────────────────────────────────────────────────
aur_install() {
    [ "$(id -u)" -eq 0 ] && die "Don't run this as root on Arch — AUR helpers refuse to run as root. Re-run as your normal user."
    local helper=""
    for h in paru yay; do command -v "$h" >/dev/null 2>&1 && { helper="$h"; break; }; done
    if [ -z "$helper" ]; then
        warn "No AUR helper (paru or yay) found. Install one first:"
        printf '      sudo pacman -S --needed git base-devel\n'
        printf '      git clone https://aur.archlinux.org/paru-bin.git\n'
        printf '      cd paru-bin && makepkg -si\n'
        die "Then re-run this installer."
    fi
    run "$helper" -S --needed "$PKG"
}

copr_install() {
    command -v dnf >/dev/null 2>&1 || die "dnf not found — is this really a Fedora-family system?"
    run sudo dnf copr enable -y "$COPR"
    run sudo dnf install -y "$PKG"
}

ppa_install() {
    command -v apt >/dev/null 2>&1 || die "apt not found — is this really a Debian/Ubuntu system?"
    if ! command -v add-apt-repository >/dev/null 2>&1; then
        run sudo apt-get update
        run sudo apt-get install -y software-properties-common
    fi
    run sudo add-apt-repository -y "$PPA"
    run sudo apt-get update
    run sudo apt-get install -y "$PKG"
}

immutable_install() {
    local flavor="$1"
    say "This is an immutable distro ($flavor) — ASM installs inside a Distrobox container."
    command -v git >/dev/null 2>&1 || die "git is required for this path. Install it, then re-run."
    local tmp; tmp="$(mktemp -d)"
    run git clone --depth=1 "$REPO_URL" "$tmp/asm"
    run bash "$tmp/asm/scripts/distrobox-install.sh"
    # distrobox-install.sh runs its own setup; nothing more to do here.
}

post_setup() {
    if command -v asm-setup >/dev/null 2>&1; then
        say "Configuring ASM (udev rules, PipeWire, systemd services)…"
        run asm-setup
    else
        warn "asm-setup is not on PATH yet — open a new terminal and run:  asm-setup"
    fi
}

# ── main ─────────────────────────────────────────────────────────────────────
say "Arctis Sound Manager — installer"
flavor="$(detect)"
say "Detected distribution: $flavor"

# Already installed? Don't blindly reinstall — the ≤1.2.0 → 1.2.1 upgrade has a
# one-time file conflict that needs remove-then-reinstall (see the README).
if [ "$FORCE" -eq 0 ] && [ "$DRY_RUN" -eq 0 ] && command -v asm-setup >/dev/null 2>&1; then
    warn "Arctis Sound Manager already looks installed on this system."
    printf '    To upgrade, use your package manager — see %s#upgrading\n' "$REPO_URL"
    printf '    Re-running setup to make sure udev/PipeWire/services are in place…\n'
    post_setup
    say "Done. Launch 'asm-gui' or find 'Arctis Sound Manager' in your app menu."
    exit 0
fi

case "$flavor" in
    bazzite|silverblue|steamos) say "Plan: install inside a Distrobox container." ;;
    arch)   say "Plan: install '$PKG' from the AUR." ;;
    fedora) say "Plan: enable COPR '$COPR', then install '$PKG'." ;;
    debian) say "Plan: add PPA '$PPA', then install '$PKG'." ;;
    unknown) die "Couldn't recognise this distribution. Install manually — see $REPO_URL#installation" ;;
esac

confirm

case "$flavor" in
    bazzite|silverblue|steamos) immutable_install "$flavor" ;;
    arch)   aur_install;  post_setup ;;
    fedora) copr_install; post_setup ;;
    debian) ppa_install;  post_setup ;;
esac

say "Done. Launch 'asm-gui' or find 'Arctis Sound Manager' in your application menu."
