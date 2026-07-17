"""JSON-lines stdio bridge: the back-end boundary for GUI front-ends.

A front-end (see gui/) spawns ``python -m tb_fitgirl.bridge``, writes one
JSON request per line on stdin, and reads JSON events on stdout:

    request : {"id": 1, "op": "search", "args": {"title": "pragmata"}}
    progress: {"id": 1, "event": "progress",
               "data": {"phase": "download", "done": 0, "total": 0,
                        "rate": 0.0, "message": "setup.exe"}}
    result  : {"id": 1, "event": "result", "data": {...}}
    error   : {"id": 1, "event": "error", "data": {"message": "...", "code": null}}
    confirm : {"id": 1, "event": "confirm",
               "data": {"kind": "finish_install", "message": "..."}}
    multiple_exes : {"id": 1, "event": "multiple_exes",
                     "data": {"exes": ["/path/to/game.exe", ...]}}

A ``confirm`` or ``multiple_exes`` event is a question: the bridge blocks
until the front-end writes one reply line. For ``confirm`` the reply is
``{"id": 1, "confirm": true|false}`` (defaults to true if stdin is closed).
For ``multiple_exes`` the reply is ``{"id": 1, "selected": "/path/to/game.exe"}``
naming one of the offered paths; if stdin is closed or the reply is
malformed, the first (largest) exe is used.

Requests are handled one at a time, in order. Long-running operations are
cancelled by killing the bridge process (front-ends run one bridge per
operation), so there is no in-band cancel.

All ops accept an optional ``api_key`` arg; TORBOX_API_KEY is the fallback.
Flutter (or any other front-end) is presentation only: every piece of
TorBox/Proton logic stays in the library modules this bridge drives.
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from . import steam
from .cli import _find_game_exe, _find_installed_dir, _find_repack_dir, _resolve_torrent
from .desktop import APPLICATIONS_DIR, remove_desktop_entry, write_desktop_entry
from .downloader import Downloader
from .installer import InstallError, find_repack, install, verify_bins
from .metadata import best_match, find_icon, store_search
from .models import Torrent, TorrentFile, human_size, magnet_hash
from .scrapers import DEFAULT_SCRAPER, SCRAPERS, get_scraper
from .torbox import TorboxClient, TorboxError

# on_event(phase, done, total, rate, message)
EmitFn = Callable[..., None]

PLAN_NAMES = {0: "Free", 1: "Essential", 2: "Pro", 3: "Standard"}

PROGRESS_INTERVAL = 0.1  # min seconds between progress events per phase


class BridgeError(RuntimeError):
    """An operation-level failure to report to the front-end."""


def _write(obj: dict[str, Any]) -> None:
    print(json.dumps(obj), flush=True)


# The id of the request currently being handled (requests are strictly
# serial), so out-of-band events like ``confirm`` can be tagged with it.
_current_request_id: Any = None


def _confirm(*, kind: str, message: str) -> bool:
    """Ask the front-end a yes/no question and block for its one-line reply.

    Emits a ``confirm`` event and reads one JSON line from stdin, expected as
    ``{"id": <req id>, "confirm": true|false}``. If stdin is already closed
    (front-ends that close it after sending the request) or the reply is
    malformed, the answer defaults to True, preserving the previous
    auto-finish behaviour.
    """
    _write(
        {
            "id": _current_request_id,
            "event": "confirm",
            "data": {"kind": kind, "message": message},
        }
    )
    line = sys.stdin.readline()
    if not line.strip():
        return True
    try:
        reply = json.loads(line)
    except ValueError:
        return True
    return bool(reply.get("confirm", True)) if isinstance(reply, dict) else True


class _Throttle:
    """Rate-limit progress events so per-chunk callbacks don't flood stdout."""

    def __init__(self, interval: float = PROGRESS_INTERVAL):
        self._interval = interval
        self._last = 0.0

    def ready(self, *, final: bool = False) -> bool:
        now = time.monotonic()
        if final or now - self._last >= self._interval:
            self._last = now
            return True
        return False


