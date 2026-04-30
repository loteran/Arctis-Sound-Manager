Name:           arctis-sound-manager
Version:        1.0.85
Release:        1%{?dist}
Summary:        Linux GUI for SteelSeries Arctis headsets

# Fedora ships slightly older versions of ruamel-yaml and pyudev — suppress the
# auto-generated versioned requirements from the wheel metadata and rely on the
# explicit Requires: lines below instead.
%global __requires_exclude python3[0-9.]*dist\\((ruamel-yaml|pyudev)\\)

License:        GPL-3.0-or-later
URL:            https://github.com/loteran/Arctis-Sound-Manager
Source0:        %{url}/archive/refs/tags/v%{version}.tar.gz
Source1:        arctis_sound_manager-%{version}-py3-none-any.whl
Source2:        dbus_next-0.2.3-py3-none-any.whl

BuildArch:      noarch
BuildRequires:  python3-devel
BuildRequires:  python3-installer
BuildRequires:  python3-ruamel-yaml
BuildRequires:  systemd-rpm-macros
BuildRequires:  desktop-file-utils
BuildRequires:  libappstream-glib

Requires:       python3-pyside6
Requires:       python3-pillow
Requires:       python3-pulsectl
Requires:       python3-pyudev
Requires:       python3-pyusb
Requires:       python3-ruamel-yaml
Requires:       pipewire
Requires:       pipewire-pulseaudio
Requires:       wireplumber
Requires:       libusb1
Requires:       pulseaudio-libs

Recommends:     noise-suppression-for-voice
# Fedora ships the Steve Harris SWH LADSPA pack as `ladspa-swh-plugins`
# (the upstream name `swh-plugins` is the Debian/Arch package name and
# does not exist in Fedora repos — the old line silently pulled in
# nothing). Required by HeSuVi 7.1 virtual surround (`plate_1423`
# reverb plugin) — Spatial Audio is silent without it (issue #23).
Recommends:     ladspa-swh-plugins

%description
Arctis Sound Manager is a Linux application for configuring SteelSeries Arctis
headsets. It provides a 4-channel audio mixer (Game / Chat / Media / HDMI),
a full Sonar parametric EQ system with 297+ presets, virtual 7.1 surround sound,
ANC/Transparent mode control, and device management via PipeWire.

%prep
%autosetup -n Arctis-Sound-Manager-%{version}

%build
# Wheel is pre-built in SRPM via .copr/Makefile

%install
python3 -m installer --destdir=%{buildroot} %{SOURCE1}

# Bundle dbus-next (not in Fedora repos — pre-downloaded in Source2)
python3 -m installer --destdir=%{buildroot} %{SOURCE2}

# udev rules — generated from device YAMLs at build time (single source of truth)
install -Dm644 /dev/null %{buildroot}%{_udevrulesdir}/91-steelseries-arctis.rules
python3 scripts/generate_udev_rules.py src/arctis_sound_manager/devices/ \
    > %{buildroot}%{_udevrulesdir}/91-steelseries-arctis.rules

# Systemd user services (single source of truth in systemd/, not heredocs)
install -Dm644 systemd/arctis-manager.service       %{buildroot}%{_userunitdir}/arctis-manager.service
install -Dm644 systemd/arctis-video-router.service  %{buildroot}%{_userunitdir}/arctis-video-router.service
install -Dm644 systemd/arctis-gui.service           %{buildroot}%{_userunitdir}/arctis-gui.service

# Desktop entry
install -Dm644 src/arctis_sound_manager/desktop/ArctisManager.desktop \
    %{buildroot}%{_datadir}/applications/ArctisManager.desktop
desktop-file-validate %{buildroot}%{_datadir}/applications/ArctisManager.desktop

# Icon
install -Dm644 src/arctis_sound_manager/gui/images/steelseries_logo.svg \
    %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/arctis-manager.svg

# AppStream metainfo (releases injected from CHANGELOG.md — never hardcode)
python3 scripts/generate_metainfo_releases.py --in-place
install -Dm644 src/arctis_sound_manager/desktop/com.github.loteran.arctis-sound-manager.metainfo.xml \
    %{buildroot}%{_metainfodir}/com.github.loteran.arctis-sound-manager.metainfo.xml
