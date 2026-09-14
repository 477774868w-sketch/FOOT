"""A dated on-disk cache.

Collection must be reproducible and polite to its sources, so every fetch is
stored with the instant it was made.  The stored timestamp is not decoration:
it becomes the ``retrieved_at`` of every piece of evidence derived from the
payload, so a report can always state how old its inputs were.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from foot.provenance import utcnow

__all__ = ["Cache", "CacheEntry"]


@dataclass(frozen=True, slots=True)
class CacheEntry:
    key: str
    payload: object
    retrieved_at: dt.datetime
    url: str | None = None

    def age(self, *, now: dt.datetime | None = None) -> dt.timedelta:
        return (now or utcnow()) - self.retrieved_at


class Cache:
    """A JSON file cache keyed by URL, with an explicit time-to-live.

    Set ``ttl_seconds`` to zero to force a refetch, or point ``directory`` at a
    throwaway path in tests.  Nothing here is silent: :meth:`load` returns
    ``None`` for a miss *and* for an expired entry, so callers always know
    whether they are about to use the network.
    """

    __slots__ = ("_directory", "_ttl")

    def __init__(self, directory: str | Path, *, ttl_seconds: float = 6 * 3600) -> None:
        if ttl_seconds < 0:
            raise ValueError("ttl_seconds must be non-negative")
        self._directory = Path(directory)
        self._ttl = ttl_seconds

    @property
    def ttl_seconds(self) -> float:
        return self._ttl

    def with_ttl(self, ttl_seconds: float) -> Cache:
        """The same store, read with a different freshness rule.

        An hour before kick-off, a six-hour cache is not a cache but a
        blindfold: a T−75 response saying « no sheet yet » would still be
        served at T−60, and the second check would read the first one's answer
        without ever asking again. Same directory, so nothing is re-downloaded
        that is genuinely fresh.
        """
        return Cache(self._directory, ttl_seconds=ttl_seconds)

    @property
    def directory(self) -> Path:
        return self._directory

    @staticmethod
    def key_for(url: str) -> str:
        return hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]

    def _path(self, key: str) -> Path:
        return self._directory / f"{key}.json"

    def load(self, url: str, *, now: dt.datetime | None = None) -> CacheEntry | None:
        """Return a fresh cached entry, or ``None`` if missing or expired."""
        path = self._path(self.key_for(url))
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            retrieved_at = dt.datetime.fromisoformat(raw["retrieved_at"])
        except (json.JSONDecodeError, KeyError, ValueError, OSError):
            return None  # a corrupt entry is a miss, never a crash
        if retrieved_at.tzinfo is None:
            return None
        entry = CacheEntry(
            key=raw.get("key", ""),
            payload=raw.get("payload"),
            retrieved_at=retrieved_at,
            url=raw.get("url"),
        )
        if self._ttl and entry.age(now=now).total_seconds() > self._ttl:
            return None
        return entry

    def store(self, url: str, payload: object, retrieved_at: dt.datetime) -> CacheEntry:
        key = self.key_for(url)
        self._directory.mkdir(parents=True, exist_ok=True)
        entry = CacheEntry(key=key, payload=payload, retrieved_at=retrieved_at, url=url)
        self._path(key).write_text(
            json.dumps(
                {
                    "key": key,
                    "url": url,
                    "retrieved_at": retrieved_at.isoformat(),
                    "payload": payload,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return entry

    def clear(self) -> int:
        """Delete every cached entry; returns how many were removed."""
        if not self._directory.exists():
            return 0
        removed = 0
        for path in self._directory.glob("*.json"):
            path.unlink()
            removed += 1
        return removed
