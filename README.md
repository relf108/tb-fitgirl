# tb-fitgirl

Scrape repack sites ([fitgirl-repacks.site](https://fitgirl-repacks.site) by
default) for repacks, check whether they're cached on TorBox, and cache the
ones that aren't.

## Setup

Requires nix + direnv.

```sh
direnv allow                       # builds dev shell (Python 3.14 + deps)
echo 'TORBOX_API_KEY=<your-key>' > .env   # key from https://torbox.app/settings
```

## Usage

```sh
# Scrape fitgirl-repacks.site, show TorBox cache status per repack
python -m tb_fitgirl.cli search "pragmata" [--limit N]

# Cache in TorBox: scrapes magnet by title (or takes a magnet URI directly),
# reports cache status, then adds it to your account
python -m tb_fitgirl.cli cache "pragmata"
python -m tb_fitgirl.cli cache "magnet:?xt=urn:btih:..."

# Never start an uncached (real) download; only add if already cached
python -m tb_fitgirl.cli cache "pragmata" --only-if-cached

# Pick a different repack source (see: Adding a scraper)
python -m tb_fitgirl.cli search "pragmata" --source fitgirl
```

## Adding a scraper

1. Subclass `tb_fitgirl.scrapers.base.Scraper`; implement `search()` and
   `fetch_magnets()`, set a unique `name`
2. Register the class in `SCRAPERS` in `src/tb_fitgirl/scrapers/__init__.py`

It's then available via `--source <name>`.

## Development

```sh
ruff check src tests && ruff format --check src tests   # QA
pytest                                                  # tests (HTTP mocked)
```

## API notes

- `https://api.torbox.app/v1/api` — `checkcached` (300/min), `createtorrent`
- Auth: `Authorization: Bearer $TORBOX_API_KEY`
- `createtorrent` success does NOT say whether the item was cached; use
  `checkcached` first or pass `add_only_if_cached` (fails with
  `DOWNLOAD_NOT_CACHED` when uncached)
- Uncached `createtorrent` calls are limited to 60/hour; cached ones only
  count against the 300/min pool
- `https://search-api.torbox.app` is NOT used: it returns
  `429 "0 per 1 minute"` (plan-gated to zero) on this account's plan
