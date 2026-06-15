{ pkgs ? import <nixpkgs> {} }:

pkgs.mkShell {
  packages =
    (import ./deps.nix pkgs) ++
    (with pkgs; [
      python3Packages.pytest
      python3Packages.pylint
      dwl
      firefox
      foot
      gedit
      mate-terminal
      python3Packages.ipython
      swaybg
    ]);
}
