{ pkgs ? import <nixpkgs> {} }:

pkgs.mkShell {
  packages =
    (import ./deps.nix pkgs) ++
    (with pkgs; [
      dwl
      foot
      python3Packages.ipython
    ]);
}
