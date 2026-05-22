{
  description = "Arctis Sound Manager — Nix package + NixOS module (SteelSeries headset control over PipeWire)";

  # Pinned to the same nixpkgs revision as nix/sources.nix (the classic path).
  # Keep flake.lock in sync when bumping.
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/0c88e1f2bdb93d5999019e99cb0e61e1fe2af4c5";

  outputs =
    { self, nixpkgs }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      forAllSystems = nixpkgs.lib.genAttrs systems;
      pkgsFor = system: nixpkgs.legacyPackages.${system};
    in
    {
      packages = forAllSystems (
        system:
        let
          arctis-sound-manager = (pkgsFor system).callPackage ./package.nix { };
        in
        {
          inherit arctis-sound-manager;
          default = arctis-sound-manager;
        }
      );

      devShells = forAllSystems (system: {
        default = import ./shell.nix { pkgs = pkgsFor system; };
      });

      overlays.default = final: _prev: {
        arctis-sound-manager = final.callPackage ./package.nix { };
      };

      # Flake users get the flake's pinned package by default; classic importers
      # of ./module.nix get nix/default.nix (same pin).
      nixosModules.default =
        { pkgs, lib, ... }:
        {
          imports = [ ./module.nix ];
          services.arctis-sound-manager.package =
            lib.mkDefault
              self.packages.${pkgs.stdenv.hostPlatform.system}.default;
        };
      nixosModules.arctis-sound-manager = self.nixosModules.default;

      formatter = forAllSystems (system: (pkgsFor system).nixfmt-rfc-style);
    };
}
