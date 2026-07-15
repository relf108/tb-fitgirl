"""Scraper for fitgirl-repacks.site."""

from __future__ import annotations

from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from ..models import Repack
from .base import Scraper

BASE_URL = "https://fitgirl-repacks.site"
DEFAULT_TIMEOUT = 30.0

# The site's host; used to recognise post URLs pasted as a target.
SITE_HOST = "fitgirl-repacks.site"

# Non-post paths under the site host: URLs on these are search/listing pages,
# not individual repack posts, so they should not be resolved as a repack.
NON_POST_PREFIXES = ("/category/", "/tag/", "/page/", "/author/", "/wp-", "/feed")

# Separators that mark the start of edition/version/DLC noise in a post title;
# everything from the first one on is dropped to build a relaxed search query.
# Commas and bare "+" are NOT separators: they appear inside real game names
# ("Warhammer 40,000"), while DLC noise is introduced by a dash or a
# space-padded "+" (" + 5 DLCs").
NOISE_SEPARATORS = ("–", "—", " - ", "[", "(", " + ")

# Post titles use typographic punctuation (curly quotes, en/em dashes) that the
# site's WordPress search does not match against straight-ASCII input: a query
# for "Sid Meier's ..." finds the post, but the scraped "Sid Meier’s ..." (with
# a curly apostrophe) returns nothing. Front-ends feed scraped titles back in
# verbatim, so fold these to ASCII before searching.
PUNCTUATION_FOLD = str.maketrans(
    {
        "\u2018": "'",  # left single quote
        "\u2019": "'",  # right single quote / apostrophe
        "\u201c": '"',  # left double quote
        "\u201d": '"',  # right double quote
        "\u2013": "-",  # en dash
        "\u2014": "-",  # em dash
        "\u2026": "...",  # ellipsis
    }
)


def normalise_query(query: str) -> str:
    """Fold typographic punctuation to ASCII so WordPress search matches."""
    return query.translate(PUNCTUATION_FOLD)


# Site is WordPress; a real browser UA avoids trivial bot blocks.
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:127.0) Gecko/20100101 Firefox/127.0"


class FitgirlScraper(Scraper):
    name = "fitgirl"

    def __init__(self, timeout: float = DEFAULT_TIMEOUT):
        self._client = httpx.Client(
            headers={"User-Agent": USER_AGENT},
            timeout=timeout,
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    def search(self, query: str, *, limit: int = 10) -> list[Repack]:
        resp = self._client.get(BASE_URL, params={"s": normalise_query(query)})
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        results: list[Repack] = []
        for article in soup.select("article"):
            link = article.select_one(".entry-title a") or article.select_one("h1 a, h2 a, h3 a")
            if link is None or not link.get("href"):
                continue
            title = link.get_text(strip=True)
            if not title:
                continue
            results.append(Repack(title=title, url=str(link["href"]), source=self.name))
            if len(results) >= limit:
                break
        return results

    def fetch_magnets(self, repack: Repack) -> Repack:
        resp = self._client.get(repack.url)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        if not repack.title:
            title_el = soup.select_one(".entry-title") or soup.find("h1")
            if title_el is not None:
                repack.title = title_el.get_text(strip=True)
        magnets: list[str] = []
        for a in soup.select('a[href^="magnet:"]'):
            href = str(a["href"])
            if href not in magnets:
                magnets.append(href)
        repack.magnets = magnets
        return repack

    def repack_from_url(self, url: str) -> Repack | None:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return None
        if parsed.netloc.lower().removeprefix("www.") != SITE_HOST:
            return None
        path = parsed.path
        # A post lives at "/<slug>/"; the site root and listing pages aren't posts.
        if path in ("", "/") or path.startswith(NON_POST_PREFIXES):
            return None
        # Title is filled in from the page by fetch_magnets; leave it empty here.
        return Repack(title="", url=url, source=self.name)

    def relax_query(self, query: str) -> str | None:
        # Split on the raw (typographic) separators first, then fold the
        # surviving game name to ASCII so search() can match it.
        relaxed = query
        for sep in NOISE_SEPARATORS:
            idx = relaxed.find(sep)
            if idx > 0:
                relaxed = relaxed[:idx]
        relaxed = normalise_query(relaxed).strip()
        # search() already folds punctuation, so a relaxed query that only
        # differs typographically would repeat the identical failed request.
        if not relaxed or relaxed == normalise_query(query).strip():
            return None
        return relaxed
