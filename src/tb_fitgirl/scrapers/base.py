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

    def repack_from_url(self, url: str) -> Repack | None:
        """Build a :class:`Repack` directly from a post URL, if recognised.

        Returns ``None`` when *url* is not a post URL for this source, so
        callers fall back to treating the string as a search query. Override
        in subclasses that can map their own post URLs to repacks.
        """
        return None

    def relax_query(self, query: str) -> str | None:
        """A less specific search query, or ``None`` if none is worthwhile.

        Post titles carry version/edition/DLC noise that WordPress search
        won't match verbatim; a relaxed query drops that so an over-specific
        exact title still finds its post. Default: no relaxation.
        """
        return None

    def find_repack(self, query: str) -> Repack | None:
        """First result with magnets populated for *query*.

        *query* may be a post URL (resolved directly, skipping search) or a
        title. When an exact-title search finds nothing, retry once with a
        relaxed query (see :meth:`relax_query`).
        """
        from_url = self.repack_from_url(query)
        if from_url is not None:
            return self.fetch_magnets(from_url)

        results = self.search(query, limit=1)
        if not results:
            relaxed = self.relax_query(query)
            if relaxed and relaxed != query:
                results = self.search(relaxed, limit=1)
        if not results:
            return None
        return self.fetch_magnets(results[0])

    def close(self) -> None:  # noqa: B027  (optional hook, intentionally empty)
        """Release any resources (HTTP clients etc). Optional override."""

    def __enter__(self) -> Scraper:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
