# tb-fitgirl

Find FitGirl repacks, cache them on [TorBox](https://torbox.app), download,
and install them on Linux via Proton — added to both Steam and your
application launcher.

Sources are pluggable ([fitgirl-repacks.site](https://fitgirl-repacks.site)
by default).

## Setup

Runtime expectations for all install methods: a Steam install with at least
one Proton runtime. On NixOS, `steam-run` on PATH is picked up automatically.

### AppImage (recommended for non-NixOS)

One file with the GUI plus frozen CLI/bridge (no host Python):

```sh
chmod +x tb-fitgirl-*-x86_64.AppImage
./tb-fitgirl-*-x86_64.AppImage                      # GUI
./tb-fitgirl-*-x86_64.AppImage --cli search "..."   # CLI
./tb-fitgirl-*-x86_64.AppImage --bridge             # JSON-lines bridge
```

Build it yourself (needs Flutter, Python 3.10+ with venv/pip, a C compiler
for the process-group wrapper, and network on first run for pip +
appimagetool; freezes the Python side with PyInstaller so the AppImage has
no host-Python dependency):

```sh
make appimage    # -> dist/tb-fitgirl-<version>-x86_64.AppImage
```

The GUI stores your TorBox API key in the OS keyring (`secret-tool`); set
`TORBOX_API_KEY` in the environment as a read-only fallback.

### pipx / uv (CLI only)

```sh
pipx install git+https://github.com/relf108/tb-fitgirl.git
# or from a checkout:  pipx install .
export TORBOX_API_KEY=<your-key>   # from torbox.app/settings
tb-fitgirl search "pragmata"
```

### Dev shell (Nix + direnv)

```sh
direnv allow                              # Python + Flutter + deps
echo 'TORBOX_API_KEY=<your-key>' > .env
```

### Installing on NixOS

The flake exposes the CLI, `tb-fitgirl-bridge`, and the GUI as installable
packages. In your NixOS config flake:

```nix
inputs.tb-fitgirl.url = "github:relf108/tb-fitgirl";

# in your system configuration:
environment.systemPackages = [
  inputs.tb-fitgirl.packages.${pkgs.system}.default
];
```

Set `TORBOX_API_KEY` in the environment (or pass `api_key` per bridge
request).

## Usage

Installed commands are `tb-fitgirl` and `tb-fitgirl-bridge`. From a checkout
you can use `python -m tb_fitgirl.cli` / `python -m tb_fitgirl.bridge`
instead (same ops).

```sh
# Search a repack source and show TorBox cache status
tb-fitgirl search "pragmata" [--limit N] [--source fitgirl]

# Cache a repack on TorBox (scrapes the magnet by title, or takes a magnet URI)
tb-fitgirl cache "pragmata"
tb-fitgirl cache "magnet:?xt=urn:btih:..." [--only-if-cached]

# Download from your TorBox account (auto-caches first if needed)
tb-fitgirl download "pragmata"            # -> ~/TBFGames
tb-fitgirl download "pragmata" --dest /mnt/games

# Install: auto-downloads if not present, unpacks via Proton, adds to Steam
# and the app launcher. Close Steam first (it rewrites shortcuts on exit).
tb-fitgirl install "pragmata"

# Add an already-installed game to Steam + launcher (no reinstall)
tb-fitgirl steam-add "pragmata"

# Uninstall: remove game files, Steam shortcut, and launcher entry
tb-fitgirl uninstall "pragmata"
tb-fitgirl uninstall "pragmata" --keep-files   # shortcuts only
```

After installing, set the Proton version in Steam
(Properties > Compatibility) — non-Steam shortcuts don't inherit a default.

### Useful install flags

- `--no-download` — fail instead of downloading if not present locally
- `--runtime {proton,wine}` / `--proton NAME` — runtime selection
  (Proton is the reliable default; raw Wine hangs on FitGirl's unpacker)
- `--gui` — run the installer UI instead of silent
- `--no-steam` / `--no-app-menu` — skip the respective shortcut
- `--no-verify` — skip MD5 verification

## GUI (Flutter)

A Flutter desktop front-end lives in `gui/`, wrapping the same pipeline via
a JSON-lines stdio bridge (`python -m tb_fitgirl.bridge`). All TorBox/Proton
logic stays in Python; Flutter is presentation only.

```sh
make gui-run     # generate the Linux runner (one-off) + flutter run
make gui-test    # flutter analyze + widget tests
make gui-build   # release build -> gui/build/linux/.../tbfg_gui
make appimage    # single-file AppImage (GUI + CLI + bridge) -> dist/
```

- On first run it prompts for your TorBox API key, validates it, and stores
  it in the OS keyring via `secret-tool` (never a file). `TORBOX_API_KEY`
  in the environment works as a read-only fallback.
- The GUI prefers `tb-fitgirl-bridge` on PATH (Nix/AppImage install), then
  falls back to a dev checkout via `src/tb_fitgirl/bridge.py` near the
  working directory or executable. Set `TBFG_BACKEND=/path/to/checkout`
  (and optionally `TBFG_PYTHON`) when running a bare `flutter build` binary
  outside the checkout.
- All direct dependencies are SDK-provided (no explicit hosted packages);
  the first `pub get` only resolves Flutter's own pinned deps.

The bridge protocol (for other front-ends): one JSON request per line on
stdin (`{"id", "op", "args"}`), progress/result/error events per line on
stdout with `data: {phase, done, total, rate, message}` progress payloads.
Cancellation = kill the bridge's process group.

## Adding a scraper

1. Subclass `tb_fitgirl.scrapers.base.Scraper`; implement `search()` and
   `fetch_magnets()`, set a unique `name`
2. Register the class in `SCRAPERS` in `src/tb_fitgirl/scrapers/__init__.py`

It's then available via `--source <name>`.

## Development

```sh
make qa      # ruff check + format --check
make test    # pytest (HTTP mocked)
make check   # both
```

## Notes

- **TorBox**: `createtorrent` success doesn't indicate cache state; we
  `checkcached` first. Uncached adds are limited to 60/hour. The
  `search-api.torbox.app` endpoint is plan-gated to zero on this account,
  so repacks are found by scraping instead.
- **Install runtime**: FitGirl's srep/lolz/precomp unpacker is Windows-only,
  so a Wine/Proton runtime is required — there's no native Linux unpack.
  Proton + the Steam Linux Runtime is used; on NixOS it's wrapped in
  `steam-run` automatically (no-op elsewhere). Silent installs use `/SILENT`
  (not `/VERYSILENT`, which deadlocks the unpacker) and stop once the game
  exe appears, skipping FitGirl's Proton-irrelevant finalisation step.
