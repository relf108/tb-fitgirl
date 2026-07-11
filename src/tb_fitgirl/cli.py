"""CLI for tb-fitgirl.

Commands:
    tb-fitgirl search "<title>"          scrape a repack source + TorBox cache status
    tb-fitgirl cache "<magnet-or-title>" add magnet to TorBox (checks cache first)
"""

from __future__ import annotations

import argparse
import sys

import httpx

from .models import Repack, magnet_hash
from .scrapers import DEFAULT_SCRAPER, SCRAPERS, get_scraper
from .torbox import TorboxClient, TorboxError


def _scrape_with_magnets(source: str, title: str, limit: int) -> list[Repack]:
    with get_scraper(source) as scraper:
        repacks = scraper.search(title, limit=limit)
        for repack in repacks:
            scraper.fetch_magnets(repack)
    return [r for r in repacks if r.magnets]


def cmd_search(args: argparse.Namespace) -> int:
    repacks = _scrape_with_magnets(args.source, args.title, args.limit)
    if not repacks:
        print(f"No repacks with magnets found via '{args.source}'.")
        return 1

    all_hashes = sorted({h for r in repacks for h in r.hashes})
    with TorboxClient() as tb:
        statuses = tb.check_cached(all_hashes)

    width = min(max(len(r.title) for r in repacks), 70)
    print(f"{'#':>3}  {'Title':<{width}}  {'Size':>10}  Cached")
    for i, repack in enumerate(repacks, 1):
        repack_statuses = [statuses[h] for h in repack.hashes]
        cached = next((s for s in repack_statuses if s.cached), None)
        size = cached.size_human if cached else ""
        print(f"{i:>3}  {repack.title[:width]:<{width}}  {size:>10}  {'yes' if cached else 'no'}")
    return 0


def cmd_cache(args: argparse.Namespace) -> int:
    magnet = args.target
    if not magnet.startswith("magnet:"):
        # Treat target as a title: scrape the source for its magnet.
        with get_scraper(args.source) as scraper:
            repack = scraper.find_repack(args.target)
        if repack is None or repack.primary_magnet is None:
            print(f"No magnet found for '{args.target}' via '{args.source}'.")
            return 1
        print(f"Found: {repack.title}")
        magnet = repack.primary_magnet

    with TorboxClient() as tb:
        infohash = magnet_hash(magnet)
        already_cached = False
        if infohash:
            status = tb.check_cached([infohash])[infohash]
            already_cached = status.cached
            print(f"Cache status: {'cached' if status.cached else 'not cached'}")

        if not already_cached and args.only_if_cached:
            print("Not cached and --only-if-cached given; not adding.")
            return 1

        try:
            data = tb.create_torrent(magnet, only_if_cached=args.only_if_cached)
        except TorboxError as err:
            if err.code == "DOWNLOAD_NOT_CACHED":
                print("Not cached and --only-if-cached given; not adding.")
                return 1
            raise
    name = data.get("name") or data.get("hash") or "torrent"
    print(f"Added to TorBox: {name}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tb-fitgirl", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def add_source_arg(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--source",
            choices=sorted(SCRAPERS),
            default=DEFAULT_SCRAPER,
            help=f"Repack source to scrape (default: {DEFAULT_SCRAPER})",
        )

    p_search = sub.add_parser("search", help="Scrape a repack source and show TorBox cache status")
    p_search.add_argument("title")
    p_search.add_argument("--limit", type=int, default=5, help="Max repack posts to check")
    add_source_arg(p_search)
    p_search.set_defaults(func=cmd_search)

    p_cache = sub.add_parser("cache", help="Add a magnet (or title) to TorBox to cache it")
    p_cache.add_argument("target", help="magnet URI, or a title to scrape from the source")
    p_cache.add_argument(
        "--only-if-cached",
        action="store_true",
        help="Never start an uncached download; only add if already cached",
    )
    add_source_arg(p_cache)
    p_cache.set_defaults(func=cmd_cache)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except TorboxError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    except httpx.HTTPError as err:
        print(f"error: HTTP request failed: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
