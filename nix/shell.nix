# Classic dev shell: `nix-shell nix` drops you into an environment where the
# wrapped asm-* tools are on PATH (Qt env correct, libusb/libudev present), plus
# uv for working on the upstream source.
#
#   nix-shell nix
#   asm-gui            # runs the built GUI, no `unset QT_PLUGIN_PATH …` needed
{
  pkgs ? import (import ./sources.nix).nixpkgs { },
}:
let
  asm = pkgs.callPackage ./package.nix { };
in
pkgs.mkShell {
  packages = [
    asm
    pkgs.uv
    pkgs.python3
  ];

  shellHook = ''
    echo "Arctis Sound Manager dev shell (${asm.version})"
    echo "  asm-gui / asm-daemon / asm-cli / asm-router / asm-setup are on PATH."
  '';
}
