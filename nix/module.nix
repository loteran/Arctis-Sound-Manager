# NixOS module for Arctis Sound Manager.
#
# Reproduces, declaratively, what the upstream `asm-setup` does on a regular
# distro — udev rules, the daemon / media-router services, and the LADSPA
# plugins the Sonar EQ needs — so the headset works after every reboot without a
# container and without scrubbing the Qt environment by hand.
#
# It does NOT define its own filter-chain service: PipeWire already ships
# `filter-chain.service` (runs `pipewire -c filter-chain.conf`, which loads
# `~/.config/pipewire/filter-chain.conf.d/` — exactly where the ASM daemon writes
# the Sonar EQ + HeSuVi surround configs). We just add the Sonar LADSPA plugins
# to it and make sure it's started.
#
# Usage — in your flake's nixosSystem modules (or imports for a non-flake config):
#
#   services.pipewire.enable = true;            # + alsa/pulse as you like
#   services.arctis-sound-manager.enable = true;
#
# then rebuild. The system tray launches on login; replug the headset once after
# the first switch so udev applies the new rules.
{
  config,
  lib,
  pkgs,
  options,
  ...
}:

let
  cfg = config.services.arctis-sound-manager;

  userService =
    extra:
    lib.recursiveUpdate {
      wantedBy = [ "graphical-session.target" ];
      partOf = [ "graphical-session.target" ];
      serviceConfig = {
        Restart = "on-failure";
        RestartSec = 5;
      };
    } extra;

  # The Sonar EQ / surround filter-chain loads these LADSPA plugins by name:
  #   sc4m_1916 (Smart Volume + mic compressor), plate_1423 (Distance reverb),
  #   gate_1410 (mic noise gate)  → swh-plugins (pkgs.ladspaPlugins)
  #   librnnoise_ladspa (mic noise cancellation) → pkgs.rnnoise-plugin
  #
  # Newer nixpkgs expose `services.pipewire.extraLadspaPackages`, which feeds the
  # filter-chain service's LADSPA_PATH directly. Older releases lack it (their
  # filter-chain service only sets LV2_PATH); there we set LADSPA_PATH on the
  # service ourselves — no conflict, since those versions don't define it.
  pipewireHasLadspaOption = options.services.pipewire ? extraLadspaPackages;

  # One directory holding all configured LADSPA plugins, symlinked to
  # /usr/lib/ladspa below. ASM's GUI dependency checker scans only the FHS dirs
  # (/usr/lib/ladspa, /usr/lib64/ladspa, …) and ignores $LADSPA_PATH, so on NixOS
  # it falsely reports plate_1423 / rnnoise "missing" even though they're on the
  # filter-chain's path. This makes the checker pass; it's also a default LADSPA
  # search dir, so the filter-chain finds them regardless.
  ladspaEnv = pkgs.buildEnv {
    name = "asm-ladspa-plugins";
    paths = cfg.ladspaPlugins;
    pathsToLink = [ "/lib/ladspa" ];
  };
in
{
  options.services.arctis-sound-manager = {
    enable = lib.mkEnableOption "Arctis Sound Manager (SteelSeries headset control over PipeWire)";

    package = lib.mkOption {
      type = lib.types.package;
      default = import ./default.nix { };
      defaultText = lib.literalExpression "import ./default.nix { } (built from the pinned nixpkgs in nix/sources.nix)";
      description = "The arctis-sound-manager package to use.";
    };

    ladspaPlugins = lib.mkOption {
      type = lib.types.listOf lib.types.package;
      default = [
        pkgs.ladspaPlugins
        pkgs.rnnoise-plugin
      ];
      defaultText = lib.literalExpression "[ pkgs.ladspaPlugins pkgs.rnnoise-plugin ]";
      description = ''
        LADSPA plugin packages added to the PipeWire filter-chain's LADSPA_PATH.
        The Sonar EQ uses sc4m_1916 / plate_1423 / gate_1410 (swh-plugins) and
        librnnoise_ladspa (rnnoise). Override only if you ship them elsewhere.
      '';
    };

    autostartTray = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = "Launch the system-tray GUI (`asm-gui --systray`) on login.";
    };
  };

  config = lib.mkIf cfg.enable (
    lib.mkMerge [
      {
        assertions = [
          {
            assertion = config.services.pipewire.enable;
            message = ''
              services.arctis-sound-manager requires PipeWire.
              Set services.pipewire.enable = true; (with pulse/alsa support as desired).
            '';
          }
        ];

        environment.systemPackages = [ cfg.package ];

        # USB HID access for every Arctis PID (no manual /etc/udev write needed).
        services.udev.packages = [ cfg.package ];

        # ASM's GUI dependency checker scans FHS LADSPA dirs and ignores
        # $LADSPA_PATH, so expose the Sonar plugins at /usr/lib/ladspa
        # (plate_1423 / rnnoise). It's also a default LADSPA search dir.
        #
        # We deliberately do NOT symlink /usr/share/arctis-sound-manager: that
        # would let `asm-setup` copy read-only store files into ~/.config, which
        # the daemon then cannot rewrite (issue #23 territory). The daemon
        # generates every PipeWire config itself, so asm-setup isn't needed here.
        systemd.tmpfiles.rules = [
          "L+ /usr/lib/ladspa - - - - ${ladspaEnv}/lib/ladspa"
        ];

        systemd.user.services = {
          # Start PipeWire's own filter-chain service so the Sonar EQ + HeSuVi
          # configs in ~/.config/pipewire/filter-chain.conf.d/ load at login (the
          # ASM daemon restarts it by this exact name when applying EQ changes).
          filter-chain.wantedBy = [ "default.target" ];

          arctis-manager = userService {
            description = "Arctis Sound Manager device daemon";
            after = [
              "pipewire.service"
              "pipewire-pulse.service"
            ];
            wants = [ "pipewire.service" ];
            serviceConfig.ExecStart = "${cfg.package}/bin/asm-daemon";
          };

          arctis-video-router = userService {
            description = "Arctis Sound Manager media auto-router";
            after = [
              "pipewire.service"
              "arctis-manager.service"
            ];
            serviceConfig.ExecStart = "${cfg.package}/bin/asm-router";
          };
        }
        // lib.optionalAttrs cfg.autostartTray {
          arctis-gui = userService {
            description = "Arctis Sound Manager tray GUI";
            after = [
              "graphical-session.target"
              "arctis-manager.service"
            ];
            serviceConfig.ExecStart = "${cfg.package}/bin/asm-gui --systray --no-enforce-systemd";
          };
        };
      }

      # Feed the Sonar LADSPA plugins into PipeWire's filter-chain service.
      (
        if pipewireHasLadspaOption then
          { services.pipewire.extraLadspaPackages = cfg.ladspaPlugins; }
        else
          {
            systemd.user.services.filter-chain.environment.LADSPA_PATH =
              lib.makeSearchPath "lib/ladspa" cfg.ladspaPlugins;
          }
      )
    ]
  );
}
