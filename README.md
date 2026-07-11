# tb-fitgirl

Find FitGirl repacks, cache them on [TorBox](https://torbox.app), download,
and install them on Linux via Proton — added to both Steam and your
application launcher.

Sources are pluggable ([fitgirl-repacks.site](https://fitgirl-repacks.site)
by default).

## Setup

Requires nix + direnv, and a Steam install with at least one Proton runtime.

```sh
direnv allow                              # dev shell (Python 3.14 + deps)
echo 'TORBOX_API_KEY=<your-key>' > .env   # key from torbox.app/settings
```

## Usage

```sh
# Search a repack source and show TorBox cache status
python -m tb_fitgirl.cli search "pragmata" [--limit N] [--source fitgirl]

# Cache a repack on TorBox (scrapes the magnet by title, or takes a magnet URI)
python -m tb_fitgirl.cli cache "pragmata"
python -m tb_fitgirl.cli cache "magnet:?xt=urn:btih:..." [--only-if-cached]

# Download from your TorBox account (auto-caches first if needed)
python -m tb_fitgirl.cli download "pragmata"            # -> ~/TBFGames
python -m tb_fitgirl.cli download "pragmata" --dest /mnt/games

# Install: auto-downloads if not present, unpacks via Proton, adds to Steam
# and the app launcher. Close Steam first (it rewrites shortcuts on exit).
python -m tb_fitgirl.cli install "pragmata"

# Add an already-installed game to Steam + launcher (no reinstall)
python -m tb_fitgirl.cli steam-add "pragmata"

# Uninstall: remove game files, Steam shortcut, and launcher entry
python -m tb_fitgirl.cli uninstall "pragmata"
python -m tb_fitgirl.cli uninstall "pragmata" --keep-files   # shortcuts only
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
