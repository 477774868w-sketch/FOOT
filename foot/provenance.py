"""Provenance: where every fact came from, when, and how much it can be trusted.

A football analysis is only as defensible as its inputs, so nothing enters the
system as a bare number.  Every fact is an :class:`Evidence` carrying its value,
its unit, the source consulted, the *upstream* provider that originated it, the
date of the fact, the publication date when known, the retrieval date, and a
status.

Two rules drive the design, and both are enforced rather than documented:

* **Two sites republishing one provider are one confirmation, not two.**
  :meth:`Ledger.independent_sources` counts distinct upstreams, not distinct
  URLs, so a fact echoed by five aggregators of the same feed stays single
  sourced.
* **Absence is not evidence of absence.**  A player missing from an injury list
  is :attr:`Confidence.UNAVAILABLE`, never :attr:`Confidence.CONFIRMED`
  available.  The enum has no value meaning "we looked and found nothing, so it
  is fine".
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from enum import Enum

__all__ = [
    "Confidence",
    "Evidence",
    "Ledger",
    "Source",
    "fingerprint",
]


class Confidence(Enum):
    """How firmly a fact is established."""

    CONFIRMED = "confirmé"
    """Established by a direct official source, or by independent agreement."""

    PROBABLE = "probable"
    """Reported, but not officially confirmed — a probable lineup, for instance."""

    CONTRADICTED = "contradictoire"
    """Independent sources disagree.  The disagreement stays visible."""

    UNAVAILABLE = "indisponible"
    """Not obtainable.  Never silently replaced by a default or a guess."""

    @property
    def is_usable(self) -> bool:
        """Whether a modelling step may consume this fact without a caveat."""
        return self in (Confidence.CONFIRMED, Confidence.PROBABLE)


@dataclass(frozen=True, slots=True)
class Source:
    """A consulted source, distinguished from the provider that originated it."""

    name: str
    """The site or endpoint actually consulted."""

    provider: str
    """The upstream originator of the data.

    Aggregators must name the feed they republish here.  Independence is counted
    on this field, which is what stops an echo chamber from looking like
    corroboration.
    """

    url: str | None = None
    official: bool = False
    """True only for a club, league or federation publishing its own facts."""

    def __post_init__(self) -> None:
        if not self.name or not self.provider:
            raise ValueError("a source needs both a name and an upstream provider")

    def __str__(self) -> str:
        mark = " (officiel)" if self.official else ""
        if self.provider != self.name:
            return f"{self.name} via {self.provider}{mark}"
        return f"{self.name}{mark}"


@dataclass(frozen=True, slots=True)
class Evidence:
    """One fact, with everything needed to audit it later."""

    key: str
    value: object
    source: Source
    retrieved_at: dt.datetime
    status: Confidence = Confidence.CONFIRMED
    unit: str | None = None
    fact_date: dt.date | None = None
    published_at: dt.datetime | None = None
    note: str | None = None

    def __post_init__(self) -> None:
        if not self.key:
            raise ValueError("evidence needs a key")
        if self.retrieved_at.tzinfo is None:
            raise ValueError(
                f"retrieved_at must be timezone aware, got {self.retrieved_at!r}"
            )
        if self.published_at is not None and self.published_at.tzinfo is None:
            raise ValueError("published_at must be timezone aware when given")
        if self.status is Confidence.UNAVAILABLE and self.value is not None:
            raise ValueError(
                "an unavailable fact carries no value; that is the point of the status"
            )

    @property
    def age_days(self) -> float:
        """Days between the fact and its retrieval — staleness, measured."""
        if self.fact_date is None:
            return float("nan")
        delta = self.retrieved_at.date() - self.fact_date
        return float(delta.days)

    def render(self) -> str:
        """One line, in French, suitable for a report's source list."""
        value = "—" if self.value is None else f"{self.value}"
        if self.unit:
            value = f"{value} {self.unit}"
        parts = [f"{self.key} = {value}", f"[{self.status.value}]", f"source : {self.source}"]
        if self.fact_date:
            parts.append(f"fait du {self.fact_date.isoformat()}")
        if self.published_at:
            parts.append(f"publié {self.published_at.strftime('%Y-%m-%d %H:%M %Z')}")
        parts.append(f"relevé {self.retrieved_at.strftime('%Y-%m-%d %H:%M %Z')}")
        return " · ".join(parts)


