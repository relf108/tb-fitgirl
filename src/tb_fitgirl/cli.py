"""CLI for tb-fitgirl.

Commands:
    tb-fitgirl search "<title>"          scrape a repack source + TorBox cache status
    tb-fitgirl cache "<magnet-or-title>" add magnet to TorBox (checks cache first)
    tb-fitgirl download "<title|id>"     download a torrent from your TorBox account
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

from .downloader import Downloader
from .models import Repack, Torrent, TorrentFile, magnet_hash
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


def _resolve_torrent(tb: TorboxClient, target: str) -> Torrent | None:
    """Find a torrent in the user's account by id, magnet/hash, or name substring."""
    torrents = tb.my_list()
    if target.isdigit():
        wanted_id = int(target)
        return next((t for t in torrents if t.id == wanted_id), None)
    infohash = magnet_hash(target) if target.startswith("magnet:") else target.lower()
    by_hash = next((t for t in torrents if t.hash == infohash), None)
    if by_hash:
        return by_hash
    needle = target.lower()
    return next((t for t in torrents if needle in t.name.lower()), None)


def _print_progress(file: TorrentFile, done: int, total: int) -> None:
    pct = f"{done / total:5.0%}" if total else f"{done} B"
    end = "\n" if total and done >= total else "\r"
    print(f"  {file.short_name or file.name}: {pct}", end=end, flush=True)


def _add_to_account(tb: TorboxClient, args: argparse.Namespace) -> int | None:
    """Scrape (if needed), cache-check, and add the target. Returns torrent id."""
    magnet = args.target
    if not magnet.startswith("magnet:"):
        print(f"Not in your account; scraping '{args.source}' for '{args.target}'...")
        with get_scraper(args.source) as scraper:
            repack = scraper.find_repack(args.target)
        if repack is None or repack.primary_magnet is None:
            print(f"No magnet found for '{args.target}' via '{args.source}'.")
            return None
        print(f"Found: {repack.title}")
        magnet = repack.primary_magnet

    infohash = magnet_hash(magnet)
    if infohash:
        status = tb.check_cached([infohash])[infohash]
        print(f"Cache status: {'cached' if status.cached else 'not cached'}")
        if not status.cached:
            print("TorBox will torrent it first; this can take a while.")
    data = tb.create_torrent(magnet)
    torrent_id = data.get("torrent_id")
    if torrent_id is None:
        print("TorBox did not return a torrent id.")
        return None
    print(f"Added to TorBox (id {torrent_id}).")
    return int(torrent_id)


def _print_wait(torrent: Torrent) -> None:
    state = torrent.download_state or "queued"
    print(f"  waiting: {state} {torrent.progress:.0%}", end="\r", flush=True)


def cmd_download(args: argparse.Namespace) -> int:
    dest = Path(args.dest).expanduser()
    with TorboxClient() as tb:
        torrent = _resolve_torrent(tb, args.target)
        if torrent is not None:
            torrent_id = torrent.id
        elif args.target.isdigit():
            # Explicit ids never fall back to scraping: the digits would be
            # used as a search title and could match something unrelated.
            print(f"Torrent id {args.target} not found in your TorBox account.")
            return 1
        else:
            maybe_id = _add_to_account(tb, args)
            if maybe_id is None:
                return 1
            torrent_id = maybe_id

        with Downloader(tb, dest) as dl:
            torrent = dl.wait_ready(torrent_id, timeout=args.wait, on_poll=_print_wait)
            print(f"{torrent.name} ({torrent.size_human}, {len(torrent.files)} files)")
            paths = dl.download_torrent(torrent, on_progress=_print_progress)

    print(f"Downloaded {len(paths)} files to {dest.resolve()}")
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

    p_dl = sub.add_parser("download", help="Download a torrent from your TorBox account")
    p_dl.add_argument(
        "target",
        help="torrent id (all digits), magnet/hash, or name substring; "
        "if absent from your account, non-id targets are scraped and added",
    )
    p_dl.add_argument(
        "--dest", default="~/TBFGames", help="Destination directory (default: ~/TBFGames)"
    )
    p_dl.add_argument(
        "--wait",
        type=float,
        default=900,
        help="Max seconds to wait for TorBox to finish fetching an uncached torrent",
    )
    add_source_arg(p_dl)
    p_dl.set_defaults(func=cmd_download)

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
