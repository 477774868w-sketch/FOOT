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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, runtime_checkable

from foot.collect.supplements import AbsenceRow, LineupRow, XgRow
from foot.domain import Fixture, MatchLog
from foot.market.odds import MatchOdds
from foot.provenance import Evidence, Source, utcnow

__all__ = [
    "AbsenceSource",
    "Capability",
    "ClosingSource",
    "CollectionError",
    "LineupSource",
    "MarketSource",
    "OddsSource",
    "Provider",
    "ProviderBlockedError",
    "ProviderStatus",
    "Reachability",
    "ResultSet",
    "SeasonData",
    "SeasonSource",
    "XgSource",
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
class LineupSource(Protocol):
    """A provider that can serve **team sheets for one fixture**.

    Kept apart from :class:`SeasonSource` because a sheet is not season data: it
    appears an hour before kick-off, it can be probable then official, and it is
    the one datum the engine must be able to re-read *during* a run rather than
    once at the start.

    What comes back is exactly what a pasted ``--compositions-csv`` produces, so
    an automatically collected sheet and a hand-typed one travel the same road
    through the cut, the scenarios and the rubrics.  A source that has nothing
    returns no row and says so in its evidence — « rien publié » and « ce plan
    ne le sert pas » are different answers and both are worth reading.
    """

    @property
    def name(self) -> str:
        """Identifier used in reports."""

    def team_sheets(
        self, fixture: Fixture
    ) -> tuple[Sequence[LineupRow], Sequence[Evidence]]:
        """Sheets known for one fixture, with the evidence that found them."""


@runtime_checkable
class AbsenceSource(Protocol):
    """A provider that can serve **reported absences for one fixture**.

    Kept apart from :class:`LineupSource` because the two answer different
    questions at different moments: an absence list exists days before a match,
    a team sheet an hour before it. A plan can serve one and refuse the other.
    """

    @property
    def name(self) -> str:
        """Identifier used in reports."""

    def absences(
        self, fixture: Fixture
    ) -> tuple[Sequence[AbsenceRow], Sequence[Evidence]]:
        """Absences claimed for one fixture, with the evidence that found them."""


@runtime_checkable
class XgSource(Protocol):
    """A provider that can serve advanced statistics for **played** matches.

    The engine passes the past fixtures it wants covered — never the upcoming
    one. Reading the statistics of a match that has not been played would either
    return nothing or, worse, return a partial live line, and a forecast must not
    be built on either.
    """

    @property
    def name(self) -> str:
        """Identifier used in reports."""

    def xg_rows(
        self, fixtures: Sequence[Fixture]
    ) -> tuple[Sequence[XgRow], Sequence[Evidence]]:
        """Expected goals for those played fixtures, as supplement rows."""


@runtime_checkable
class MarketSource(Protocol):
    """A provider that can quote **several markets**, each with its own price.

    Broader than :class:`OddsSource`, which only carries 1–N–2. A source that
    also quotes totals and handicaps can feed the whole comparison, and every
    price arrives with the bookmaker that gave it and the instant it was seen —
    the two facts the selector cannot do without.

    Consulted only after the sport dossier is sealed, like any price source.
    """

    @property
    def name(self) -> str:
        """Identifier used in reports."""

    def market_prices(
        self, fixture: Fixture
    ) -> Mapping[str, tuple[float, dt.datetime | None, str]]:
        """``{clé de marché: (cote, heure de relevé, bookmaker)}`` for one fixture."""


@runtime_checkable
class OddsSource(Protocol):
    """A provider that can quote prices for fixtures.

    Kept separate from :class:`SeasonSource` on purpose: the engine consults a
    price source **only after the sport dossier is sealed**, so the two contracts
    must not be reachable through one object the sport phase already holds.
    """

    @property
    def name(self) -> str:
        """Identifier used in reports."""

    @property
    def capabilities(self) -> frozenset[Capability]:
        """What this provider offers."""

    def odds(self) -> tuple[dict[Fixture, MatchOdds], list[Evidence]]:
        """Prices this provider can quote, with the evidence naming their origin."""

    def quoted_at(self) -> dt.datetime | None:
        """When those prices were observed, when the source knows it."""


@runtime_checkable
class ClosingSource(Protocol):
    """A source of **archived closing prices**, addressable by competition-season.

    Distinct from :class:`OddsSource`, whose ``odds()`` takes no argument and
    means "whatever you can quote right now". An archive must be *asked* for a
    competition and a season: calling it bare let it answer with its own
    defaults, so a request for Serie A 2026-27 came back with English prices from
    two seasons earlier — silently, and with the wrong identities attached.
    """

    @property
    def name(self) -> str:
        """Identifier used in reports."""

    def competitions(self) -> Sequence[str]:
        """Division codes this archive serves."""

    def odds(
        self, competition: str, season: str
    ) -> tuple[dict[Fixture, MatchOdds], list[Evidence]]:
        """Closing prices for one competition-season."""


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
