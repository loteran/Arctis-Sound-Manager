#!/usr/bin/env bash
# Arctis Sound Manager — Distrobox installer dispatcher for immutable distros
# Detects the host distro and delegates to the appropriate per-distro script.
#
# Usage:
#   bash scripts/distrobox-install.sh [options]
#
# Options:
#   --reinstall          Remove and recreate the container, then reinstall
#   --uninstall          Fully uninstall (services, exports, container)
#   --no-services        Skip enabling systemd services after install
#   --base arch|fedora   Deprecated — ignored (image now selected per distro)
#   -h, --help           Show this help message

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DISTROBOX_DIR="$SCRIPT_DIR/distrobox"

usage() {
    cat <<'EOF'
Arctis Sound Manager — Distrobox installer for immutable distros

Usage:
  bash scripts/distrobox-install.sh [options]

Options:
  --reinstall    Remove and recreate the container, then reinstall
  --uninstall    Fully uninstall ASM (services, exports, container)
  --no-services  Skip enabling systemd services after install
  -h, --help     Show this help message

Supported hosts (auto-detected):
  Bazzite                  → distrobox/bazzite.sh    (Arch container)
  Fedora Silverblue/Kinoite → distrobox/silverblue.sh (Fedora 41 container)
  SteamOS / Steam Deck     → distrobox/steamos.sh    (Arch container)

Log: ~/.cache/asm-distrobox-install.log
EOF
}

# ---------------------------------------------------------------------------
# detect_distro
# Outputs one of: bazzite | silverblue | steamos | unknown
# ---------------------------------------------------------------------------
detect_distro() {
    if [[ -f /etc/steamos-release ]] || command -v steamos-readonly &>/dev/null; then
        echo "steamos"
        return
    fi

    if [[ -f /etc/os-release ]]; then
        local id_like name
        # shellcheck disable=SC1091
        id_like="$(. /etc/os-release && echo "${ID_LIKE:-}")"
        name="$(. /etc/os-release && echo "${ID:-}${VARIANT_ID:-}${NAME:-}" | tr '[:upper:]' '[:lower:]')"

        if echo "$name" | grep -q "bazzite"; then
            echo "bazzite"
            return
        fi
        # ublue-os (Universal Blue) derivatives other than Bazzite → treat as bazzite
        if echo "$id_like $name" | grep -qiE 'ublue|bazzite'; then
            echo "bazzite"
            return
        fi
        if echo "$name" | grep -qiE 'silverblue|kinoite'; then
            echo "silverblue"
            return
        fi
    fi

    echo "unknown"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

# Forward args — strip deprecated --base and collect the rest
FORWARD_ARGS=()
DO_UNINSTALL=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --base)
            shift
            echo "Warning: --base is deprecated and ignored; the image is now selected per distro." >&2
            ;;
        --uninstall)
            DO_UNINSTALL=1
            FORWARD_ARGS+=("$1")
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            FORWARD_ARGS+=("$1")
            ;;
    esac
    shift
done

if [[ $DO_UNINSTALL -eq 1 ]]; then
    exec bash "$DISTROBOX_DIR/uninstall.sh" "${FORWARD_ARGS[@]+"${FORWARD_ARGS[@]}"}"
fi

DISTRO="$(detect_distro)"

case "$DISTRO" in
    bazzite)
        exec bash "$DISTROBOX_DIR/bazzite.sh" "${FORWARD_ARGS[@]+"${FORWARD_ARGS[@]}"}"
        ;;
    silverblue)
        exec bash "$DISTROBOX_DIR/silverblue.sh" "${FORWARD_ARGS[@]+"${FORWARD_ARGS[@]}"}"
        ;;
    steamos)
        exec bash "$DISTROBOX_DIR/steamos.sh" "${FORWARD_ARGS[@]+"${FORWARD_ARGS[@]}"}"
        ;;
    unknown)
        echo ""
        echo "Could not detect your distro automatically."
        echo "Run the appropriate script manually:"
        echo ""
        echo "  Bazzite:              bash scripts/distrobox/bazzite.sh"
        echo "  Silverblue/Kinoite:   bash scripts/distrobox/silverblue.sh"
        echo "  SteamOS/Steam Deck:   bash scripts/distrobox/steamos.sh"
        echo ""
        exit 1
        ;;
esac
