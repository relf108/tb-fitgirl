"""TorBox main API client (https://api.torbox.app/v1/api).

Used for cache checks and adding torrents. Auth is the TorBox API key
as a Bearer token.
"""

from __future__ import annotations

import os
import re
from typing import Any

import httpx

from .models import CacheStatus, Torrent

MAIN_API = "https://api.torbox.app/v1/api"

DEFAULT_TIMEOUT = 30.0


class TorboxError(RuntimeError):
    """Raised when the TorBox API returns an error."""

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.code = code


class TorboxClient:
    def __init__(self, api_key: str | None = None, timeout: float = DEFAULT_TIMEOUT):
        self.api_key = api_key or os.environ.get("TORBOX_API_KEY", "")
        if not self.api_key:
            raise TorboxError("No API key. Set TORBOX_API_KEY or pass api_key to TorboxClient.")
        self._client = httpx.Client(
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=timeout,
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> TorboxClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- internals ----------------------------------------------------------

    def _request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        resp = self._client.request(method, url, **kwargs)
        if resp.status_code == 403:
            raise TorboxError("Authentication failed (403). Check your API key.")
        try:
            payload = resp.json()
        except ValueError as err:
            raise TorboxError(f"Non-JSON response from {url} (HTTP {resp.status_code})") from err
        if resp.status_code == 429:
            detail = ""
            if isinstance(payload, dict):
                detail = payload.get("error") or payload.get("detail") or ""
            if re.search(r"(?<!\d)0 per", str(detail)):
                raise TorboxError(
                    f"Endpoint not available on your plan (rate limit is zero): {url}"
                )
            raise TorboxError(f"Rate limited: {detail}")
        if resp.status_code >= 400 or (
            isinstance(payload, dict) and payload.get("success") is False
        ):
            detail = code = None
            if isinstance(payload, dict):
                detail = payload.get("detail")
                code = payload.get("error")
            raise TorboxError(detail or code or f"API error (HTTP {resp.status_code})", code=code)
        return payload if isinstance(payload, dict) else {"data": payload}

    # -- endpoints ----------------------------------------------------------

    def me(self) -> dict[str, Any]:
        """Account details for the key's owner (also serves as key validation).

        Returns the raw ``data`` object from ``GET /user/me`` (plan, email,
        premium_expires_at, ...). Raises TorboxError on a bad key.
        """
        payload = self._request("GET", f"{MAIN_API}/user/me")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise TorboxError("Unexpected response from /user/me.")
        return data

    def check_cached(self, hashes: list[str]) -> dict[str, CacheStatus]:
        """Return {hash: CacheStatus} for the given info-hashes."""
        if not hashes:
            return {}
        payload = self._request(
            "GET",
            f"{MAIN_API}/torrents/checkcached",
            params={"hash": ",".join(h.lower() for h in hashes), "format": "object"},
        )
        data = payload.get("data") or {}
        statuses: dict[str, CacheStatus] = {}
        for h in (h.lower() for h in hashes):
            entry = data.get(h)
            if entry:
                statuses[h] = CacheStatus(
                    hash=h,
                    cached=True,
                    name=entry.get("name"),
                    size=int(entry.get("size") or 0),
                )
            else:
                statuses[h] = CacheStatus(hash=h, cached=False)
        return statuses

    def my_list(
        self, *, torrent_id: int | None = None, bypass_cache: bool = False
    ) -> list[Torrent]:
        """Torrents in the user's account. ``bypass_cache`` forces fresh state."""
        params: dict[str, str] = {}
        if torrent_id is not None:
            params["id"] = str(torrent_id)
        if bypass_cache:
            params["bypass_cache"] = "true"
        payload = self._request("GET", f"{MAIN_API}/torrents/mylist", params=params)
        data = payload.get("data") or []
        if isinstance(data, dict):  # id= returns a single object
            data = [data]
        return [Torrent.from_api(raw) for raw in data]

    def request_download_link(self, torrent_id: int, *, file_id: int | None = None) -> str:
        """Presigned CDN link for a file (or the whole torrent as zip if no file_id).

        Note: this endpoint authenticates via the ``token`` query param.
        Links are valid to *start* a download for 3 hours.
        """
        params: dict[str, str] = {"token": self.api_key, "torrent_id": str(torrent_id)}
        if file_id is not None:
            params["file_id"] = str(file_id)
        else:
            params["zip_link"] = "true"
        payload = self._request("GET", f"{MAIN_API}/torrents/requestdl", params=params)
        link = payload.get("data")
        if not isinstance(link, str) or not link:
            raise TorboxError("No download link returned.")
        return link

    def create_torrent(
        self,
        magnet: str,
        *,
        name: str | None = None,
        only_if_cached: bool = False,
    ) -> dict[str, Any]:
        """Add a magnet to the user's TorBox account.

        With ``only_if_cached`` TorBox refuses (error code DOWNLOAD_NOT_CACHED)
        instead of starting a real download when the torrent isn't cached.
        """
        form: dict[str, str] = {"magnet": magnet}
        if name:
            form["name"] = name
        if only_if_cached:
            form["add_only_if_cached"] = "true"
        payload = self._request("POST", f"{MAIN_API}/torrents/createtorrent", data=form)
        return payload.get("data") or {}