class _RateMeter:
    """Bytes/sec over the window since the last sample."""

    def __init__(self) -> None:
        self._t = time.monotonic()
        self._done = 0

    def sample(self, done: int) -> float:
        now = time.monotonic()
        delta_t, delta_b = now - self._t, done - self._done
        self._t, self._done = now, done
        return delta_b / delta_t if delta_t > 0 and delta_b >= 0 else 0.0


# -- op helpers --------------------------------------------------------------


def _client(args: dict[str, Any]) -> TorboxClient:
    return TorboxClient(api_key=args.get("api_key") or None)


def _resolve_or_add(tb: TorboxClient, emit: EmitFn, target: str, source: str) -> int:
    """Find *target* in the account, scraping + adding it if needed."""
    torrent = _resolve_torrent(tb, target)
    if torrent is not None:
        return torrent.id
    if target.isdigit():
        raise BridgeError(f"Torrent id {target} not found in your TorBox account.")
    magnet = target
    if not magnet.startswith("magnet:"):
        with get_scraper(source) as scraper:
            # Ask the scraper whether the target is a post URL it can open
            # directly; anything else (including foreign URLs) is searched.
            if scraper.repack_from_url(target) is not None:
                emit(phase="scrape", message=f"Opening {target}...")
            else:
                emit(phase="scrape", message=f"Searching {source} for '{target}'...")
            repack = scraper.find_repack(target)
        if repack is None or repack.primary_magnet is None:
            raise BridgeError(f"No magnet found for '{target}' via '{source}'.")
        emit(phase="scrape", message=f"Found: {repack.title}")
        magnet = repack.primary_magnet
    infohash = magnet_hash(magnet)
    if infohash and not tb.check_cached([infohash])[infohash].cached:
        emit(phase="cache", message="Not cached; TorBox is fetching the torrent first.")
    data = tb.create_torrent(magnet)
    torrent_id = data.get("torrent_id")
    if torrent_id is None:
        raise BridgeError("TorBox did not return a torrent id.")
    return int(torrent_id)


def _download(emit: EmitFn, args: dict[str, Any], dest: Path) -> Path:
    """Cache (if needed) + download into *dest*; returns the repack's top dir."""
    target = str(args.get("target") or "")
    source = args.get("source") or DEFAULT_SCRAPER
    wait = float(args.get("wait") or 900)

    def on_poll(torrent: Torrent) -> None:
        emit(
            phase="cache",
            done=int(torrent.progress * 100),
            total=100,
            message=torrent.download_state or "queued",
        )

    throttle = _Throttle()
    meter = _RateMeter()

    def on_progress(file: TorrentFile, done: int, total: int) -> None:
        if throttle.ready(final=bool(total) and done >= total):
            emit(
                phase="download",
                done=done,
                total=total,
                rate=meter.sample(done),
                message=file.short_name or file.name,
            )

    with _client(args) as tb:
        torrent_id = _resolve_or_add(tb, emit, target, source)
        with Downloader(tb, dest) as dl:
            torrent = dl.wait_ready(torrent_id, timeout=wait, on_poll=on_poll)
            emit(
                phase="download",
                message=f"{torrent.name} ({torrent.size_human}, {len(torrent.files)} files)",
            )
            paths = dl.download_torrent(torrent, on_progress=on_progress)

    tops = {p.relative_to(dest).parts[0] for p in paths if p.is_relative_to(dest)}
    return dest / next(iter(tops)) if len(tops) == 1 else dest


# -- ops ----------------------------------------------------------------------


def op_status(emit: EmitFn, args: dict[str, Any]) -> dict[str, Any]:
    return {"steam_running": steam.steam_running(), "sources": sorted(SCRAPERS)}


def op_validate_key(emit: EmitFn, args: dict[str, Any]) -> dict[str, Any]:
    with _client(args) as tb:
        data = tb.me()
    plan = data.get("plan")
    plan_name = PLAN_NAMES.get(plan) if isinstance(plan, int) else None
    return {
        "email": data.get("email"),
        "plan": plan,
        "plan_name": plan_name or str(plan),
        "expiry": data.get("premium_expires_at"),
    }


