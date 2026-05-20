# Arctis Sound Manager packaged for Nix.
#
# Builds the *unmodified* upstream source (src/ layout, uv_build backend) and
# wraps every entry point so it runs natively on NixOS:
#   - Qt plugin paths are baked into the wrappers → no more `unset QT_PLUGIN_PATH …`.
#   - libusb / libudev are on LD_LIBRARY_PATH for the pyusb / pyudev ctypes backends.
#   - pactl, pw-*, wpctl, pgrep, systemctl, curl are on PATH so the runtime
#     config generator and "restart filter-chain" actions work.
#
# callPackage-style: usable from both the flake and the classic default.nix.
{
  lib,
  python3Packages,
  qt6,
  libusb1,
  systemd, # libudev (pyudev) + systemctl/udevadm
  pipewire, # pw-metadata / pw-dump / pw-cli
  wireplumber, # wpctl
  pulseaudio, # pactl (client tool; not shipped by pipewire on NixOS)
  procps, # pgrep (asm-router singleton guard)
  coreutils,
  curl, # HRIR download in asm-setup
}:

let
  # Only the files the wheel + packaging steps need. Keeps the build input small
  # and avoids rebuilds when unrelated repo files (docs, top-level hrir/, aur/…)
  # change. The 58 selectable HRIR profiles live under src/.../hrir_assets and
  # are included via ../src.
  src = lib.fileset.toSource {
    root = ../.;
    fileset = lib.fileset.unions [
      ../pyproject.toml
      ../README.md
      ../LICENSE
      ../src
      ../scripts/generate_udev_rules.py
    ];
  };

  # A tiny python that can run the udev-rule generator at build time (it needs
  # ruamel.yaml to parse the device YAMLs). Single source of truth: identical
  # output to `asm-cli udev dump-rules`.
  udevGenPython = python3Packages.python.withPackages (ps: [ ps.ruamel-yaml ]);
in
python3Packages.buildPythonApplication {
  pname = "arctis-sound-manager";
  version = (builtins.fromTOML (builtins.readFile ../pyproject.toml)).project.version;
  pyproject = true;
  inherit src;

  build-system = [ python3Packages.uv-build ];

  # nixpkgs 25.11 ships these a hair below the pyproject floor (pyside6 6.10.0
  # vs .1, pyudev 0.24.3 vs .4, ruamel 0.18.14 vs 0.19.1). The differences are
  # functionally irrelevant for ASM's usage; relax the bounds rather than
  # patching upstream.
  pythonRelaxDeps = [
    "pyside6"
    "pyudev"
    "ruamel-yaml"
  ];

  dependencies = with python3Packages; [
    babel
    dbus-next
    pillow
    pulsectl
    pyside6
    pyudev
    pyusb
    ruamel-yaml
  ];

  # nixpkgs 25.11 ships uv-build 0.9.7; pyproject pins the backend to
  # >=0.9.17. Lower the floor on the sandbox copy only — the repo's
  # pyproject.toml is untouched. (Build-system requires are verified by
  # `python -m build`, so pythonRelaxDeps can't cover this one.)
  postPatch = ''
    substituteInPlace pyproject.toml \
      --replace-fail 'uv_build>=0.9.17,<0.12' 'uv_build>=0.9.7'
  '';

  # We wrap the Python entry points ourselves (makeWrapperArgs below), so tell
  # the qt6 stdenv guard not to expect wrapQtAppsHook. Without this, depending
  # on qtbase fails with "no wrapping behavior was specified".
  dontWrapQtApps = true;

  # Qt plugin dirs are wired in explicitly via makeWrapperArgs below, so the
  # buildInputs here exist mainly to pull qtbase/qtwayland/qtsvg into the
  # runtime closure (referenced by those wrapper paths).
  buildInputs = [
    libusb1
    qt6.qtbase # platform (xcb) + base plugins
    qt6.qtwayland # Wayland QPA platform plugin (user is on KDE Wayland)
    qt6.qtsvg # imageformat plugin — ASM's UI icons are SVG
  ];

  # Bake everything the Python entry points need straight into their wrappers:
  #   - QT_PLUGIN_PATH / QML2_IMPORT_PATH → the nixpkgs qt6 plugin dirs. This is
  #     the fix for the host Qt env bleed (a wrong inherited QT_PLUGIN_PATH is
  #     what crashes the distrobox PySide6); --prefix puts the correct dirs
  #     first no matter what the session exports.
  #   - LD_LIBRARY_PATH → libusb (pyusb) + libudev (pyudev) ctypes backends.
  #   - PATH → pw-* / wpctl / pgrep / systemctl / curl used at runtime.
  #
  # Set as Nix-level strings rather than via wrapQtAppsHook's qtWrapperArgs: the
  # hook only wraps ELF binaries (it skips the Python entry points), and the
  # usual `makeWrapperArgs+=("''${qtWrapperArgs[@]}")` merge silently drops the
  # Qt entries here because, without structuredAttrs, makeWrapperArgs is a
  # scalar string and the array append mangles it.
  makeWrapperArgs = [
    "--prefix QT_PLUGIN_PATH : ${
      lib.makeSearchPath qt6.qtbase.qtPluginPrefix [
        qt6.qtbase
        qt6.qtwayland
        qt6.qtsvg
      ]
    }"
    "--prefix QML2_IMPORT_PATH : ${lib.makeSearchPath qt6.qtbase.qtQmlPrefix [ qt6.qtwayland ]}"
    "--prefix LD_LIBRARY_PATH : ${
      lib.makeLibraryPath [
        libusb1
        systemd
      ]
    }"
    "--prefix PATH : ${
      lib.makeBinPath [
        pipewire
        wireplumber
        pulseaudio
        procps
        coreutils
        curl
        systemd
      ]
    }"
  ];

  # Don't run the test suite here: tests import pulsectl/PySide6 against a live
  # PipeWire + display that the sandbox doesn't have.
  doCheck = false;
  pythonImportsCheck = [ "arctis_sound_manager" ];

  postInstall = ''
    # udev rules generated from the device YAMLs (covers every Arctis PID,
    # including Nova Pro Wireless 0x12e0 / 0x12e5). services.udev.packages in
    # the NixOS module picks these up from $out/lib/udev/rules.d.
    install -d "$out/lib/udev/rules.d"
    ${udevGenPython}/bin/python scripts/generate_udev_rules.py \
      src/arctis_sound_manager/devices \
      > "$out/lib/udev/rules.d/91-steelseries-arctis.rules"

    # Desktop entry + icon
    install -Dm644 src/arctis_sound_manager/desktop/ArctisManager.desktop \
      "$out/share/applications/ArctisManager.desktop"
    substituteInPlace "$out/share/applications/ArctisManager.desktop" \
      --replace-fail "Exec=asm-gui" "Exec=$out/bin/asm-gui"
    install -Dm644 src/arctis_sound_manager/gui/images/steelseries_logo.svg \
      "$out/share/icons/hicolor/scalable/apps/arctis-manager.svg"
  '';

  passthru.updateScript = null;

  meta = {
    description = "Linux GUI for SteelSeries Arctis headsets — mixer, per-channel Sonar EQ and HeSuVi 7.1 surround over PipeWire";
    homepage = "https://github.com/loteran/Arctis-Sound-Manager";
    license = lib.licenses.gpl3Only;
    mainProgram = "asm-gui";
    platforms = lib.platforms.linux;
  };
}
