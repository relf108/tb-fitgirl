"""Game metadata from the Steam storefront (appids, names, artwork).

Uses the keyless public store search endpoint plus the app-artwork CDN.
Everything here is best-effort: repack titles don't always match store
names, and an install must never fail because artwork couldn't be fetched
(callers use :func:`find_icon`, which swallows failures and returns None).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import httpx

STORE_SEARCH_URL = "https://store.steampowered.com/api/storesearch/"
CDN_HEADER_URL = "https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/header.jpg"

ICONS_DIR = "~/.tb-fitgirl/icons"

DEFAULT_TIMEOUT = 15.0


@dataclass
class StoreMatch:
    """A storefront search hit."""

    appid: int
    name: str
    tiny_image: str = ""


def _normalise(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


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


def find_icon(game_name: str, *, icons_dir: Path | str = ICONS_DIR) -> Path | None:
    """Best-effort artwork for a game name. Never raises."""
    try:
        match = best_match(game_name, store_search(game_name))
        if match is None:
            return None
        return fetch_artwork(match.appid, icons_dir=icons_dir)
    except (httpx.HTTPError, OSError, ValueError):
        return None
