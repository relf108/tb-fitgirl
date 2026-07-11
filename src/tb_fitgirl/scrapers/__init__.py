"""Scraper registry.

To add a new source: subclass :class:`~tb_fitgirl.scrapers.base.Scraper`,
then add the class to ``SCRAPERS`` below.
"""

from __future__ import annotations

from .base import Scraper
from .fitgirl import FitgirlScraper

SCRAPERS: dict[str, type[Scraper]] = {
    FitgirlScraper.name: FitgirlScraper,
}

DEFAULT_SCRAPER = FitgirlScraper.name


def get_scraper(name: str = DEFAULT_SCRAPER) -> Scraper:
    try:
        return SCRAPERS[name]()
    except KeyError:
        available = ", ".join(sorted(SCRAPERS))
        raise ValueError(f"Unknown scraper '{name}'. Available: {available}") from None


__all__ = ["DEFAULT_SCRAPER", "SCRAPERS", "FitgirlScraper", "Scraper", "get_scraper"]
