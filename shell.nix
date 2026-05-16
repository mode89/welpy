{ pkgs ? import <nixpkgs> {} }:

pkgs.mkShell {
  packages =
    (import ./deps.nix pkgs) ++
    (with pkgs; [
      dwl
      foot
      gobject-introspection
      gtk4
      python3Packages.pygobject3
      xpra
    ]);
}
