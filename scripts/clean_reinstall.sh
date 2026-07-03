#!/usr/bin/env bash
# Arctis Sound Manager — clean reinstall utility
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "=================================================="
echo "==> Starting CLEAN REINSTALL..."
echo "=================================================="

# 1. Run uninstaller with purge and auto-confirm
if [ -f "$REPO_DIR/scripts/uninstall.sh" ]; then
    echo "==> Purging old installation and user configurations..."
    bash "$REPO_DIR/scripts/uninstall.sh" --all --purge --yes
else
    echo "  [!] uninstaller.sh not found, skipping purge..."
fi

# 2. Run installer
if [ -f "$REPO_DIR/scripts/install.sh" ]; then
    echo "==> Rebuilding and reinstalling..."
    bash "$REPO_DIR/scripts/install.sh"
else
    echo "  [!] install.sh not found! Cannot reinstall."
    exit 1
fi

# 3. Run post-install setup
echo "==> Running post-install setup..."
export PATH="$HOME/.local/bin:$PATH"
if command -v asm-setup &>/dev/null; then
    asm-setup
else
    echo "  [!] asm-setup not found. Please run it manually if needed."
fi

echo ""
echo "=================================================="
echo "==> Clean reinstall complete!"
echo "    Run 'asm-gui' to start the application."
echo "=================================================="