appstream-util validate-relax --nonet \
    %{buildroot}%{_metainfodir}/com.github.loteran.arctis-sound-manager.metainfo.xml

# PipeWire configs
install -Dm644 scripts/pipewire/10-arctis-virtual-sinks.conf \
    %{buildroot}%{_datadir}/%{name}/pipewire/10-arctis-virtual-sinks.conf
install -Dm644 scripts/pipewire/sink-virtual-surround-7.1-hesuvi.conf \
    %{buildroot}%{_datadir}/%{name}/pipewire/sink-virtual-surround-7.1-hesuvi.conf

# filter-chain.service (bundled for distros that don't ship one)
install -Dm644 scripts/filter-chain.service \
    %{buildroot}%{_datadir}/%{name}/filter-chain.service

# Device configs
install -d %{buildroot}%{_datadir}/%{name}/devices
install -Dm644 src/arctis_sound_manager/devices/*.yaml \
    -t %{buildroot}%{_datadir}/%{name}/devices/

# First-run autostart (triggers asm-setup on first graphical login)
install -Dm644 debian/asm-first-run.desktop \
    %{buildroot}/etc/xdg/autostart/asm-first-run.desktop

%post
%systemd_user_post arctis-manager.service arctis-video-router.service arctis-gui.service
udevadm control --reload-rules || :
udevadm trigger --action=add --subsystem-match=usb || :

# Run asm-setup immediately if the installing user has an active D-Bus session.
# Falls back to /etc/xdg/autostart/asm-first-run.desktop on next login otherwise.
REAL_USER="${SUDO_USER:-}"
if [ -n "$REAL_USER" ]; then
    REAL_UID=$(id -u "$REAL_USER")
    XDG_RUNTIME_DIR="/run/user/$REAL_UID"
    DBUS_SOCKET="$XDG_RUNTIME_DIR/bus"
    if [ -S "$DBUS_SOCKET" ]; then
        echo "  Running asm-setup for ${REAL_USER}..."
        su -l "$REAL_USER" -c \
            "XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR DBUS_SESSION_BUS_ADDRESS=unix:path=$DBUS_SOCKET asm-setup" \
            || echo "  [!] asm-setup failed — run manually: asm-setup"
    else
        echo "  No active session found — asm-setup will run on next login."
    fi
fi

%preun
%systemd_user_preun arctis-manager.service arctis-video-router.service arctis-gui.service

%postun
%systemd_user_postun arctis-manager.service arctis-video-router.service arctis-gui.service
# Clean up user-level PipeWire configs on real removal (not upgrade).
# RPM passes the post-transaction package count as $1 — 0 means uninstall,
# >=1 means an upgrade is in progress and we must keep configs in place.
# Without this block the "ghost" Arctis virtual sinks keep being loaded
# by PipeWire on every login because asm-setup deployed them to
# ~/.config/pipewire/ and the package can't reach into $HOME (issue #24).
# Audio profiles in ~/.config/arctis_manager/profiles/ are PRESERVED.
if [ "$1" = "0" ]; then
    REAL_USER="${SUDO_USER:-}"
    if [ -n "$REAL_USER" ]; then
        REAL_UID=$(id -u "$REAL_USER" 2>/dev/null || echo "")
        if [ -n "$REAL_UID" ]; then
            XDG_RUNTIME_DIR="/run/user/$REAL_UID"
            DBUS_SOCKET="$XDG_RUNTIME_DIR/bus"
            echo "==> Cleaning up user-level configs for ${REAL_USER} (audio profiles preserved)..."
            su -l "$REAL_USER" -c "
                XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR DBUS_SESSION_BUS_ADDRESS=unix:path=$DBUS_SOCKET
                export XDG_RUNTIME_DIR DBUS_SESSION_BUS_ADDRESS
                for unit in arctis-manager.service arctis-video-router.service arctis-gui.service filter-chain.service; do
                    systemctl --user disable --now \"\$unit\" >/dev/null 2>&1 || true
                done
                rm -f \"\$HOME/.config/pipewire/pipewire.conf.d/10-arctis-virtual-sinks.conf\"
                rm -f \"\$HOME/.config/pipewire/filter-chain.conf.d/sink-virtual-surround-7.1-hesuvi.conf\"
                rm -f \"\$HOME/.config/pipewire/filter-chain.conf.d\"/sonar-*.conf
                rm -f \"\$HOME/.config/systemd/user/arctis-manager.service\"
                rm -f \"\$HOME/.config/systemd/user/arctis-video-router.service\"
                rm -f \"\$HOME/.config/systemd/user/arctis-gui.service\"
                if [ -S \"\$DBUS_SOCKET\" ]; then
                    systemctl --user daemon-reload >/dev/null 2>&1 || true
                    systemctl --user restart pipewire pipewire-pulse >/dev/null 2>&1 || true
                fi
            " 2>/dev/null || true
        fi
    else
        echo "==> arctis-sound-manager removed."
        echo "    To finish cleaning up ghost virtual sinks (run as your normal user):"
        echo "    curl -fsSL https://raw.githubusercontent.com/loteran/Arctis-Sound-Manager/main/scripts/uninstall.sh | bash -s -- --purge --yes"
    fi
fi

%files
%license LICENSE
%doc README.md CHANGELOG.md
%{python3_sitelib}/arctis_sound_manager/
%{python3_sitelib}/arctis_sound_manager-*.dist-info/
%{python3_sitelib}/dbus_next/
%{python3_sitelib}/dbus_next-*.dist-info/
%{_bindir}/asm-daemon
%{_bindir}/asm-gui
%{_bindir}/asm-cli
%{_bindir}/asm-router
%{_bindir}/asm-setup
%{_udevrulesdir}/91-steelseries-arctis.rules
%{_userunitdir}/arctis-manager.service
%{_userunitdir}/arctis-video-router.service
%{_userunitdir}/arctis-gui.service
%{_datadir}/applications/ArctisManager.desktop
%{_datadir}/icons/hicolor/scalable/apps/arctis-manager.svg
%{_metainfodir}/com.github.loteran.arctis-sound-manager.metainfo.xml
%{_datadir}/%{name}/
/etc/xdg/autostart/asm-first-run.desktop

%changelog
* Tue Apr 22 2026 loteran <https://github.com/loteran> - 1.0.64-1
- Fix: install update button now always shown in home banner (not only for pipx installs)

* Tue Apr 22 2026 loteran <https://github.com/loteran> - 1.0.63-1
- Add: product_id to telemetry payload — enables per-variant hardware tracking
- Add: scripts/update_readme_stats.py — auto-updates README devices/distros tables from telemetry
- Add: cloudflare/migrate_v2.sql — D1 migration for product_id column

* Mon Apr 21 2026 loteran <https://github.com/loteran> - 1.0.62-1
- Fix: ASM claims default sink on start (pactl) and restores previous default on exit — EasyEffects coexists cleanly
- Revert: priority.session back to neutral (1000/unset) — explicit metadata selection is the correct mechanism

* Mon Apr 21 2026 loteran <https://github.com/loteran> - 1.0.61-1
- Fix: raise priority.session to 2000 on all ASM virtual sinks so ASM always wins over EasyEffects in WirePlumber routing

* Mon Apr 21 2026 loteran <https://github.com/loteran> - 1.0.60-1
- Refactor: udev rules generated at build time from device YAMLs — no more hardcoded rules that drift out of sync
- Add: asm-cli udev dump-rules subcommand (stdout output for packaging)
- Add: python3-ruamel-yaml to BuildRequires

* Mon Apr 21 2026 loteran <https://github.com/loteran> - 1.0.59-1
- Fix: udev rules missing PID 1294 for Arctis Pro Wireless — caused Errno 13 Access denied on Fedora/Nobara
- Fix: remove ENV{DEVTYPE}=="usb_device" from all udev rules — fails silently on some kernels
- Fix: sync udev rules with all device yamls (add Nova 7 Gen1/Gen2, Arctis 1/7X/7P, Arctis Pro 2019, Nova 3P/3X, Nova 7P Gen2)

* Thu Apr 16 2026 loteran <https://github.com/loteran> - 1.0.43-1
- Fix: udev rules file installed with 0600 perms (pkexec cp) — dialog reappeared at every login
- Fix: arctis-gui.service missing from package — systray never started at login despite "launch on startup"

* Wed Apr 15 2026 loteran <https://github.com/loteran> - 1.0.40-1
- Fix udev: remove GROUP="plugdev" from generated rules (breaks on Fedora)
- Fix report dialog: clipboard copy and GitHub issue link (URL too long)
- Fix RPM: filter auto-generated python dist version requires
- Feat: in-app update detects install method and shows correct package manager command

* Mon Apr 14 2026 loteran <https://github.com/loteran> - 1.0.38-1
- Rename preset files: remove tm/r symbols that broke bsdtar packaging
- Fix AUR: use uv pip install --prefix instead of uv pip download

* Sun Apr 12 2026 loteran <https://github.com/loteran> - 1.0.36-1
- Fix cross-distro USB permissions: udev rules use plugdev group and drop DEVTYPE check

* Sun Apr 12 2026 loteran <https://github.com/loteran> - 1.0.35-1
- Fix udev rules: one rule per PID instead of multi-value ATTRS{idProduct} (not supported by all udev versions)

* Sat Apr 12 2026 loteran <https://github.com/loteran> - 1.0.34-1
- Bundle dbus-next and pulsectl (not in Fedora/Ubuntu/Debian/Arch repos)
- Add wireplumber to Requires
- Auto-run asm-setup in %%post when user session is active (SUDO_USER + D-Bus socket)
- Install XDG autostart fallback (/etc/xdg/autostart/asm-first-run.desktop)
- asm-setup: write .setup_done flag to skip autostart on subsequent logins

* Sat Apr 12 2026 loteran <https://github.com/loteran> - 1.0.33-1
- Bundle dbus-next (not in Fedora repos)
- Add wireplumber to Requires
- Auto-run asm-setup in %%post if user session is active
- Install XDG autostart fallback for asm-setup

* Fri Apr 11 2026 loteran <https://github.com/loteran> - 1.0.31-1
- Bundle 334 Sonar presets in package (312 Game, 8 Chat, 14 Mic)
- asm-setup: full automation (desktop, udev, services, PipeWire)
- Settings: Launch at startup toggle via systemd
- Fix: hardcoded device YAML and NVMe path

* Thu Apr 10 2026 loteran <https://github.com/loteran> - 1.0.27-1
- Fix: silent Game channel persists after Sonar mode switch (issue #14) — duplicate HeSuVi node
  (install.sh places static config in pipewire.conf.d, Sonar generates dynamic in filter-chain.conf.d;
  both registering the same node name caused the game sink to connect to nothing)

* Thu Apr 10 2026 loteran <https://github.com/loteran> - 1.0.26-1
- Fix: silent Game channel on first Sonar mode switch (issue #14)

* Thu Apr 10 2026 loteran <https://github.com/loteran> - 1.0.25-1
- Fix: install.sh PATH regression — export PATH=~/.local/bin always set so asm-cli/asm-daemon found on reinstall (issue #3)
- CI: automate AUR and COPR builds on release

* Fri Apr 04 2026 loteran <https://github.com/loteran> - 1.0.13-1
- Fix: DeviceConfiguration.device_init and .status always initialized (no more AttributeError)
- Fix: SELECT widget IndexError/KeyError when options not loaded
- Fix: device_state thread safety via threading.Lock
- Fix: PipeWire node.target omitted when empty (invalid SPA JSON)

* Fri Apr 04 2026 loteran <https://github.com/loteran> - 1.0.12-1
- Fix: LADSPA mic processing (noise gate, RNNoise, compressor) silently broken — missing = in PipeWire control syntax
- Fix: get_physical_source() retries 15x on PulseAudio error
- Fix: D-Bus polling loop survives transient connection errors

* Tue Apr 01 2026 loteran <https://github.com/loteran> - 1.0.7-1
- Add one-click auto-update from GUI

* Tue Apr 01 2026 loteran <https://github.com/loteran> - 1.0.6-1
- Add Debian/Ubuntu packaging (.deb + Launchpad PPA)

* Mon Mar 30 2026 loteran <https://github.com/loteran> - 1.0.5-1
- Fix desktop entry for Fedora desktop-file-validate
- Add remote icon and AppStream metainfo for package manager GUIs
- Widen uv_build version range
- Add COPR packaging for Fedora

* Sun Mar 29 2026 loteran <https://github.com/loteran> - 1.0.4-1
- External output EQ, volume control, stream routing
- Update notifications and clean exit
- ANC/Transparent mode restore on device init

* Sun Mar 29 2026 loteran <https://github.com/loteran> - 1.0.0-1
- Initial release