def op_search(emit: EmitFn, args: dict[str, Any]) -> dict[str, Any]:
    title = str(args.get("title") or "")
    limit = int(args.get("limit") or 5)
    source = args.get("source") or DEFAULT_SCRAPER
    emit(phase="scrape", message=f"Searching {source}...")
    with get_scraper(source) as scraper:
        repacks = scraper.search(title, limit=limit)
        for repack in repacks:
            scraper.fetch_magnets(repack)
    repacks = [r for r in repacks if r.magnets]
    if not repacks:
        return {"repacks": []}

    all_hashes = sorted({h for r in repacks for h in r.hashes})
    emit(phase="cache", message="Checking TorBox cache...")
    with _client(args) as tb:
        statuses = tb.check_cached(all_hashes)

    results = []
    for repack in repacks:
        cached = next((statuses[h] for h in repack.hashes if statuses[h].cached), None)
        results.append(
            {
                "title": repack.title,
                "url": repack.url,
                "magnet": repack.primary_magnet,
                "cached": cached is not None,
                "size": cached.size if cached else 0,
                "size_human": cached.size_human if cached else "",
                "source": source,
            }
        )
    return {"repacks": results}


def op_cache(emit: EmitFn, args: dict[str, Any]) -> dict[str, Any]:
    target = str(args.get("target") or "")
    source = args.get("source") or DEFAULT_SCRAPER
    magnet = target
    if not magnet.startswith("magnet:"):
        with get_scraper(source) as scraper:
            repack = scraper.find_repack(target)
        if repack is None or repack.primary_magnet is None:
            raise BridgeError(f"No magnet found for '{target}' via '{source}'.")
        magnet = repack.primary_magnet
    with _client(args) as tb:
        infohash = magnet_hash(magnet)
        already = bool(infohash) and tb.check_cached([infohash])[infohash].cached
        data = tb.create_torrent(magnet, only_if_cached=bool(args.get("only_if_cached")))
    return {"name": data.get("name") or data.get("hash") or "torrent", "cached": already}


def op_download(emit: EmitFn, args: dict[str, Any]) -> dict[str, Any]:
    dest = Path(args.get("dest") or "~/TBFGames").expanduser()
    return {"path": str(_download(emit, args, dest))}


def _add_shortcut(name: str, exe: Path, *, app_menu: bool) -> dict[str, Any]:
    if steam.steam_running():
        raise BridgeError("Steam is running; close it and retry.")
    icon = find_icon(name)  # best-effort, None on any failure
    appid = steam.add_shortcut(name, exe, icon=icon)
    grid = None
    if icon is not None:
        # The same art doubles as the library header: it *is* Steam's wide
        # capsule format (460x215). Best-effort like the icon itself.
        try:
            grid = steam.set_grid_art(appid, icon)
        except (steam.SteamNotFound, OSError):
            grid = None
    entry = write_desktop_entry(name, appid, icon=str(icon) if icon else None) if app_menu else None
    return {
        "appid": appid,
        "desktop_entry": entry.name if entry else None,
        "icon": str(icon) if icon else None,
        "grid": str(grid) if grid else None,
    }


def _choose_exe(exes: list[Path]) -> Path:
    """If multiple exes found, ask the front-end which to use."""
    if len(exes) == 1:
        return exes[0]
    _write(
        {
            "id": _current_request_id,
            "event": "multiple_exes",
            "data": {"exes": [str(e) for e in exes]},
        }
    )
    line = sys.stdin.readline()
    if not line.strip():
        return exes[0]
    try:
        reply = json.loads(line)
    except ValueError:
        return exes[0]
    selected = str(reply.get("selected", "")) if isinstance(reply, dict) else ""
    if selected:
        chosen = Path(selected)
        if chosen in exes:
            return chosen
    return exes[0]


