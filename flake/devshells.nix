# Development shell for python projects. This is meant to be used alongside a native python package manager.
# Uses nix-ld to make specified dynamic libraries discoverable by the linker (required by some python packages, e.g. numpy)
{
  lib,
  inputs,
  ...
}: {
  perSystem = {
    pkgs,
    system,
    ...
  }: {
    devShells = let
      PYTHON_VERSION = "3.10.1";
      pkgs-python = inputs.nixpkgs-python.packages.${system};
    in {
      default = pkgs.mkShell {
        # general packages to install
        packages = [
          (
            pkgs.python3.withPackages (p: [
              p.tkinter
              p.beautifulsoup4
              p.playwright
              p.pillow
              p.pyside6
            ])
          )
          pkgs.uv
          pkgs.playwright-driver.browsers
          # ... <- add nix packages here
        ];

        # packages with depended-on dynamic libraries
        NIX_LD_LIBRARY_PATH = lib.makeLibraryPath [
          pkgs.stdenv.cc.cc
          pkgs.zstd
          pkgs.glib
          pkgs.libGL
          pkgs.fontconfig
          pkgs.libx11
          pkgs.libxkbcommon
          pkgs.freetype
          pkgs.dbus
          pkgs.wayland

          pkgs.nss
          pkgs.nspr
          pkgs.at-spi2-atk
          pkgs.cups
          pkgs.libdrm
          pkgs.expat
          pkgs.libxcb
          pkgs.libxcomposite
          pkgs.libxdamage
          pkgs.libxext
          pkgs.libxfixes
          pkgs.libxrandr
          pkgs.libgbm
          pkgs.pango
          pkgs.cairo
          pkgs.eudev
          pkgs.alsa-lib

          pkgs.krb5
          pkgs.brotli

          pkgs.zlib
          # ... <- add nix packages with depended-on dynamic libraries here
        ];

        NIX_LD = lib.fileContents "${pkgs.stdenv.cc}/nix-support/dynamic-linker";
        shellHook = ''
          # force the use of the ld wrapper provided by nix-ld even for python interpreters patched for nix
          export LD_LIBRARY_PATH=$NIX_LD_LIBRARY_PATH

          # export PLAYWRIGHT_BROWSERS_PATH=${pkgs.playwright-driver.browsers}
        '';
      };
    };
  };
}
