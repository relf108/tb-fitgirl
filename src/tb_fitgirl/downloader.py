"""Download torrent contents from TorBox to the local disk."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path, PurePosixPath

import httpx

from .models import Torrent, TorrentFile
from .torbox import TorboxClient, TorboxError

DEFAULT_TIMEOUT = 60.0
CHUNK_SIZE = 1024 * 256

# on_progress(file, bytes_done, bytes_total)
ProgressFn = Callable[[TorrentFile, int, int], None]


def safe_relpath(name: str) -> Path:
    """Turn a torrent file path into a safe relative path (no .., no absolute)."""
    parts = [p for p in PurePosixPath(name).parts if p not in ("", "/", "..", ".")]
    if not parts:
        raise ValueError(f"Unusable file path in torrent: {name!r}")
    return Path(*parts)


class Downloader:
    def __init__(
        self,
        client: TorboxClient,
        dest_dir: Path | str,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        self._tb = client
        self.dest_dir = Path(dest_dir)
        self._http = httpx.Client(timeout=timeout, follow_redirects=True)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> Downloader:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def wait_ready(
        self,
        torrent_id: int,
        *,
        timeout: float = 120.0,
        interval: float = 3.0,
        on_poll: Callable[[Torrent], None] | None = None,
    ) -> Torrent:
        """Poll mylist until the torrent's download is present on TorBox's servers.

        A freshly created torrent may take a moment to appear in the list at
        all; absence is only an error once the deadline passes.
        """
        deadline = time.monotonic() + timeout
        torrent: Torrent | None = None
        while True:
            torrents = self._tb.my_list(torrent_id=torrent_id, bypass_cache=True)
            if torrents:
                torrent = torrents[0]
                if torrent.ready:
                    return torrent
                if on_poll:
                    on_poll(torrent)
            if time.monotonic() >= deadline:
                if torrent is None:
                    raise TorboxError(f"Torrent id {torrent_id} not found in your account.")
                raise TorboxError(
                    f"Timed out waiting for '{torrent.name}' to become downloadable "
                    f"(state: {torrent.download_state}, progress: {torrent.progress:.0%})."
                )
            time.sleep(interval)

    def download_file(
        self, torrent: Torrent, file: TorrentFile, on_progress: ProgressFn | None = None
    ) -> Path:
        """Stream one file to ``dest_dir``; returns its path. Skips complete files."""
        target = self.dest_dir / safe_relpath(file.name)
        if target.exists() and file.size and target.stat().st_size == file.size:
            if on_progress:
                on_progress(file, file.size, file.size)
            return target

        link = self._tb.request_download_link(torrent.id, file_id=file.id)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".part")
        done = 0
        with self._http.stream("GET", link) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("Content-Length") or file.size or 0)
            with tmp.open("wb") as fh:
                for chunk in resp.iter_bytes(CHUNK_SIZE):
                    fh.write(chunk)
                    done += len(chunk)
                    if on_progress:
                        on_progress(file, done, total)
        tmp.rename(target)
        return target

    def download_torrent(
        self, torrent: Torrent, on_progress: ProgressFn | None = None
    ) -> list[Path]:
        """Download every file in the torrent; returns their paths."""
        return [self.download_file(torrent, f, on_progress) for f in torrent.files]