def op_steam_add(emit: EmitFn, args: dict[str, Any]) -> dict[str, Any]:
    target = str(args.get("target") or "")
    game_dir = _find_installed_dir(target)
    if game_dir is None:
        raise BridgeError(f"No installed game directory found for '{target}'.")
    exes = _find_game_exe(game_dir)
    if not exes:
        raise BridgeError(f"No game exe found under {game_dir}.")
    exe = _choose_exe(exes)
    shortcut = _add_shortcut(game_dir.name, exe, app_menu=not args.get("no_app_menu"))
    return {"name": game_dir.name, "exe": str(exe), **shortcut}


def _short_title(title: str) -> str:
    """Best-effort game name from a repack post title.

    Post titles carry edition/DLC/build noise ("Game – v1.0 + 2 DLCs
    [FitGirl Repack]") that local directory names (torrent names) don't
    share; only the leading game name is common to both.
    """
    for sep in ("–", "—", " - ", "[", "(", "+", ","):
        idx = title.find(sep)
        if idx > 0:
            title = title[:idx]
    return title.strip()


def _locate_repack_dir(target: str, downloads: Path) -> Path | None:
    """`_find_repack_dir`, retried with the de-noised game name."""
    found = _find_repack_dir(target, downloads)
    if found is None:
        short = _short_title(target)
        if short and short != target:
            found = _find_repack_dir(short, downloads)
    return found


def op_install(emit: EmitFn, args: dict[str, Any]) -> dict[str, Any]:
    target = str(args.get("target") or "")
    downloads = Path(args.get("downloads") or "~/TBFGames").expanduser()

    repack_dir = _locate_repack_dir(target, downloads)
    if repack_dir is None:
        if args.get("no_download"):
            raise BridgeError(f"No repack directory matching '{target}' (downloads disabled).")
        # _download reports the directory it wrote into: trust that over a
        # title match (post titles rarely match torrent dir names exactly).
        top = _download(emit, args, downloads)
        if top != downloads and top.is_dir():
            repack_dir = top
        else:
            repack_dir = _locate_repack_dir(target, downloads)
        if repack_dir is None:
            raise BridgeError(f"Downloaded, but no repack directory found under {downloads}.")

    repack = find_repack(repack_dir)
    emit(phase="verify", message=f"Repack: {repack.game_name} ({len(repack.bins)} archives)")
    if not args.get("no_verify") and repack.md5_file is not None:
        failures = verify_bins(
            repack,
            on_progress=lambda name, ok: emit(
                phase="verify", message=f"{'ok ' if ok else 'BAD'} {name}"
            ),
        )
        if failures:
            raise BridgeError("Verification failed: " + ", ".join(failures))

    runtime = args.get("runtime") or "proton"
    proton_path = None
    if runtime == "proton":
        wanted = args.get("proton")
        proton_path = steam.find_proton(wanted) if wanted else steam.newest_proton()
        emit(phase="unpack", message=f"Runtime: Proton ({proton_path.parent.name})")

    target_dir = steam.common_dir() / repack.game_name
    emit(phase="unpack", message=f"Installing to {target_dir}...")

    throttle = _Throttle()

    def on_progress(done: int, total: int | None, elapsed: float, rate: float) -> None:
        if throttle.ready(final=rate == 0.0):
            emit(
                phase="unpack",
                done=done,
                total=total or 0,
                rate=rate,
                message=f"{human_size(done)} written",
            )

    install(
        repack,
        target_dir,
        runtime=runtime,
        proton=proton_path,
        use_steam_run=not args.get("no_steam_run"),
        silent=True,
        mute=True,
        ready_when=lambda d: bool(_find_game_exe(d)),
        confirm_finish=lambda: _confirm(
            kind="finish_install",
            message=(
                "It looks like the install is done but the installer hasn't exited. Finish now?"
            ),
        ),
        on_progress=on_progress,
    )

    exes = _find_game_exe(target_dir)
    if not exes:
        raise BridgeError(f"Installed, but no game exe found under {target_dir}.")

    result: dict[str, Any] = {
        "name": repack.game_name,
        "exe": str(exes[0]),
        "steam_added": False,
        "appid": None,
        "manual_steps": ["Set the Proton version in Steam: Properties > Compatibility."],
    }
    if not args.get("no_steam"):
        emit(phase="shortcut", message="Adding Steam + launcher shortcuts...")
        if steam.steam_running():
            result["reason"] = "steam_running"
            result["manual_steps"].insert(
                0, "Steam is running: close it, then retry adding the shortcut."
            )
        else:
            exe = _choose_exe(exes)
            result["exe"] = str(exe)
            shortcut = _add_shortcut(repack.game_name, exe, app_menu=not args.get("no_app_menu"))
            result.update(steam_added=True, **shortcut)
    return result


