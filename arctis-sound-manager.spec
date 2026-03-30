Name:           arctis-sound-manager
Version:        1.0.5
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
Requires:       python3-dbus-next
Requires:       python3-pulsectl
Requires:       python3-pyudev
Requires:       python3-pyusb
Requires:       python3-ruamel-yaml
Requires:       pipewire
Requires:       pipewire-pulseaudio
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
SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTRS{idVendor}=="1038", ATTRS{idProduct}=="1280", MODE="0666", TAG+="uaccess"
SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTRS{idVendor}=="1038", ATTRS{idProduct}=="1290", MODE="0666", TAG+="uaccess"
LABEL="local_end"
RULES

# Systemd user services
install -Dm644 /dev/stdin %{buildroot}%{_userunitdir}/arctis-manager.service <<'SERVICE'
[Unit]
Description=Arctis Sound Manager
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

# Device configs
install -d %{buildroot}%{_datadir}/%{name}/devices
install -Dm644 src/arctis_sound_manager/devices/*.yaml \
    -t %{buildroot}%{_datadir}/%{name}/devices/

%post
%systemd_user_post arctis-manager.service arctis-video-router.service
udevadm control --reload-rules || :

%preun
%systemd_user_preun arctis-manager.service arctis-video-router.service

%postun
%systemd_user_postun arctis-manager.service arctis-video-router.service

%files
%license LICENSE
%doc README.md CHANGELOG.md
%{python3_sitelib}/arctis_sound_manager/
%{python3_sitelib}/arctis_sound_manager-*.dist-info/
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

%changelog
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
