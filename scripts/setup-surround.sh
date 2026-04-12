#!/usr/bin/env bash
# Virtual surround 7.1 setup for stereo headsets (HeSuVi / PipeWire filter-chain)
# Can be run standalone or before install.sh
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# HeSuVi config goes in filter-chain.conf.d — it is managed by filter-chain.service.
# The daemon regenerates this file with the correct physical output at runtime.
FC_CONF_DIR="$HOME/.config/pipewire/filter-chain.conf.d"
HRIR_DIR="$HOME/.local/share/pipewire/hrir_hesuvi"
CONF_SRC="$REPO_DIR/scripts/pipewire/sink-virtual-surround-7.1-hesuvi.conf"

echo "==> Setting up virtual surround 7.1 (HeSuVi / PipeWire filter-chain)..."

# Remove stale copy from pipewire.conf.d if present (old installs put it there)
rm -f "$HOME/.config/pipewire/pipewire.conf.d/sink-virtual-surround-7.1-hesuvi.conf"

# 1. Install the config into filter-chain.conf.d
mkdir -p "$FC_CONF_DIR"
cp "$CONF_SRC" "$FC_CONF_DIR/sink-virtual-surround-7.1-hesuvi.conf"
echo "    [ok] HeSuVi config installed to filter-chain.conf.d."

# 2. Install HRIR file
mkdir -p "$HRIR_DIR"

if [ -s "$HRIR_DIR/hrir.wav" ]; then
    echo "    [ok] HRIR file already present — skipping download."
else
    rm -f "$HRIR_DIR/hrir.wav"
    echo "    Downloading default HRIR file (KEMAR Gardner 1995)..."
    HRIR_URL="https://github.com/nicehash/HeSuVi/raw/master/hrir/44/KEMAR%20Gardner%201995/kemar.wav"
    downloaded=false
    if command -v curl &>/dev/null; then
        curl -L -o "$HRIR_DIR/hrir.wav" "$HRIR_URL" && [ -s "$HRIR_DIR/hrir.wav" ] && downloaded=true
    fi
    if ! $downloaded && command -v wget &>/dev/null; then
        wget -O "$HRIR_DIR/hrir.wav" "$HRIR_URL" && [ -s "$HRIR_DIR/hrir.wav" ] && downloaded=true
    fi
    if ! $downloaded; then
        rm -f "$HRIR_DIR/hrir.wav"
        echo "  [!] Could not download HRIR file. Please download it manually:"
        echo "      Source: https://github.com/nicehash/HeSuVi/tree/master/hrir/44"
        echo "      Save it as: $HRIR_DIR/hrir.wav"
        echo "      Then run: systemctl --user restart filter-chain.service"
        exit 1
    fi
    echo "    [ok] HRIR file downloaded."
fi

# 3. Ensure filter-chain service exists and enable it
echo "==> Enabling filter-chain service..."
FC_SERVICE=""
for name in filter-chain.service pipewire-filter-chain.service; do
    if systemctl --user list-unit-files "$name" 2>/dev/null | grep -q "${name%%.*}"; then
        FC_SERVICE="$name"
        break
    fi
done
if [ -z "$FC_SERVICE" ]; then
    echo "  [info] filter-chain.service not found — installing bundled copy..."
    SYSTEMD_USER_DIR="$HOME/.config/systemd/user"
    mkdir -p "$SYSTEMD_USER_DIR"
    cp "$REPO_DIR/scripts/filter-chain.service" "$SYSTEMD_USER_DIR/filter-chain.service"
    systemctl --user daemon-reload
    FC_SERVICE="filter-chain.service"
fi
systemctl --user enable --now "$FC_SERVICE"
systemctl --user restart "$FC_SERVICE"

echo ""
echo "==> Virtual surround ready!"
echo "    A new sink 'Virtual Surround Sink' is now available."
echo "    Route your headset's output to it in your audio settings to enable 7.1 surround."
