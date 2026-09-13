"""football-data.co.uk — historical results *and* closing odds.

A real adapter for the de facto standard archive of European league results.
Its files carry bookmaker columns, which makes it the natural odds source for
backtesting, and the existing CSV reader already understands the layout.

In the sandbox this library was developed in, the host is refused by the egress
proxy, and :meth:`probe` reports exactly that.  The adapter is shipped anyway
because the blocker is environmental, not a property of the code: run it where
the host is reachable and it works.  It is never presented as operational
without a probe that says so.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Sequence

from foot.collect.base import (
    Capability,
    CollectionError,
    ProviderBlockedError,
    ProviderStatus,
    Reachability,
    ResultSet,
    SeasonData,
)
from foot.collect.cache import Cache
from foot.collect.http import fetch
from foot.data.csv_source import parse_date
from foot.domain import Fixture, Match, MatchLog, Score
from foot.market.odds import MatchOdds
from foot.provenance import Confidence, Evidence, Source, utcnow

__all__ = ["DIVISIONS", "FootballDataProvider"]

_BASE = "https://www.football-data.co.uk/mmz4281"

DIVISIONS: dict[str, str] = {
    "E0": "Premier League (Angleterre)",
    "E1": "Championship (Angleterre)",
    "SP1": "Primera División (Espagne)",
    "D1": "Bundesliga (Allemagne)",
    "I1": "Serie A (Italie)",
    "F1": "Ligue 1 (France)",
    "N1": "Eredivisie (Pays-Bas)",
    "P1": "Primeira Liga (Portugal)",
}


def season_code(season: str) -> str:
    """``"2024-25"`` becomes ``"2425"``, the layout the archive uses."""
    parts = season.replace("/", "-").split("-")
    if len(parts) != 2:
        raise CollectionError(f"saison illisible : {season!r} (attendu 2024-25)")
    start, end = parts[0][-2:], parts[1][-2:]
    if not (start.isdigit() and end.isdigit()):
        raise CollectionError(f"saison illisible : {season!r}")
    return f"{start}{end}"


class FootballDataProvider:
    """Results with bookmaker odds, for the divisions listed in :data:`DIVISIONS`."""

    __slots__ = ("_bookmaker", "_cache", "_timeout")

    def __init__(
        self,
        cache: Cache | None = None,
        *,
        bookmaker: str = "B365",
        timeout: float = 30.0,
    ) -> None:
        self._cache = cache
        self._bookmaker = bookmaker
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "football-data.co.uk"

    @property
    def upstream(self) -> str:
        return "football-data.co.uk"

    @property
    def capabilities(self) -> frozenset[Capability]:
        return frozenset({Capability.RESULTS, Capability.ODDS, Capability.REFEREE})

    def competitions(self) -> Sequence[str]:
        return tuple(DIVISIONS)

    def url_for(self, division: str, season: str) -> str:
        if division not in DIVISIONS:
            raise CollectionError(f"division inconnue : {division!r}")
        return f"{_BASE}/{season_code(season)}/{division}.csv"

    def source(self, url: str) -> Source:
        return Source(name="football-data.co.uk", provider=self.upstream, url=url)

    def probe(self, *, division: str = "E0", season: str = "2024-25") -> ProviderStatus:
        url = self.url_for(division, season)
        checked_at = utcnow()
        try:
            response = fetch(url, timeout=self._timeout, retries=1)
        except ProviderBlockedError as error:
            return ProviderStatus(
                provider=self.name,
                reachability=Reachability.BLOCKED,
                capabilities=self.capabilities,
                checked_at=checked_at,
                detail=str(error),
                requires=("accès réseau sortant vers www.football-data.co.uk",),
            )
        except CollectionError as error:
            return ProviderStatus(
                provider=self.name,
                reachability=Reachability.ERROR,
                capabilities=self.capabilities,
                checked_at=checked_at,
                detail=str(error),
            )
        return ProviderStatus(
            provider=self.name,
            reachability=Reachability.OK,
            capabilities=self.capabilities,
            checked_at=checked_at,
            coverage=(f"{division} {season} ({len(response.body)} octets)",),
        )

    def _rows(self, division: str, season: str) -> tuple[list[dict[str, str]], object]:
        url = self.url_for(division, season)
        if self._cache is not None:
            cached = self._cache.load(url)
            if cached is not None and isinstance(cached.payload, str):
                return list(csv.DictReader(io.StringIO(cached.payload))), cached.retrieved_at
        response = fetch(url, timeout=self._timeout)
        text = response.text("utf-8-sig")
        if self._cache is not None:
            self._cache.store(url, text, response.retrieved_at)
        return list(csv.DictReader(io.StringIO(text))), response.retrieved_at

    def results(self, competition: str = "E0", season: str = "2024-25") -> ResultSet:
        rows, retrieved_at = self._rows(competition, season)
        url = self.url_for(competition, season)
        source = self.source(url)
        matches: list[Match] = []
        for row in rows:
            try:
                date = parse_date(row["Date"])
                matches.append(
                    Match(
                        home=row["HomeTeam"].strip(),
                        away=row["AwayTeam"].strip(),
                        date=date,
                        score=Score(int(float(row["FTHG"])), int(float(row["FTAG"]))),
                        competition=DIVISIONS[competition],
                    )
                )
            except (KeyError, ValueError):
                continue
        log = MatchLog(matches)
        return ResultSet(
            matches=log,
            evidence=(
                Evidence(
                    key=f"résultats::{competition}::{season}",
                    value=f"{len(log)} matchs",
                    source=source,
                    retrieved_at=retrieved_at,  # type: ignore[arg-type]
                    status=Confidence.CONFIRMED if log else Confidence.UNAVAILABLE,
                    fact_date=log.end if log else None,
                ),
            ),
            source=source,
            retrieved_at=retrieved_at,  # type: ignore[arg-type]
        )

    def season(self, competition: str, season: str) -> SeasonData:
        """Serve the archive through the engine's provider contract.

        The archive holds results only — no forward fixtures — so
        :attr:`SeasonData.fixtures` is empty and the engine treats any requested
        match as unverified rather than inventing a calendar entry.
        """
        result = self.results(competition, season)
        return SeasonData(
            competition=competition,
            season=season,
            label=DIVISIONS.get(competition, competition),
            played=result.matches,
            fixtures=(),
            retrieved_at=result.retrieved_at,
            url=self.url_for(competition, season),
        )

    def odds(
        self, competition: str = "E0", season: str = "2024-25"
    ) -> tuple[dict[Fixture, MatchOdds], list[Evidence]]:
        """Closing 1X2 prices keyed by fixture."""
        rows, retrieved_at = self._rows(competition, season)
        source = self.source(self.url_for(competition, season))
        columns = (f"{self._bookmaker}H", f"{self._bookmaker}D", f"{self._bookmaker}A")
        book: dict[Fixture, MatchOdds] = {}
        evidence: list[Evidence] = []
        for row in rows:
            try:
                fixture = Fixture(
                    home=row["HomeTeam"].strip(),
                    away=row["AwayTeam"].strip(),
                    date=parse_date(row["Date"]),
                    competition=DIVISIONS[competition],
                )
                prices = [float(row[column]) for column in columns]
            except (KeyError, ValueError):
                continue
            quote = MatchOdds(prices[0], prices[1], prices[2], bookmaker=self._bookmaker)
            book[fixture] = quote
            evidence.append(
                Evidence(
                    key=f"cote::{fixture.home} vs {fixture.away}",
                    value=str(quote),
                    source=source,
                    retrieved_at=retrieved_at,  # type: ignore[arg-type]
                    status=Confidence.CONFIRMED,
                    fact_date=fixture.date,
                    note="cote de clôture archivée : à ne pas confondre avec le prix "
                    "disponible au moment de la décision",
                )
            )
        return book, evidence
