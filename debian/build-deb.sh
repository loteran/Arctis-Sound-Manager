#!/bin/bash
# Build arctis-sound-manager .deb package (works on any distro with uv + dpkg-deb)
set -euo pipefail

cd "$(dirname "$0")/.."

PKG="arctis-sound-manager"
VERSION=$(python3 -c "
import tomllib
with open('pyproject.toml', 'rb') as f:
    print(tomllib.load(f)['project']['version'])
")
ARCH="all"
DEB_NAME="${PKG}_${VERSION}-1_${ARCH}.deb"
PKGDIR="build/deb/${PKG}_${VERSION}-1_${ARCH}"
PYLIB="${PKGDIR}/usr/lib/python3/dist-packages"

echo "==> Building ${DEB_NAME} ..."

# ── Pre-flight checks ──────────────────────────────────────
for cmd in uv dpkg-deb; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "ERROR: '$cmd' not found. Install it first."
        [ "$cmd" = "dpkg-deb" ] && echo "  Arch: pacman -S dpkg"
        exit 1
    fi
done
# Use uv pip (already required for wheel build)
PIP="uv pip"

# ── Clean ───────────────────────────────────────────────────
rm -rf build/deb
mkdir -p "${PKGDIR}/DEBIAN" "${PYLIB}" "${PKGDIR}/usr/bin"

# ── Build wheel ─────────────────────────────────────────────
echo "==> Building wheel..."
uv build --wheel --out-dir build/deb/

# ── Python packages ─────────────────────────────────────────
echo "==> Installing Python packages..."
export PYTHONDONTWRITEBYTECODE=1

# App (no deps — system packages handle most)
$PIP install --target="${PYLIB}" --no-deps --python-platform linux --python-version 3.12 \
    build/deb/arctis_sound_manager-*.whl

# Bundle dbus-next and pulsectl (not in Ubuntu/Debian repos)
$PIP install --target="${PYLIB}" --no-deps --python-platform linux --python-version 3.12 \
    dbus-next pulsectl

# Clean bytecode and uv artifacts
find "${PYLIB}" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
rm -f "${PYLIB}/.lock"

# ── Console scripts ─────────────────────────────────────────
declare -A ENTRIES=(
    [asm-daemon]="arctis_sound_manager.scripts.daemon"
    [asm-cli]="arctis_sound_manager.scripts.cli"
    [asm-gui]="arctis_sound_manager.scripts.gui"
    [asm-router]="arctis_sound_manager.scripts.video_router"
    [asm-setup]="arctis_sound_manager.scripts.setup"
)
for cmd in "${!ENTRIES[@]}"; do
    cat > "${PKGDIR}/usr/bin/${cmd}" << SCRIPT
#!/usr/bin/python3
import sys
from ${ENTRIES[$cmd]} import main
sys.exit(main())
SCRIPT
    chmod 755 "${PKGDIR}/usr/bin/${cmd}"
done

# ── udev rules ──────────────────────────────────────────────
echo "==> Installing system files..."
uv run --with ruamel.yaml python3 scripts/generate_udev_rules.py > build/deb/91-steelseries-arctis.rules
install -Dm644 build/deb/91-steelseries-arctis.rules \
    "${PKGDIR}/usr/lib/udev/rules.d/91-steelseries-arctis.rules"

# ── Systemd user services ──────────────────────────────────
install -Dm644 debian/arctis-manager.service \
    "${PKGDIR}/usr/lib/systemd/user/arctis-manager.service"
install -Dm644 debian/arctis-video-router.service \
    "${PKGDIR}/usr/lib/systemd/user/arctis-video-router.service"
install -Dm644 debian/arctis-gui.service \
    "${PKGDIR}/usr/lib/systemd/user/arctis-gui.service"

# ── Desktop entry ───────────────────────────────────────────
install -Dm644 src/arctis_sound_manager/desktop/ArctisManager.desktop \
    "${PKGDIR}/usr/share/applications/ArctisManager.desktop"

# ── Icon ────────────────────────────────────────────────────
install -Dm644 src/arctis_sound_manager/gui/images/steelseries_logo.svg \
    "${PKGDIR}/usr/share/icons/hicolor/scalable/apps/arctis-manager.svg"

# ── AppStream metainfo ──────────────────────────────────────
install -Dm644 src/arctis_sound_manager/desktop/com.github.loteran.arctis-sound-manager.metainfo.xml \
    "${PKGDIR}/usr/share/metainfo/com.github.loteran.arctis-sound-manager.metainfo.xml"

# ── PipeWire configs ────────────────────────────────────────
install -Dm644 scripts/pipewire/10-arctis-virtual-sinks.conf \
    "${PKGDIR}/usr/share/${PKG}/pipewire/10-arctis-virtual-sinks.conf"
install -Dm644 scripts/pipewire/sink-virtual-surround-7.1-hesuvi.conf \
    "${PKGDIR}/usr/share/${PKG}/pipewire/sink-virtual-surround-7.1-hesuvi.conf"

# ── filter-chain.service ─────────────────────────────────────
install -Dm644 scripts/filter-chain.service \
    "${PKGDIR}/usr/share/${PKG}/filter-chain.service"

# ── Device configs ──────────────────────────────────────────
install -d "${PKGDIR}/usr/share/${PKG}/devices"
install -Dm644 src/arctis_sound_manager/devices/*.yaml \
    -t "${PKGDIR}/usr/share/${PKG}/devices/"

# ── First-run autostart ─────────────────────────────────────
install -Dm644 debian/asm-first-run.desktop \
    "${PKGDIR}/etc/xdg/autostart/asm-first-run.desktop"

# ── DEBIAN/control ──────────────────────────────────────────
cat > "${PKGDIR}/DEBIAN/control" << EOF
Package: ${PKG}
Version: ${VERSION}-1
Architecture: ${ARCH}
Maintainer: loteran <axel.valadon@gmail.com>
Depends: python3 (>= 3.10), python3-pyside6.qtcore | python3-pip, python3-pyside6.qtgui | python3-pip, python3-pyside6.qtwidgets | python3-pip, python3-pyside6.qtsvg | python3-pip, python3-pyside6.qtnetwork | python3-pip, python3-pyudev, python3-usb, python3-ruamel.yaml, pipewire, pipewire-pulse, wireplumber, libusb-1.0-0
Recommends: noise-suppression-for-voice, swh-plugins
Section: sound
Priority: optional
Homepage: https://github.com/loteran/Arctis-Sound-Manager
Description: Linux GUI for SteelSeries Arctis headsets
 Arctis Sound Manager is a Linux application for configuring SteelSeries Arctis
 headsets. It provides a 4-channel audio mixer (Game / Chat / Media / HDMI),
 a full Sonar parametric EQ system with 297+ presets, virtual 7.1 surround sound,
 ANC/Transparent mode control, and device management via PipeWire.
EOF

# ── DEBIAN/postinst ─────────────────────────────────────────
install -m755 debian/postinst "${PKGDIR}/DEBIAN/postinst"

# ── DEBIAN/postrm ───────────────────────────────────────────
install -m755 debian/postrm "${PKGDIR}/DEBIAN/postrm"

# ── md5sums ─────────────────────────────────────────────────
(cd "${PKGDIR}" && find usr etc -type f -exec md5sum {} +) > "${PKGDIR}/DEBIAN/md5sums"

# ── Build .deb ──────────────────────────────────────────────
echo "==> Packaging..."
dpkg-deb --root-owner-group --build "${PKGDIR}" "build/deb/${DEB_NAME}"

echo ""
echo "==> Done!"
ls -lh "build/deb/${DEB_NAME}"
