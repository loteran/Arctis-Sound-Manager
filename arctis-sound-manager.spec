Name:           arctis-sound-manager
Version:        1.0.34
Release:        1%{?dist}
Summary:        Linux GUI for SteelSeries Arctis headsets

License:        GPL-3.0-or-later
URL:            https://github.com/loteran/Arctis-Sound-Manager
Source0:        %{url}/archive/refs/tags/v%{version}.tar.gz
Source1:        arctis_sound_manager-%{version}-py3-none-any.whl

BuildArch:      noarch
BuildRequires:  python3-devel
BuildRequires:  python3-pip
BuildRequires:  systemd-rpm-macros
BuildRequires:  desktop-file-utils
BuildRequires:  libappstream-glib

Requires:       python3-pyside6
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
Recommends:     swh-plugins

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
pip3 install --root=%{buildroot} --prefix=/usr --no-deps --no-build-isolation %{SOURCE1}

# Bundle dbus-next (not in Fedora repos)
pip3 install --root=%{buildroot} --prefix=/usr --no-deps dbus-next

# udev rules
install -Dm644 /dev/stdin %{buildroot}%{_udevrulesdir}/91-steelseries-arctis.rules <<'RULES'
ACTION=="remove", GOTO="local_end"
SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTRS{idVendor}=="1038", ATTRS{idProduct}=="1260|12ad", MODE="0666", TAG+="uaccess"
SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTRS{idVendor}=="1038", ATTRS{idProduct}=="220e|2212|2216|2236", MODE="0666", TAG+="uaccess"
SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTRS{idVendor}=="1038", ATTRS{idProduct}=="12ec", MODE="0666", TAG+="uaccess"
SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTRS{idVendor}=="1038", ATTRS{idProduct}=="2232|2253", MODE="0666", TAG+="uaccess"
SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTRS{idVendor}=="1038", ATTRS{idProduct}=="220a", MODE="0666", TAG+="uaccess"
SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTRS{idVendor}=="1038", ATTRS{idProduct}=="12d7", MODE="0666", TAG+="uaccess"
SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTRS{idVendor}=="1038", ATTRS{idProduct}=="12c2", MODE="0666", TAG+="uaccess"
SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTRS{idVendor}=="1038", ATTRS{idProduct}=="12e0|12e5", MODE="0666", TAG+="uaccess"
SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTRS{idVendor}=="1038", ATTRS{idProduct}=="12cb|12cd", MODE="0666", TAG+="uaccess"
SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTRS{idVendor}=="1038", ATTRS{idProduct}=="1280", MODE="0666", TAG+="uaccess"
SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTRS{idVendor}=="1038", ATTRS{idProduct}=="1290", MODE="0666", TAG+="uaccess"
LABEL="local_end"
RULES

# Systemd user services
install -Dm644 /dev/stdin %{buildroot}%{_userunitdir}/arctis-manager.service <<'SERVICE'
[Unit]
Description=Arctis Sound Manager
After=pipewire.service pipewire-pulse.service
Wants=pipewire.service
StartLimitInterval=1min
StartLimitBurst=5

[Service]
Type=simple
ExecStart=%{_bindir}/asm-daemon
Restart=on-failure
RestartSec=5

[Install]
WantedBy=graphical-session.target
SERVICE

install -Dm644 /dev/stdin %{buildroot}%{_userunitdir}/arctis-video-router.service <<'SERVICE'
[Unit]
Description=Arctis Sound Manager — Media Router
After=pipewire.service arctis-manager.service
Requires=pipewire.service

[Service]
ExecStart=%{_bindir}/asm-router
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
SERVICE

# Desktop entry
install -Dm644 src/arctis_sound_manager/desktop/ArctisManager.desktop \
    %{buildroot}%{_datadir}/applications/ArctisManager.desktop
desktop-file-validate %{buildroot}%{_datadir}/applications/ArctisManager.desktop

# Icon
install -Dm644 src/arctis_sound_manager/gui/images/steelseries_logo.svg \
    %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/arctis-manager.svg

# AppStream metainfo
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
%systemd_user_post arctis-manager.service arctis-video-router.service
udevadm control --reload-rules || :
udevadm trigger || :

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
%systemd_user_preun arctis-manager.service arctis-video-router.service

%postun
%systemd_user_postun arctis-manager.service arctis-video-router.service

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
%{_datadir}/applications/ArctisManager.desktop
%{_datadir}/icons/hicolor/scalable/apps/arctis-manager.svg
%{_metainfodir}/com.github.loteran.arctis-sound-manager.metainfo.xml
%{_datadir}/%{name}/
/etc/xdg/autostart/asm-first-run.desktop

%changelog
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