def _desktop_entry_value(path: Path, key: str) -> str | None:
    prefix = f"{key}="
    for line in path.read_text(errors="replace").splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :].replace("\\\\", "\\")
    return None


def _usable_icon(icon: str | None) -> str | None:
    """An icon value front-ends can render: an existing absolute file path."""
    if icon and icon.startswith("/") and Path(icon).is_file():
        return icon
    return None


def op_metadata(emit: EmitFn, args: dict[str, Any]) -> dict[str, Any]:
    """Steam store lookup for a game name (appid/canonical name/thumbnail).

    Best-effort by design: unknown or unmatched names yield null fields
    rather than an error.
    """
    name = str(args.get("name") or "")
    if not name:
        raise BridgeError("metadata requires a name.")
    term = _short_title(name)  # store search chokes on "+ DLCs [FitGirl]" noise
    try:
        match = best_match(term, store_search(term))
    except httpx.HTTPError:
        match = None
    if match is None:
        return {"appid": None, "name": None, "image": None}
    return {"appid": match.appid, "name": match.name, "image": match.tiny_image or None}


def op_library(emit: EmitFn, args: dict[str, Any]) -> dict[str, Any]:
    """Games this tool installed: union of our Steam shortcuts + launcher entries.

    Only non-Steam shortcuts pointing inside steamapps/common (ours) and
    tb-fitgirl-*.desktop entries are considered, so regular Steam games can
    never appear here (or be offered for uninstall).
    """
    common = steam.common_dir().resolve()
    games: dict[str, dict[str, Any]] = {}

    try:
        shortcuts = steam.load_shortcuts(steam.shortcuts_vdf()).get("shortcuts") or {}
    except steam.SteamNotFound:
        shortcuts = {}
    for entry in shortcuts.values():
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("AppName") or "")
        exe = str(entry.get("Exe") or "").strip('"')
        if not name or not exe:
            continue
        exe_path = Path(exe)
        if not exe_path.is_absolute() or not exe_path.is_relative_to(common):
            continue  # a non-Steam shortcut the user made themselves
        game_dir = common / exe_path.relative_to(common).parts[0]
        games[name] = {
            "name": name,
            "path": str(game_dir),
            "exe": exe,
            "appid": entry.get("appid"),
            "icon": _usable_icon(str(entry.get("icon") or "")),
            "steam_shortcut": True,
            "launcher_entry": False,
            "installed": game_dir.is_dir(),
        }

    apps_dir = Path(APPLICATIONS_DIR).expanduser()
    for entry_path in sorted(apps_dir.glob("tb-fitgirl-*.desktop")):
        name = _desktop_entry_value(entry_path, "Name")
        if not name:
            continue
        icon = _usable_icon(_desktop_entry_value(entry_path, "Icon"))
        if name in games:
            games[name]["launcher_entry"] = True
            games[name]["icon"] = games[name]["icon"] or icon
        else:
            game_dir = common / name
            games[name] = {
                "name": name,
                "path": str(game_dir),
                "exe": None,
                "appid": None,
                "icon": icon,
                "steam_shortcut": False,
                "launcher_entry": True,
                "installed": game_dir.is_dir(),
            }

    return {
        "games": sorted(games.values(), key=lambda g: str(g["name"]).lower()),
        "steam_running": steam.steam_running(),
    }


