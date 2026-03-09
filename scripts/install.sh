#!/usr/bin/env bash
# Arctis Sound Manager — installer
set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"

echo "==> Installing Arctis Sound Manager..."

# 1. Check dependencies
if ! command -v uv &>/dev/null; then
    echo "  [!] 'uv' not found. Installing..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
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
find ./dist -name "*.whl" | head -n1 | xargs pipx install --force

# 3. Setup udev rules (requires sudo)
echo "==> Installing udev rules (requires sudo)..."
lam-cli udev write-rules --force --reload

# 4. Setup desktop entries
echo "==> Installing desktop entries..."
lam-cli desktop write

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

echo ""
echo "==> Installation complete!"
echo "    Run 'lam-gui' to open the interface, or find it in your application menu."
