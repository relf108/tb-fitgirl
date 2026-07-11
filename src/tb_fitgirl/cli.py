"""CLI for tb-fitgirl.

Commands:
    tb-fitgirl search "<title>"          scrape a repack source + TorBox cache status
    tb-fitgirl cache "<magnet-or-title>" add magnet to TorBox (checks cache first)
    tb-fitgirl download "<title|id>"     download a torrent from your TorBox account
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import httpx

from . import steam
from .desktop import remove_desktop_entry, write_desktop_entry
from .downloader import Downloader
from .installer import InstallError, find_repack, install, verify_bins
from .models import Repack, Torrent, TorrentFile, human_size, magnet_hash
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


def _do_download(args: argparse.Namespace, dest: Path) -> Path | None:
    """Cache (if needed) + download the target into *dest*.

    Returns the top-level directory the files were written into, or None on
    failure. Shared by the ``download`` and ``install`` commands.
    """
    with TorboxClient() as tb:
        torrent = _resolve_torrent(tb, args.target)
        if torrent is not None:
            torrent_id = torrent.id
        elif args.target.isdigit():
            # Explicit ids never fall back to scraping: the digits would be
            # used as a search title and could match something unrelated.
            print(f"Torrent id {args.target} not found in your TorBox account.")
            return None
        else:
            maybe_id = _add_to_account(tb, args)
            if maybe_id is None:
                return None
            torrent_id = maybe_id

        with Downloader(tb, dest) as dl:
            torrent = dl.wait_ready(torrent_id, timeout=args.wait, on_poll=_print_wait)
            print(f"{torrent.name} ({torrent.size_human}, {len(torrent.files)} files)")
            paths = dl.download_torrent(torrent, on_progress=_print_progress)

    print(f"Downloaded {len(paths)} files to {dest.resolve()}")
    # Files land under dest/<torrent name>/...; return that top dir.
    tops = {p.relative_to(dest).parts[0] for p in paths if p.is_relative_to(dest)}
    if len(tops) == 1:
        return dest / next(iter(tops))
    return dest


def cmd_download(args: argparse.Namespace) -> int:
    dest = Path(args.dest).expanduser()
    return 0 if _do_download(args, dest) is not None else 1


def _find_repack_dir(target: str, downloads: Path) -> Path | None:
    path = Path(target).expanduser()
    if path.is_dir():
        return path
    if not downloads.is_dir():
        return None
    needle = target.lower()
    candidates = (p for p in sorted(downloads.iterdir()) if p.is_dir())
    return next((p for p in candidates if needle in p.name.lower()), None)


def _find_game_exe(game_dir: Path) -> Path | None:
    """Best guess at the main game executable after install."""
    skip = ("unins", "setup", "dxwebsetup", "vcredist", "dotnet", "redist", "crashhandler")
    exes = [p for p in game_dir.rglob("*.exe") if not any(s in p.name.lower() for s in skip)]
    return max(exes, key=lambda p: p.stat().st_size, default=None)


def _install_progress(done: int, total: int | None, elapsed: float, rate: float) -> None:
    rate_s = f"{human_size(int(rate))}/s"
    if total:
        pct = min(done / total, 1.0)
        bar = "#" * int(pct * 30)
        line = f"  [{bar:<30}] {pct:4.0%}  {human_size(done)}/~{human_size(total)}  {rate_s}"
    else:
        line = f"  {human_size(done)} written  {rate_s}  {elapsed:.0f}s"
    print(f"\r{line:<78}", end="", flush=True)


def _add_steam_shortcut(name: str, exe: Path, target: str, *, app_menu: bool = True) -> int:
    if steam.steam_running():
        print(
            "\nSteam is running; it would overwrite the shortcut on exit.\n"
            f"Close Steam, then run: tb-fitgirl steam-add '{target}'"
        )
        raise steam.SteamNotFound("Steam is running")
    appid = steam.add_shortcut(name, exe)
    print(
        f"Added to Steam as '{name}' (appid {appid}).\n"
        "Restart Steam, then set Proton in: Properties > Compatibility."
    )
    if app_menu:
        entry = write_desktop_entry(name, appid)
        print(f"Added to application launcher: {entry.name}")
    return appid


def cmd_steam_add(args: argparse.Namespace) -> int:
    game_dir = Path(args.target).expanduser()
    if not game_dir.is_dir():
        game_dir = steam.common_dir() / args.target
    if not game_dir.is_dir():
        print(f"No installed game directory found for '{args.target}'.")
        return 1
    exe = _find_game_exe(game_dir)
    if exe is None:
        print(f"No game exe found under {game_dir}.")
        return 1
    try:
        _add_steam_shortcut(game_dir.name, exe, args.target)
    except steam.SteamNotFound:
        return 1
    return 0


def _find_installed_dir(target: str) -> Path | None:
    """Locate an installed game directory under steamapps/common by name."""
    path = Path(target).expanduser()
    if path.is_dir() and path.name:
        return path
    try:
        common = steam.common_dir()
    except steam.SteamNotFound:
        return None
    exact = common / target
    if exact.is_dir():
        return exact
    needle = target.lower()
    return next(
        (p for p in sorted(common.iterdir()) if p.is_dir() and needle in p.name.lower()),
        None,
    )


def cmd_uninstall(args: argparse.Namespace) -> int:
    game_dir = _find_installed_dir(args.target)
    if game_dir is None:
        print(f"No installed game matching '{args.target}'.")
        return 1
    name = game_dir.name

    # Safety: only ever delete inside a Steam library's steamapps/common.
    if not args.keep_files:
        try:
            common = steam.common_dir().resolve()
        except steam.SteamNotFound:
            common = None
        resolved = game_dir.resolve()
        if common is None or common not in resolved.parents:
            print(
                f"Refusing to delete {resolved}: not inside a Steam steamapps/common. "
                "Use --keep-files to only remove the shortcuts."
            )
            return 1

    if not args.no_steam:
        if steam.steam_running():
            print(
                "Steam is running; it would restore the shortcut on exit.\n"
                f"Close Steam, then run: tb-fitgirl uninstall '{args.target}'"
            )
            return 1
        appid = steam.remove_shortcut(name)
        print(
            f"Removed Steam shortcut for '{name}'."
            if appid is not None
            else f"No Steam shortcut found for '{name}'."
        )

    if remove_desktop_entry(name):
        print("Removed application launcher entry.")

    if not args.keep_files:
        shutil.rmtree(game_dir)
        print(f"Deleted {game_dir}")
    else:
        print(f"Kept game files at {game_dir}")
    return 0


def cmd_install(args: argparse.Namespace) -> int:
    downloads = Path(args.downloads).expanduser()
    repack_dir = _find_repack_dir(args.target, downloads)
    if repack_dir is None:
        if args.no_download:
            print(f"No repack directory matching '{args.target}' and --no-download set.")
            return 1
        if args.target.isdigit():
            print(f"No downloaded repack matching '{args.target}'.")
            return 1
        print(f"'{args.target}' not downloaded yet; fetching from TorBox first...")
        top = _do_download(args, downloads)
        if top is None:
            return 1
        repack_dir = _find_repack_dir(args.target, downloads)
        if repack_dir is None:
            print(f"Downloaded, but no repack directory found under {downloads}.")
            return 1

    repack = find_repack(repack_dir)
    print(
        f"Repack: {repack.game_name} ({len(repack.bins)} archives"
        + (f", {len(repack.optional_bins)} optional" if repack.optional_bins else "")
        + ")"
    )

    if not args.no_verify and repack.md5_file is not None:
        print("Verifying archives...")
        failures = verify_bins(
            repack, on_progress=lambda name, ok: print(f"  {'ok ' if ok else 'BAD'} {name}")
        )
        if failures:
            print("Verification failed:\n  " + "\n  ".join(failures))
            return 1

    proton_path = None
    if args.runtime == "proton":
        try:
            proton_path = steam.find_proton(args.proton) if args.proton else steam.newest_proton()
        except steam.SteamNotFound as err:
            print(f"{err}\nUse --runtime wine to fall back to system Wine.")
            return 1
        print(f"Runtime: Proton ({proton_path.parent.name})")
    else:
        print("Runtime: system Wine")

    target = steam.common_dir() / repack.game_name
    print(f"Installing to {target} (unpacking; this can take a while)...")
    progress = None if args.gui else _install_progress
    # Stop waiting once the game exe is present: FitGirl's finalisation runs
    # Windows redists that are irrelevant under Proton and can hang for ages.
    # In --gui mode leave the installer alone so the user drives it.
    ready_when = None if args.gui else (lambda d: _find_game_exe(d) is not None)
    install(
        repack,
        target,
        runtime=args.runtime,
        proton=proton_path,
        use_steam_run=not args.no_steam_run,
        silent=not args.gui,
        mute=not args.no_mute,
        ready_when=ready_when,
        on_progress=progress,
    )
    if progress is not None:
        print()  # end the progress line

    exe = _find_game_exe(target)
    if exe is None:
        print(f"Installed, but no game exe found under {target}.")
        return 1
    print(f"Installed: {exe}")

    if not args.no_steam:
        try:
            _add_steam_shortcut(repack.game_name, exe, args.target, app_menu=not args.no_app_menu)
        except steam.SteamNotFound:
            return 1
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

    p_inst = sub.add_parser(
        "install",
        help="Install a repack (auto-downloads from TorBox first if not present)",
    )
    p_inst.add_argument("target", help="repack directory path, or name substring under --downloads")
    p_inst.add_argument("--downloads", default="~/TBFGames", help="Where downloads live")
    p_inst.add_argument(
        "--no-download",
        action="store_true",
        help="Fail instead of downloading if the repack isn't present locally",
    )
    p_inst.add_argument(
        "--wait",
        type=float,
        default=900,
        help="Max seconds to wait for TorBox to fetch an uncached torrent (when downloading)",
    )
    p_inst.add_argument("--no-verify", action="store_true", help="Skip MD5 verification")
    p_inst.add_argument(
        "--runtime",
        choices=("proton", "wine"),
        default="proton",
        help="Runtime for the installer (default: proton; more reliable for FitGirl unpackers)",
    )
    p_inst.add_argument(
        "--proton",
        metavar="NAME",
        help="Proton build name substring (default: newest official Valve Proton)",
    )
    p_inst.add_argument("--gui", action="store_true", help="Run installer GUI instead of silent")
    p_inst.add_argument(
        "--no-mute", action="store_true", help="Don't silence the installer's music (Wine audio)"
    )
    p_inst.add_argument(
        "--no-steam-run",
        action="store_true",
        help="Don't wrap Proton in steam-run (only relevant on NixOS)",
    )
    p_inst.add_argument("--no-steam", action="store_true", help="Don't add a Steam shortcut")
    p_inst.add_argument(
        "--no-app-menu",
        action="store_true",
        help="Don't add a .desktop entry to the application launcher",
    )
    add_source_arg(p_inst)
    p_inst.set_defaults(func=cmd_install)

    p_steam = sub.add_parser(
        "steam-add", help="Add an already-installed game to Steam (no reinstall)"
    )
    p_steam.add_argument("target", help="installed game dir path, or name under steamapps/common")
    p_steam.set_defaults(func=cmd_steam_add)

    p_uninst = sub.add_parser(
        "uninstall", help="Remove an installed game: files, Steam shortcut, launcher entry"
    )
    p_uninst.add_argument("target", help="installed game dir path, or name under steamapps/common")
    p_uninst.add_argument(
        "--keep-files", action="store_true", help="Only remove shortcuts, keep the game files"
    )
    p_uninst.add_argument("--no-steam", action="store_true", help="Don't touch the Steam shortcut")
    p_uninst.set_defaults(func=cmd_uninstall)

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
    except (InstallError, steam.SteamNotFound) as err:
        print(f"error: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
