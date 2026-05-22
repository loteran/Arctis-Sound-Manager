# Classic (non-flakes) entry point: `nix-build nix` builds the package against
# the pinned nixpkgs in sources.nix.
#
#   nix-build nix                       # build with the pinned nixpkgs
#   nix-build nix --arg pkgs 'import <nixpkgs> {}'   # or against your channel
{
  pkgs ? import (import ./sources.nix).nixpkgs { },
}:
pkgs.callPackage ./package.nix { }
