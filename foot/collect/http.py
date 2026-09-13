"""A minimal, dependency-free HTTP client with retries and honest failures.

Written on :mod:`urllib` so that the collection layer adds no dependency.  Its
one job beyond fetching is to *classify* failures: an egress-policy denial is
reported as :class:`~foot.collect.base.ProviderBlockedError` rather than as a generic
error, because the two demand completely different responses from the operator.
"""

from __future__ import annotations

import datetime as dt
import http.client
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from foot.collect.base import CollectionError, ProviderBlockedError
from foot.provenance import utcnow

__all__ = ["Response", "fetch", "fetch_json"]

_USER_AGENT = "foot/2.0 (+https://github.com/openfootball; analyse football)"
_BLOCKED_MARKERS = (
    "tunnel connection failed",
    "connect tunnel failed",
    "forbidden by proxy",
    "proxy authentication",
)


@dataclass(frozen=True, slots=True)
class Response:
    url: str
    status: int
    body: bytes
    retrieved_at: dt.datetime

    def text(self, encoding: str = "utf-8") -> str:
        return self.body.decode(encoding, errors="replace")

    def json(self) -> object:
        return json.loads(self.text())


def _classify(error: Exception, url: str) -> CollectionError:
    """Turn a transport failure into the most specific error we can justify."""
    message = str(error).lower()
    if isinstance(error, urllib.error.HTTPError):
        if error.code in (401, 403):
            return ProviderBlockedError(
                f"{url} : HTTP {error.code} — accès refusé "
                f"(politique réseau ou authentification manquante)"
            )
        return CollectionError(f"{url} : HTTP {error.code}")
    if any(marker in message for marker in _BLOCKED_MARKERS):
        return ProviderBlockedError(
            f"{url} : le proxy de sortie a refusé la connexion "
            f"(politique d'organisation) — {error}"
        )
    return CollectionError(f"{url} : {type(error).__name__} — {error}")


def fetch(
    url: str,
    *,
    timeout: float = 30.0,
    retries: int = 3,
    backoff: float = 1.5,
    headers: dict[str, str] | None = None,
    sleep: object = time.sleep,
) -> Response:
    """GET ``url``, retrying transient failures with exponential backoff.

    Policy denials are **not** retried: repeating a refused request wastes time
    and tells the operator nothing new.

    Args:
        sleep: injected so tests can run the retry path without waiting.
    """
    if retries < 1:
        raise ValueError("retries must be at least 1")
    request = urllib.request.Request(
        url, headers={"User-Agent": _USER_AGENT, **(headers or {})}
    )
    if not url.startswith("https://"):
        raise ValueError(f"only https URLs are collected, got {url!r}")

    last: CollectionError | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as handle:
                return Response(
                    url=url,
                    status=int(handle.status),
                    body=handle.read(),
                    retrieved_at=utcnow(),
                )
        except (urllib.error.URLError, http.client.HTTPException, OSError) as error:
            last = _classify(error, url)
            if isinstance(last, ProviderBlockedError):
                raise last from error
            if attempt < retries - 1:
                sleep(backoff**attempt)  # type: ignore[operator]
    raise last if last is not None else CollectionError(f"{url} : échec inconnu")


def fetch_json(url: str, **kwargs: object) -> tuple[object, dt.datetime]:
    """Fetch and decode JSON, returning the payload and its retrieval time."""
    response = fetch(url, **kwargs)  # type: ignore[arg-type]
    try:
        return response.json(), response.retrieved_at
    except json.JSONDecodeError as error:
        raise CollectionError(f"{url} : réponse JSON invalide — {error}") from error
