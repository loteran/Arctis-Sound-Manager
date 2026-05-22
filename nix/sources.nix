# Pinned dependencies for the classic (non-flakes) entry points.
#
# This is the single source of truth for `default.nix`, `shell.nix` and the
# NixOS module's default package, so a system with flakes *disabled* still gets
# a fully reproducible build. The flake (`flake.nix`) pins the same revision via
# its own input, kept in sync by `flake.lock`.
#
# Revision: nixos-25.11 channel (0c88e1f2bdb9), the version this was developed
# and test-built against. Bump `rev` + `sha256` together; get the new hash with:
#   nix-prefetch-url --unpack \
#     "https://github.com/NixOS/nixpkgs/archive/<rev>.tar.gz" --name source
{
  nixpkgs = fetchTarball {
    name = "nixpkgs-25.11-arctis";
    url = "https://github.com/NixOS/nixpkgs/archive/0c88e1f2bdb93d5999019e99cb0e61e1fe2af4c5.tar.gz";
    sha256 = "004fbmvifmsbx4fx7ah5ichvj8ki6xlqajlsin5qq77dn0lf9ydb";
  };
}
