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

# ── DEBIAN/control dependency fields ────────────────────────
# Read from debian/control (the source of truth check-packaging-drift.py's
# DEPS_MAP already validates) rather than hand-kept here a second time. The
# hand-written list drifted: it carried neither python3-pil, python3-babel,
# pulseaudio-utils nor curl, so the .deb attached to GitHub Releases died
# with `ModuleNotFoundError: No module named 'PIL'` on first launch and got
# wrong plural forms in every non-English locale (PKG-1 / PKG-5).
#
# Exclusions: this script pip-installs pulsectl straight into dist-packages
# (see "Bundle dbus-next and pulsectl" below), so python3-pulsectl must not
# also be a system Depends: here. debhelper substvars (${misc:Depends},
# ${dbus-next:Depends}) never resolve outside a dh_gencontrol run, so they
# are dropped too — dbus-next is bundled unconditionally by this script.
_control_field() {
    # $1 = control field name (Depends, Recommends, ...). Prints its
    # comma-joined value with debhelper substvars and this script's bundled
    # packages stripped out.
    python3 -c '
import re
import sys

field = sys.argv[1]
text = open("debian/control", encoding="utf-8").read()
m = re.search(rf"^{field}:[ \t]*(.*?)(?=^\S|\Z)", text, re.M | re.S)
if not m:
    sys.exit(f"no {field}: field found in debian/control")
items = [i.strip() for i in m.group(1).replace("\n", " ").split(",")]
items = [i for i in items if i and not i.startswith("${")]
bundled = {"python3-pulsectl"}
items = [i for i in items if i not in bundled]

# The PPA build resolves PySide6 from the archive of one known series, so
# debian/control can depend on it flatly. This .deb is downloaded from the
# releases page onto whatever the user runs, including series that ship no
# python3-pyside6.* at all (Ubuntu 22.04, Debian 12) — there, a flat
# dependency makes the package uninstallable. The `| python3-pip`
# alternative predates the derivation and is kept deliberately: it lets the
# install proceed and leaves PySide6 to asm-setup.
items = [f"{i} | python3-pip" if i.startswith("python3-pyside6.") else i
         for i in items]
print(", ".join(items))
' "$1"
}

DEB_DEPENDS=$(_control_field Depends)
DEB_RECOMMENDS=$(_control_field Recommends)

if [ -z "${DEB_DEPENDS}" ]; then
    echo "ERROR: could not derive Depends: from debian/control" >&2
    exit 1
fi

# Introspection mode for check-packaging-drift.py and the test suite: print
# the derived Depends: and exit, without requiring uv/dpkg-deb to be
# installed. Keep this above the pre-flight checks below.
if [ "${1:-}" = "--print-depends" ]; then
    echo "${DEB_DEPENDS}"
    exit 0
fi

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
# Read from pyproject.toml rather than kept as a list here. The hand-written
# list drifted: it carried five of the eight entry points, so the .deb on the
# releases page shipped without asm-stream-guard and without asm-clipd — a unit
# file and a keybinding pointing at executables the package did not contain.
# asm-diag-dinit is installed from scripts/ further down; it is a standalone
# script rather than a console entry point.
ENTRY_SPECS=$(awk '/^\[project\.scripts\]/{f=1;next} /^\[/{f=0} f && /=/ {gsub(/"/,""); split($0,a,"="); split(a[2],b,":"); gsub(/[ \t]/,"",a[1]); gsub(/[ \t]/,"",b[1]); if (a[1] != "") print a[1], b[1]}' pyproject.toml)

if [ -z "${ENTRY_SPECS}" ]; then
    echo "ERROR: no [project.scripts] entry points found in pyproject.toml" >&2
    exit 1
fi

while read -r cmd module; do
    [ -z "${cmd}" ] && continue
    # Installed separately from scripts/, not generated from an entry point.
    [ "${cmd}" = "asm-diag-dinit" ] && continue
    cat > "${PKGDIR}/usr/bin/${cmd}" << SCRIPT
#!/usr/bin/python3
import sys
from ${module} import main
sys.exit(main())
SCRIPT
    chmod 755 "${PKGDIR}/usr/bin/${cmd}"
    echo "    console script: ${cmd}"
done <<< "${ENTRY_SPECS}"

# ── udev rules ──────────────────────────────────────────────
echo "==> Installing system files..."
uv run --with ruamel.yaml python3 scripts/generate_udev_rules.py > build/deb/91-steelseries-arctis.rules
install -Dm644 build/deb/91-steelseries-arctis.rules \
    "${PKGDIR}/usr/lib/udev/rules.d/91-steelseries-arctis.rules"

# ── Systemd user services (single source of truth in systemd/) ──
install -Dm644 systemd/arctis-manager.service \
    "${PKGDIR}/usr/lib/systemd/user/arctis-manager.service"
install -Dm644 systemd/arctis-video-router.service \
    "${PKGDIR}/usr/lib/systemd/user/arctis-video-router.service"
install -Dm644 systemd/arctis-stream-guard.service \
    "${PKGDIR}/usr/lib/systemd/user/arctis-stream-guard.service"
# Renamed from arctis-gui.service in 784093a so the unit matches the
# desktop entry and the tray shortcut can bind to it. The .spec and the
# PKGBUILD were updated then; this script was not, and `install` would
# have failed the .deb build on a file that no longer exists.
install -Dm644 systemd/app-ArctisManager.service \
    "${PKGDIR}/usr/lib/systemd/user/app-ArctisManager.service"

# Used by the GUI to restart ASM's own units after an upgrade.
install -Dm755 scripts/restart-user-services.sh \
    "${PKGDIR}/usr/lib/arctis-sound-manager/restart-user-services.sh"

# ── dinit user service templates ────────────────────────────
install -Dm644 dinit/arctis-manager \
    "${PKGDIR}/usr/share/arctis-sound-manager/dinit/arctis-manager"
install -Dm644 dinit/arctis-video-router \
    "${PKGDIR}/usr/share/arctis-sound-manager/dinit/arctis-video-router"
install -Dm644 dinit/arctis-stream-guard \
    "${PKGDIR}/usr/share/arctis-sound-manager/dinit/arctis-stream-guard"
install -Dm644 dinit/arctis-gui \
    "${PKGDIR}/usr/share/arctis-sound-manager/dinit/arctis-gui"
install -Dm644 dinit/pipewire-filter-chain \
    "${PKGDIR}/usr/share/arctis-sound-manager/dinit/pipewire-filter-chain"
install -Dm755 scripts/asm-diag-dinit.py \
    "${PKGDIR}/usr/bin/asm-diag-dinit"

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
Depends: ${DEB_DEPENDS}
Recommends: ${DEB_RECOMMENDS}
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
