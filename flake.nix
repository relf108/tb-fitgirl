{
  description = "Find, cache (TorBox), download and install FitGirl repacks on Linux via Proton";

  inputs.nixpkgs.url = "https://flakehub.com/f/NixOS/nixpkgs/0.1.*.tar.gz";

  outputs = { self, nixpkgs }:
    let
      supportedSystems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forEachSupportedSystem = f: nixpkgs.lib.genAttrs supportedSystems (system: f {
        pkgs = import nixpkgs { inherit system; };
      });
    in
    {
      # Installable package: the tb-fitgirl CLI + bridge (python -m
      # tb_fitgirl.bridge). Add this flake as an input to your NixOS config
      # and put `packages.${system}.default` in environment.systemPackages.
      # Runtime expectations: a Steam install with a Proton runtime; on
      # NixOS, steam-run on PATH (picked up automatically if present).
      packages = forEachSupportedSystem ({ pkgs }:
        let
          python = pkgs.python314;

          tb-fitgirl = python.pkgs.buildPythonApplication {
            pname = "tb-fitgirl";
            version = "0.1.0";
            pyproject = true;
            src = self;

            build-system = [ python.pkgs.setuptools ];
            dependencies = with python.pkgs; [
              httpx
              beautifulsoup4
            ];

            nativeCheckInputs = with python.pkgs; [
              pytestCheckHook
              respx
            ];
            # The installer tests create ~/.tb-fitgirl/wine; the sandbox's
            # HOME (/homeless-shelter) isn't writable.
            preCheck = ''
              export HOME=$(mktemp -d)
            '';

            # steam_running() shells out to pgrep.
            makeWrapperArgs = [
              "--prefix" "PATH" ":" (pkgs.lib.makeBinPath [ pkgs.procps ])
            ];

            # A launcher for the GUI's stdio bridge (mirrors bridge.py's
            # __main__ block incl. the process-group setup for cancel).
            # Cleaner as a [project.scripts] entry eventually; kept at the
            # packaging layer for now. wrapPythonPrograms gives it the same
            # environment as the CLI.
            postInstall = ''
              cat > $out/bin/tb-fitgirl-bridge <<'EOF'
              #!${python.interpreter}
              import os
              import sys

              from tb_fitgirl.bridge import main

              if __name__ == "__main__":
                  try:
                      os.setpgid(0, 0)
                  except OSError:
                      pass
                  sys.exit(main())
              EOF
              sed -i 's/^              //' $out/bin/tb-fitgirl-bridge
              chmod +x $out/bin/tb-fitgirl-bridge
            '';

            meta = {
              description = "Find, cache (TorBox), download and install FitGirl repacks on Linux via Proton";
              mainProgram = "tb-fitgirl";
            };
          };

          # Flutter Linux desktop front-end.  Only defined (and evaluated) on
          # Linux; on Darwin the rec-level `default` falls back to tb-fitgirl.
          tb-fitgirl-gui = pkgs.flutter.buildFlutterApplication {
            pname = "tbfg-gui";
            version = "0.1.0";
            src = "${self}/gui";

            # autoPubspecLock reads pubspec.lock via IFD (fine outside nixpkgs).
            autoPubspecLock = "${self}/gui/pubspec.lock";
            # Obtain the real hash by running `nix build .#tb-fitgirl-gui` and
            # replacing this placeholder with the value from the error message.
            vendorHash = pkgs.lib.fakeHash;

            nativeBuildInputs = with pkgs; [ cmake ninja clang pkg-config makeWrapper ];
            buildInputs = with pkgs; [ gtk3 libsecret ];

            # Put tb-fitgirl-bridge on PATH so the GUI can spawn it via stdio.
            postInstall = ''
              wrapProgram $out/bin/tbfg_gui \
                --prefix PATH : ${pkgs.lib.makeBinPath [ tb-fitgirl ]}
            '';

            meta = {
              description = "Flutter GUI for tb-fitgirl";
              mainProgram = "tbfg_gui";
              platforms = pkgs.lib.platforms.linux;
            };
          };
        in
        {
          inherit tb-fitgirl;
          # On Linux the GUI is the primary entry-point; on Darwin fall back to
          # the CLI (which works fine without a display).
          default = if pkgs.stdenv.isLinux then tb-fitgirl-gui else tb-fitgirl;
        } // pkgs.lib.optionalAttrs pkgs.stdenv.isLinux {
          inherit tb-fitgirl-gui;
        });

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
