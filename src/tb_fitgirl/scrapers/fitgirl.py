"""Scraper for fitgirl-repacks.site."""

from __future__ import annotations

import httpx
from bs4 import BeautifulSoup

from ..models import Repack
from .base import Scraper

BASE_URL = "https://fitgirl-repacks.site"
DEFAULT_TIMEOUT = 30.0

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
        resp = self._client.get(BASE_URL, params={"s": query})
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
        magnets: list[str] = []
        for a in soup.select('a[href^="magnet:"]'):
            href = str(a["href"])
            if href not in magnets:
                magnets.append(href)
        repack.magnets = magnets
        return repack
