"""Game metadata from the Steam storefront (appids, names, artwork).

Uses the keyless public store search endpoint plus the app-artwork CDN.
Optional SteamGridDB icons (when a key is available) are preferred for
shortcut / .desktop icons; the store header remains the library capsule.
Everything here is best-effort: repack titles don't always match store
names, and an install must never fail because artwork couldn't be fetched
(callers use :func:`find_icon`, which swallows failures and returns None).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx

STEAM_GRID_DB_ICONS_URL = "https://www.steamgriddb.com/api/v2/icons/steam/{appid}"
STORE_SEARCH_URL = "https://store.steampowered.com/api/storesearch/"
CDN_HEADER_URL = "https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/header.jpg"

ICONS_DIR = "~/.tb-fitgirl/icons"
CONFIG_DIR = "~/.config/tb-fitgirl"
STEAMGRIDDB_KEY_FILE = "steamgriddb_api_key"

DEFAULT_TIMEOUT = 15.0


@dataclass
class StoreMatch:
    """A storefront search hit."""

    appid: int
    name: str
    tiny_image: str = ""


def _normalise(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def _steamgriddb_api_key(api_key: str | None = None) -> str:
    """Resolve key: explicit arg → env → ~/.config/tb-fitgirl/steamgriddb_api_key."""
    if api_key and (stripped := api_key.strip()):
        return stripped
    env = (os.environ.get("STEAMGRIDDB_API_KEY") or "").strip()
    if env:
        return env
    path = Path(CONFIG_DIR).expanduser() / STEAMGRIDDB_KEY_FILE
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def steam_grid_db_search(
    appid: int,
    *,
    api_key: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> str | None:
    """Top-voted SteamGridDB icon URL for a Steam *appid*, or None.

    Needs a SteamGridDB API key (arg, ``STEAMGRIDDB_API_KEY``, or config file).
    Missing key / empty results return None; HTTP failures propagate.
    """
    key = _steamgriddb_api_key(api_key)
    if not key:
        return None
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        resp = client.get(
            STEAM_GRID_DB_ICONS_URL.format(appid=appid),
            headers={"Authorization": f"Bearer {key}"},
        )
        resp.raise_for_status()
        payload = resp.json()
    if not isinstance(payload, dict) or not payload.get("success"):
        return None
    items = payload.get("data") or []
    if not isinstance(items, list):
        return None
    for item in items:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or item.get("thumb") or "").strip()
        if url:
            return url
    return None


def store_search(term: str, *, timeout: float = DEFAULT_TIMEOUT) -> list[StoreMatch]:
    """Search the Steam storefront. Returns hits in Steam's relevance order."""
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        resp = client.get(STORE_SEARCH_URL, params={"term": term, "cc": "us", "l": "en"})
        resp.raise_for_status()
        payload = resp.json()
    items = payload.get("items") or [] if isinstance(payload, dict) else []
    matches: list[StoreMatch] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            appid = int(item.get("id") or 0)
        except (TypeError, ValueError):
            continue
        name = str(item.get("name") or "")
        if appid and name:
            matches.append(
                StoreMatch(appid=appid, name=name, tiny_image=str(item.get("tiny_image") or ""))
            )
    return matches


def best_match(term: str, matches: list[StoreMatch]) -> StoreMatch | None:
    """Pick a confident match for *term*, or None.

    Conservative on purpose: an exact normalised match, else a prefix match
    in either direction (editions/subtitles). No icon beats a wrong icon,
    so anything vaguer returns None.
    """
    want = _normalise(term)
    if not want:
        return None
    for match in matches:
        if _normalise(match.name) == want:
            return match
    for match in matches:
        have = _normalise(match.name)
        if have.startswith(want) or want.startswith(have):
            return match
    return None


def fetch_artwork(
    appid: int,
    *,
    icons_dir: Path | str = ICONS_DIR,
    timeout: float = DEFAULT_TIMEOUT,
) -> Path:
    """Download (and cache) the store header art for *appid*; returns its path."""
    directory = Path(icons_dir).expanduser()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{appid}.jpg"
    if target.is_file() and target.stat().st_size > 0:
        return target
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        resp = client.get(CDN_HEADER_URL.format(appid=appid))
        resp.raise_for_status()
    target.write_bytes(resp.content)
    return target


def fetch_url_artwork(
    url: str,
    appid: int,
    *,
    icons_dir: Path | str = ICONS_DIR,
    timeout: float = DEFAULT_TIMEOUT,
) -> Path:
    """Download (and cache) *url* as icon art for *appid*; returns its path."""
    directory = Path(icons_dir).expanduser()
    directory.mkdir(parents=True, exist_ok=True)
    suffix = Path(urlparse(url).path).suffix.lower() or ".png"
    if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".ico"}:
        suffix = ".png"
    # Distinct from header cache ({appid}.jpg) so both can coexist.
    target = directory / f"{appid}.icon{suffix}"
    if target.is_file() and target.stat().st_size > 0:
        return target
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        resp = client.get(url)
        resp.raise_for_status()
    target.write_bytes(resp.content)
    return target


def find_header(game_name: str, *, icons_dir: Path | str = ICONS_DIR) -> Path | None:
    """Steam store header (wide capsule) for *game_name*, or None. Never raises."""
    try:
        match = best_match(game_name, store_search(game_name))
        if match is None:
            return None
        return fetch_artwork(match.appid, icons_dir=icons_dir)
    except (httpx.HTTPError, OSError, ValueError):
        return None


def find_icon(
    game_name: str,
    *,
    icons_dir: Path | str = ICONS_DIR,
    api_key: str | None = None,
) -> Path | None:
    """Best-effort icon for a game name. Never raises.

    Prefers a SteamGridDB icon when a key is available; falls back to the
    Steam store header.
    """
    try:
        match = best_match(game_name, store_search(game_name))
        if match is None:
            return None
        try:
            icon_url = steam_grid_db_search(match.appid, api_key=api_key)
            if icon_url:
                return fetch_url_artwork(icon_url, match.appid, icons_dir=icons_dir)
        except (httpx.HTTPError, OSError, ValueError):
            pass
        return fetch_artwork(match.appid, icons_dir=icons_dir)
    except (httpx.HTTPError, OSError, ValueError):
        return None
