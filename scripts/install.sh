#!/usr/bin/env bash
# Arctis Sound Manager — installer
set -euo pipefail

# Ensure pipx/pipx-installed binaries (asm-cli, asm-daemon) are always in PATH,
# regardless of whether uv was just installed or already present.
export PATH="$HOME/.local/bin:$PATH"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"

echo "==> Installing Arctis Sound Manager..."

# 1. Check dependencies
if ! command -v uv &>/dev/null; then
    echo "  [!] 'uv' not found. Installing..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi

if ! command -v pipx &>/dev/null; then
    echo "  [!] 'pipx' not found. Please install it with your package manager."
    echo "      Arch/CachyOS:  sudo pacman -S python-pipx"
    echo "      Debian/Ubuntu: sudo apt install pipx"
    exit 1
fi

# 2. Build and install the package
echo "==> Building package..."
cd "$REPO_DIR"
rm -rf dist
uv build

echo "==> Installing package with pipx..."
WHL=$(find ./dist -name "*.whl" | head -n1)
[ -n "$WHL" ] || { echo "  [!] No wheel found in dist/ — build failed."; exit 1; }
pipx install --force "$WHL"

# 3. Setup udev rules (requires sudo)
echo "==> Installing udev rules (requires sudo)..."
asm-cli udev write-rules --force --reload

# 4. Install app icon (requires sudo)
echo "==> Installing app icon (requires sudo)..."
sudo install -Dm644 "$REPO_DIR/src/arctis_sound_manager/gui/images/steelseries_logo.svg" \
    /usr/share/icons/hicolor/scalable/apps/arctis-manager.svg
gtk-update-icon-cache /usr/share/icons/hicolor/ 2>/dev/null || true
echo "    [ok] Icon installed."

# 5. Install AppStream metainfo (requires sudo)
echo "==> Installing AppStream metainfo..."
sudo install -Dm644 "$REPO_DIR/src/arctis_sound_manager/desktop/com.github.loteran.arctis-sound-manager.metainfo.xml" \
    /usr/share/metainfo/com.github.loteran.arctis-sound-manager.metainfo.xml
echo "    [ok] Metainfo installed."

# 6. Setup desktop entries
echo "==> Installing desktop entries..."
asm-cli desktop write

# 5. Install and enable the main daemon service
echo "==> Enabling arctis-manager systemd service..."
systemctl --user daemon-reload
systemctl --user enable --now arctis-manager.service

# 6. Install the media router service
echo "==> Installing arctis-video-router systemd service..."
mkdir -p "$SYSTEMD_USER_DIR"
cp "$REPO_DIR/scripts/arctis-video-router.service" "$SYSTEMD_USER_DIR/"
systemctl --user daemon-reload
systemctl --user enable --now arctis-video-router.service

# 7. Copy device config to user config dir (needed for Sonar EQ mode switch)
echo "==> Copying device config to user config dir..."
ARCTIS_CONFIG_DIR="$HOME/.config/arctis_manager/devices"
mkdir -p "$ARCTIS_CONFIG_DIR"
DEVICES_SRC="$REPO_DIR/src/arctis_sound_manager/devices"
if [ -d "$DEVICES_SRC" ]; then
    cp "$DEVICES_SRC"/*.yaml "$ARCTIS_CONFIG_DIR/"
    echo "    [ok] Device configs copied."
else
    echo "    [!] Device config source not found at $DEVICES_SRC — skipping."
fi

# 8. Deploy native PipeWire configs (fixes WirePlumber crash on 0.5.x + load order)
echo "==> Installing native PipeWire configs..."
PIPEWIRE_CONF_DIR="$HOME/.config/pipewire/pipewire.conf.d"
FILTERCHAIN_CONF_DIR="$HOME/.config/pipewire/filter-chain.conf.d"
mkdir -p "$PIPEWIRE_CONF_DIR" "$FILTERCHAIN_CONF_DIR"
# Virtual sinks (Arctis_Game, Arctis_Chat, Arctis_Media)
cp "$REPO_DIR/scripts/pipewire/10-arctis-virtual-sinks.conf" "$PIPEWIRE_CONF_DIR/"
# HeSuVi surround sink — goes in filter-chain.conf.d (managed by the filter-chain service).
# The daemon regenerates this file with the correct physical output at runtime;
# the copy here serves as the initial default before the daemon writes its own version.
cp "$REPO_DIR/scripts/pipewire/sink-virtual-surround-7.1-hesuvi.conf" "$FILTERCHAIN_CONF_DIR/"
# Remove stale copy from pipewire.conf.d if present (old installs put it there, causing
# a duplicate-node conflict with the filter-chain version → silent Game channel).
rm -f "$PIPEWIRE_CONF_DIR/sink-virtual-surround-7.1-hesuvi.conf"
systemctl --user restart pipewire pipewire-pulse || true
echo "    [ok] PipeWire configs deployed."

# 9. Install HRIR file for virtual surround (if not already present)
HRIR_DIR="$HOME/.local/share/pipewire/hrir_hesuvi"
mkdir -p "$HRIR_DIR"
if [ -f "$HRIR_DIR/hrir.wav" ]; then
    echo "    [ok] HRIR file already present — skipping download."
else
    echo "==> Downloading default HRIR file (KEMAR Gardner 1995)..."
    HRIR_URL="https://github.com/nicehash/HeSuVi/raw/master/hrir/44/KEMAR%20Gardner%201995/kemar.wav"
    if command -v curl &>/dev/null; then
        curl -L -o "$HRIR_DIR/hrir.wav" "$HRIR_URL"
    elif command -v wget &>/dev/null; then
        wget -O "$HRIR_DIR/hrir.wav" "$HRIR_URL"
    else
        echo "  [!] Neither curl nor wget found. Download the HRIR file manually:"
        echo "      Source: https://github.com/nicehash/HeSuVi/tree/master/hrir/44"
        echo "      Save it as: $HRIR_DIR/hrir.wav"
    fi
    if [ -s "$HRIR_DIR/hrir.wav" ]; then
        echo "    [ok] HRIR file downloaded."
    else
        rm -f "$HRIR_DIR/hrir.wav"
        echo "  [!] HRIR download failed or file is empty — virtual surround will not work."
        echo "      Download manually: https://github.com/nicehash/HeSuVi/tree/master/hrir/44"
        echo "      Save as: $HRIR_DIR/hrir.wav, then run: systemctl --user restart filter-chain.service"
    fi
fi

# 10. Enable filter-chain service (required for Sonar EQ and virtual surround)
echo "==> Enabling filter-chain systemd service..."
# Detect the service name (differs by distro); install a bundled one if absent
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

echo ""
echo "==> Installation complete!"
echo "    Run 'asm-gui' to open the interface, or find it in your application menu."