@dataclass(slots=True)
class Ledger:
    """The evidence gathered for one analysis, queryable and auditable."""

    entries: list[Evidence] = field(default_factory=list)

    def add(self, evidence: Evidence) -> Evidence:
        self.entries.append(evidence)
        return evidence

    def extend(self, items: Iterable[Evidence]) -> None:
        for item in items:
            self.add(item)

    def __iter__(self) -> Iterator[Evidence]:
        return iter(self.entries)

    def __len__(self) -> int:
        return len(self.entries)

    def for_key(self, key: str) -> list[Evidence]:
        return [e for e in self.entries if e.key == key]

    def keys(self) -> list[str]:
        seen: dict[str, None] = {}
        for entry in self.entries:
            seen.setdefault(entry.key, None)
        return list(seen)

    def independent_sources(self, key: str) -> int:
        """Number of *distinct upstream providers* backing a fact.

        Counting URLs would let one feed, republished five times, masquerade as
        five confirmations.  Counting providers does not.
        """
        return len({e.source.provider for e in self.for_key(key) if e.status.is_usable})

    def has_official(self, key: str) -> bool:
        return any(e.source.official and e.status.is_usable for e in self.for_key(key))

    def contradictions(self) -> dict[str, list[Evidence]]:
        """Keys whose independent providers report different values.

        A single official source establishes a fact on its own; a disagreement
        between independent providers is surfaced, never averaged away.
        """
        found: dict[str, list[Evidence]] = {}
        for key in self.keys():
            usable = [e for e in self.for_key(key) if e.status.is_usable]
            by_provider: dict[str, object] = {}
            conflicting = False
            for entry in usable:
                previous = by_provider.get(entry.source.provider, _MISSING)
                if previous is _MISSING:
                    by_provider[entry.source.provider] = entry.value
                elif previous != entry.value:
                    conflicting = True
            if len({_hashable(v) for v in by_provider.values()}) > 1 or conflicting:
                found[key] = usable
        return found

    def unavailable_keys(self) -> list[str]:
        return [
            key
            for key in self.keys()
            if all(e.status is Confidence.UNAVAILABLE for e in self.for_key(key))
        ]

    def resolve(self, key: str) -> Evidence | None:
        """The best available entry for a key.

        Official beats unofficial; confirmed beats probable; the most recently
        retrieved breaks the remaining ties.  Returns ``None`` when nothing
        usable exists, which callers must handle rather than defaulting.
        """
        usable = [e for e in self.for_key(key) if e.status.is_usable]
        if not usable:
            return None
        return max(
            usable,
            key=lambda e: (
                e.source.official,
                e.status is Confidence.CONFIRMED,
                e.retrieved_at,
            ),
        )

    def summary(self) -> str:
        lines = [f"{len(self.entries)} éléments de preuve, {len(self.keys())} faits distincts"]
        contradictions = self.contradictions()
        if contradictions:
            lines.append(f"  contradictions non résolues : {', '.join(sorted(contradictions))}")
        missing = self.unavailable_keys()
        if missing:
            lines.append(f"  indisponibles : {', '.join(sorted(missing))}")
        return "\n".join(lines)


class _Missing:
    __slots__ = ()


_MISSING = _Missing()


def _hashable(value: object) -> object:
    """Best-effort hashable projection so values can be compared in a set."""
    try:
        hash(value)
    except TypeError:
        return json.dumps(value, sort_keys=True, default=str)
    return value


def fingerprint(payload: object) -> str:
    """A stable SHA-256 digest of any JSON-encodable structure.

    Used to seal a sport dossier before the market is consulted.  The digest is
    a *trace*: it proves what the inputs were, and it is deliberately not the
    mechanism that enforces odds-blindness — that is a separate, tested barrier
    in :mod:`foot.analysis.dossier`.
    """
    encoded = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def utcnow() -> dt.datetime:
    """Timezone-aware present instant; the single clock the system reads."""
    return dt.datetime.now(dt.timezone.utc)


def latest_retrieval(entries: Sequence[Evidence]) -> dt.datetime | None:
    return max((e.retrieved_at for e in entries), default=None)
