"""Shared data models."""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field


@dataclass
class CacheStatus:
    """Cache state of a single info-hash on TorBox."""

    hash: str
    cached: bool
    name: str | None = None
    size: int = 0  # bytes

    @property
    def size_human(self) -> str:
        return human_size(self.size)


@dataclass
class Repack:
    """A repack post scraped from some source site."""

    title: str
    url: str
    magnets: list[str] = field(default_factory=list)
    source: str = ""

    @property
    def primary_magnet(self) -> str | None:
        return self.magnets[0] if self.magnets else None

    @property
    def hashes(self) -> list[str]:
        found = []
        for magnet in self.magnets:
            infohash = magnet_hash(magnet)
            if infohash and infohash not in found:
                found.append(infohash)
        return found


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} TB"


def magnet_hash(magnet: str) -> str | None:
    """Extract the btih info-hash from a magnet URI as lowercase hex.

    Handles both hex (40 chars) and base32 (32 chars, converted to hex)
    encodings. Returns None if no valid btih hash is present.
    """
    marker = "urn:btih:"
    idx = magnet.find(marker)
    if idx == -1:
        return None
    start = idx + len(marker)
    end = len(magnet)
    for sep in ("&", "?"):
        pos = magnet.find(sep, start)
        if pos != -1:
            end = min(end, pos)
    infohash = magnet[start:end].strip().lower()
    if re.fullmatch(r"[0-9a-f]{40}", infohash):
        return infohash
    if re.fullmatch(r"[a-z2-7]{32}", infohash):
        return base64.b32decode(infohash.upper()).hex()
    return None