def op_uninstall(emit: EmitFn, args: dict[str, Any]) -> dict[str, Any]:
    target = str(args.get("target") or "")
    keep_files = bool(args.get("keep_files"))
    game_dir = _find_installed_dir(target)
    if game_dir is None:
        raise BridgeError(f"No installed game matching '{target}'.")
    name = game_dir.name

    if not keep_files:
        # Safety: only ever delete inside a Steam library's steamapps/common.
        try:
            common = steam.common_dir().resolve()
        except steam.SteamNotFound:
            common = None
        if common is None or common not in game_dir.resolve().parents:
            raise BridgeError(
                f"Refusing to delete {game_dir.resolve()}: not inside steamapps/common."
            )

    removed_shortcut = False
    if not args.get("no_steam"):
        if steam.steam_running():
            raise BridgeError("Steam is running; close it and retry.")
        removed_appid = steam.remove_shortcut(name)
        removed_shortcut = removed_appid is not None
        if removed_appid is not None:
            try:
                steam.remove_grid_art(removed_appid)
            except steam.SteamNotFound:
                pass
    removed_entry = remove_desktop_entry(name)

    if not keep_files:
        shutil.rmtree(game_dir)
    return {
        "name": name,
        "path": str(game_dir),
        "removed_shortcut": removed_shortcut,
        "removed_entry": removed_entry,
        "deleted": not keep_files,
    }


OPS: dict[str, Callable[[EmitFn, dict[str, Any]], dict[str, Any]]] = {
    "status": op_status,
    "validate_key": op_validate_key,
    "search": op_search,
    "cache": op_cache,
    "download": op_download,
    "install": op_install,
    "steam_add": op_steam_add,
    "library": op_library,
    "metadata": op_metadata,
    "uninstall": op_uninstall,
}


def handle_request(req: dict[str, Any]) -> None:
    req_id = req.get("id")
    op = req.get("op")
    args = req.get("args") or {}

    def emit(
        *, phase: str, done: int = 0, total: int = 0, rate: float = 0.0, message: str = ""
    ) -> None:
        _write(
            {
                "id": req_id,
                "event": "progress",
                "data": {
                    "phase": phase,
                    "done": done,
                    "total": total,
                    "rate": rate,
                    "message": message,
                },
            }
        )

    global _current_request_id
    _current_request_id = req_id  # lets helpers (e.g. _confirm) tag their events

    handler = OPS.get(str(op))
    if handler is None:
        _write(
            {
                "id": req_id,
                "event": "error",
                "data": {"message": f"Unknown op: {op!r}", "code": "UNKNOWN_OP"},
            }
        )
        return
    try:
        data = handler(emit, args)
    except TorboxError as err:
        _write({"id": req_id, "event": "error", "data": {"message": str(err), "code": err.code}})
    except (BridgeError, InstallError, steam.SteamNotFound, ValueError) as err:
        _write({"id": req_id, "event": "error", "data": {"message": str(err), "code": None}})
    except httpx.HTTPError as err:
        _write(
            {
                "id": req_id,
                "event": "error",
                "data": {"message": f"HTTP request failed: {err}", "code": "HTTP"},
            }
        )
    else:
        _write({"id": req_id, "event": "result", "data": data})


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            _write(
                {
                    "id": None,
                    "event": "error",
                    "data": {"message": "Invalid JSON request.", "code": "BAD_REQUEST"},
                }
            )
            continue
        if not isinstance(req, dict):
            _write(
                {
                    "id": None,
                    "event": "error",
                    "data": {"message": "Request must be a JSON object.", "code": "BAD_REQUEST"},
                }
            )
            continue
        handle_request(req)
    return 0


if __name__ == "__main__":
    # Become a process-group leader so a front-end can cancel by killing the
    # group (Proton/unpacker children included). Deliberately NOT setsid():
    # detaching from the session/terminal makes Proton's unpacker hang
    # (observed stuck in kernel snd_power_wait on the installer's audio).
    import os

    try:
        os.setpgid(0, 0)
    except OSError:
        pass
    sys.exit(main())
