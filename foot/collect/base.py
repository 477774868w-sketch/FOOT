"""The provider contract, and the capability probe that keeps it honest.

A provider name in a configuration file is not an integration.  Every provider
here must answer :meth:`Provider.probe` with what it can *actually* reach right
now, and the analysis engine consults that answer before it promises anything.

The separation is deliberate: this package talks to the outside world and
returns :class:`~foot.provenance.Evidence`; the modelling packages never make a
network call and never see a URL.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, runtime_checkable

from foot.domain import Fixture, MatchLog
from foot.provenance import Evidence, Source, utcnow

__all__ = [
    "Capability",
    "CollectionError",
    "Provider",
    "ProviderBlockedError",
    "ProviderStatus",
    "Reachability",
    "ResultSet",
    "SeasonData",
    "SeasonSource",
]


class Capability(Enum):
    """What a provider can supply.  Claimed capabilities are probed, not trusted."""

    RESULTS = "résultats historiques"
    FIXTURES = "rencontres à venir"
    LINEUPS = "compositions"
    INJURIES = "absences et blessures"
    ADVANCED_STATS = "statistiques avancées (xG, tirs)"
    ODDS = "cotes"
    WEATHER = "météo"
    REFEREE = "arbitre"


class Reachability(Enum):
    """The outcome of probing a provider."""

    OK = "accessible"
    BLOCKED = "bloqué par la politique réseau"
    AUTH_REQUIRED = "clé d'API requise"
    ERROR = "erreur"
    NOT_PROBED = "non testé"

    @property
    def usable(self) -> bool:
        return self is Reachability.OK


class CollectionError(RuntimeError):
    """A provider could not satisfy a request."""


class ProviderBlockedError(CollectionError):
    """The network refused the connection — an environment fact, not a bug.

    Raised for egress-policy denials so that callers can report the blocker
    precisely instead of degrading into invented data.
    """


@dataclass(frozen=True, slots=True)
class ProviderStatus:
    """What one provider can actually do, as measured."""

    provider: str
    reachability: Reachability
    capabilities: frozenset[Capability]
    checked_at: dt.datetime
    detail: str = ""
    coverage: tuple[str, ...] = ()
    """Competition keys the provider is known to serve, as verified."""

    requires: tuple[str, ...] = ()
    """External prerequisites still to be configured, such as an API key."""

    @property
    def usable(self) -> bool:
        return self.reachability.usable

    def render(self) -> str:
        marks = {
            Reachability.OK: "OK",
            Reachability.BLOCKED: "BLOQUÉ",
            Reachability.AUTH_REQUIRED: "CLÉ REQUISE",
            Reachability.ERROR: "ERREUR",
            Reachability.NOT_PROBED: "NON TESTÉ",
        }
        caps = ", ".join(sorted(c.value for c in self.capabilities)) or "—"
        line = f"{self.provider:<22} {marks[self.reachability]:<12} {caps}"
        if self.detail:
            line += f"\n{'':<22} {self.detail}"
        if self.requires:
            line += f"\n{'':<22} prérequis : {', '.join(self.requires)}"
        if self.coverage:
            line += f"\n{'':<22} couverture vérifiée : {', '.join(self.coverage)}"
        return line


@dataclass(frozen=True, slots=True)
class ResultSet:
    """Matches returned by a provider, with the evidence that documents them."""

    matches: MatchLog
    evidence: tuple[Evidence, ...] = ()
    source: Source | None = None
    retrieved_at: dt.datetime = field(default_factory=utcnow)

    def __len__(self) -> int:
        return len(self.matches)


@dataclass(frozen=True, slots=True)
class SeasonData:
    """One competition-season, split into what has been played and what has not."""

    competition: str
    season: str
    label: str
    played: MatchLog
    fixtures: tuple[Fixture, ...]
    retrieved_at: dt.datetime
    url: str = ""

    @property
    def teams(self) -> tuple[str, ...]:
        names = set(self.played.teams)
        names.update(t for f in self.fixtures for t in f.teams)
        return tuple(sorted(names))


@runtime_checkable
class SeasonSource(Protocol):
    """A provider that can serve a whole competition-season at once.

    The analysis engine depends on *this*, not on any concrete adapter, so a new
    source — or a stub in a test — plugs in without the engine knowing its type.
    """

    @property
    def name(self) -> str:
        """Identifier used in reports."""

    @property
    def capabilities(self) -> frozenset[Capability]:
        """What this provider offers."""

    def competitions(self) -> Sequence[str]:
        """Competition keys this provider serves."""

    def season(self, competition: str, season: str) -> SeasonData:
        """Results and remaining fixtures for one competition-season."""


@runtime_checkable
class Provider(Protocol):
    """A source of football facts.

    Implementations must be side-effect free apart from network access and
    caching, and must never fabricate a value: an absent fact is reported as
    absent, which is what lets the analysis say so in the report.
    """

    @property
    def name(self) -> str:
        """Identifier used in reports and in the provider table."""

    @property
    def upstream(self) -> str:
        """The provider that originates the data, for independence counting."""

    @property
    def capabilities(self) -> frozenset[Capability]:
        """What this provider claims to offer, before probing."""

    def probe(self) -> ProviderStatus:
        """Measure what this provider can actually reach, now."""

    def competitions(self) -> Sequence[str]:
        """Competition keys this provider serves."""

    def results(self, competition: str, season: str) -> ResultSet:
        """Played matches for a competition and season.

        Raises:
            ProviderBlockedError: the network refused the request.
            CollectionError: the provider cannot serve this request.
        """
