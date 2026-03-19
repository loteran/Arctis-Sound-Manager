#!/usr/bin/env bash
# Virtual surround 7.1 setup for stereo headsets (HeSuVi / PipeWire filter-chain)
# Run this BEFORE install.sh
set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Deploy to pipewire.conf.d so PipeWire loads the HeSuVi sink before
# filter-chain.service starts — prevents ENOENT when sonar-game-eq.conf
# references effect_input.virtual-surround-7.1-hesuvi at graph startup.
CONF_DST="$HOME/.config/pipewire/pipewire.conf.d"
HRIR_DIR="$HOME/.local/share/pipewire/hrir_hesuvi"
CONF_SRC="$REPO_DIR/scripts/pipewire/sink-virtual-surround-7.1-hesuvi.conf"

echo "==> Setting up virtual surround 7.1 (HeSuVi / PipeWire filter-chain)..."

# Remove stale copy from filter-chain.conf.d if present
rm -f "$HOME/.config/pipewire/filter-chain.conf.d/sink-virtual-surround-7.1-hesuvi.conf"

# 1. Install the config into pipewire.conf.d
mkdir -p "$CONF_DST"
cp "$CONF_SRC" "$CONF_DST/sink-virtual-surround-7.1-hesuvi.conf"
echo "    [ok] HeSuVi config installed to pipewire.conf.d."

# 2. Install HRIR file
mkdir -p "$HRIR_DIR"

if [ -f "$HRIR_DIR/hrir.wav" ]; then
    echo "    [ok] HRIR file already present — skipping download."
else
    echo "    Downloading default HRIR file (KEMAR Gardner 1995)..."
    HRIR_URL="https://github.com/nicehash/HeSuVi/raw/master/hrir/44/KEMAR%20Gardner%201995/kemar.wav"
    if command -v curl &>/dev/null; then
        curl -L -o "$HRIR_DIR/hrir.wav" "$HRIR_URL"
    elif command -v wget &>/dev/null; then
        wget -O "$HRIR_DIR/hrir.wav" "$HRIR_URL"
    else
        echo "  [!] Neither curl nor wget found. Please download the HRIR file manually:"
        echo "      Source: https://github.com/nicehash/HeSuVi/tree/master/hrir/44"
        echo "      Save it as: $HRIR_DIR/hrir.wav"
        echo "      Then run: systemctl --user restart filter-chain.service"
        exit 1
    fi
    echo "    [ok] HRIR file downloaded."
fi

# 3. Enable and restart filter-chain service
echo "==> Enabling filter-chain service..."
systemctl --user enable --now filter-chain.service
systemctl --user restart filter-chain.service

echo ""
echo "==> Virtual surround ready!"
echo "    A new sink 'Virtual Surround Sink' is now available."
echo "    Route your headset's output to it in your audio settings to enable 7.1 surround."
echo ""
echo "    You can now run: bash scripts/install.sh"
