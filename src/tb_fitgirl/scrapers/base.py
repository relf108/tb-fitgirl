"""Scraper abstraction.

A scraper knows how to find repack posts on some site and extract magnet
links from them. Implement a new source by subclassing :class:`Scraper`
and registering it in ``scrapers/__init__.py``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from ..models import Repack


class Scraper(ABC):
    """Base class for repack sources."""

    #: Registry key and value of ``Repack.source`` for results.
    name: ClassVar[str]

    @abstractmethod
    def search(self, query: str, *, limit: int = 10) -> list[Repack]:
        """Return repack posts matching *query* (magnets not yet fetched)."""

    @abstractmethod
    def fetch_magnets(self, repack: Repack) -> Repack:
        """Populate ``repack.magnets`` from its post page and return it."""

    def find_repack(self, query: str) -> Repack | None:
        """Convenience: first search hit with magnets populated."""
        results = self.search(query, limit=1)
        if not results:
            return None
        return self.fetch_magnets(results[0])

    def close(self) -> None:  # noqa: B027  (optional hook, intentionally empty)
        """Release any resources (HTTP clients etc). Optional override."""

    def __enter__(self) -> Scraper:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
