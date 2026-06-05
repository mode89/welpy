{ pkgs ? import <nixpkgs> {} }:

pkgs.mkShell {
  packages =
    (import ./deps.nix pkgs) ++
    (with pkgs; [
      dwl
      firefox
      foot
      gedit
      mate.mate-terminal
      python3Packages.ipython
      swaybg
    ]);
}
