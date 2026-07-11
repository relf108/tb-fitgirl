{
  description = "A Nix-flake-based Python development environment for tb-fitgirl";

  inputs.nixpkgs.url = "https://flakehub.com/f/NixOS/nixpkgs/0.1.*.tar.gz";

  outputs = { self, nixpkgs }:
    let
      supportedSystems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forEachSupportedSystem = f: nixpkgs.lib.genAttrs supportedSystems (system: f {
        pkgs = import nixpkgs { inherit system; };
      });
    in
    {
      devShells = forEachSupportedSystem ({ pkgs }:
        let
          python = pkgs.python314;
          pythonEnv = python.withPackages (ps: with ps; [
            httpx
            beautifulsoup4
            pytest
            respx
          ]);
        in
        {
          default = pkgs.mkShell {
            packages = [
              pythonEnv
              pkgs.ruff
              # Flutter GUI (gui/): SDK + Linux desktop build deps
              pkgs.flutter
              pkgs.cmake
              pkgs.ninja
              pkgs.clang
              pkgs.pkg-config
            ] ++ pkgs.lib.optionals pkgs.stdenv.isLinux [
              pkgs.gtk3
              pkgs.libsecret # secret-tool, used by the GUI for API key storage
            ];
            shellHook = ''
              export PYTHONPATH="$PWD/src''${PYTHONPATH:+:$PYTHONPATH}"
            '';
          };
        });
    };
}
