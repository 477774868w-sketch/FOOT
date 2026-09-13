"""The openfootball adapter — the one provider verified operational here.

openfootball publishes season files as JSON on GitHub under a public domain
dedication.  It carries results *and* forward fixtures for the top divisions of
England, Spain, Germany, Italy and France, which is exactly the spine an
analysis needs: a training history and the fixtures to be analysed.

It does **not** carry lineups, injuries, xG or odds.  The adapter says so in its
capabilities rather than pretending otherwise, and the engine reports those
rubrics as unavailable instead of inventing them.

Real-world messiness handled here, all of it observed in the live files:

* ``score`` appears as ``{"ft": [h, a], "ht": [...]}``, as a bare ``[h, a]``,
  or absent entirely for a fixture not yet played;
* a season file mixes played matches and future fixtures, so the split is done
  on the presence of a full-time score, never on the date.
"""

from __future__ import annotations

import datetime as dt
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
from foot.collect.http import fetch_json
from foot.domain import Fixture, Match, MatchLog, Score
from foot.provenance import Confidence, Evidence, Source, utcnow

__all__ = ["COMPETITIONS", "OpenFootballProvider", "SeasonData"]

_BASE = "https://raw.githubusercontent.com/openfootball/football.json/master"

COMPETITIONS: dict[str, str] = {
    "en.1": "Premier League (Angleterre)",
    "es.1": "Primera División (Espagne)",
    "de.1": "Bundesliga (Allemagne)",
    "it.1": "Serie A (Italie)",
    "fr.1": "Ligue 1 (France)",
}
"""Competition keys this adapter serves, with their French labels."""

_ALIASES: dict[str, str] = {
    "premier league": "en.1", "premier-league": "en.1", "angleterre": "en.1",
    "epl": "en.1", "pl": "en.1", "england": "en.1",
    "liga": "es.1", "la liga": "es.1", "laliga": "es.1", "espagne": "es.1",
    "primera": "es.1", "spain": "es.1",
    "bundesliga": "de.1", "allemagne": "de.1", "germany": "de.1",
    "serie a": "it.1", "seriea": "it.1", "italie": "it.1", "italy": "it.1",
    "ligue 1": "fr.1", "ligue1": "fr.1", "france": "fr.1", "l1": "fr.1",
}


def resolve_competition(text: str) -> str | None:
    """Map a user's wording onto a competition key, or ``None`` if unknown."""
    cleaned = text.strip().lower()
    if cleaned in COMPETITIONS:
        return cleaned
    return _ALIASES.get(cleaned)


def _parse_score(raw: object) -> tuple[int, int] | None:
    """Read the three score shapes openfootball actually emits."""
    if raw is None:
        return None
    if isinstance(raw, dict):
        full = raw.get("ft")
        if isinstance(full, (list, tuple)) and len(full) == 2:
            return (int(full[0]), int(full[1]))
        return None
    if isinstance(raw, (list, tuple)) and len(raw) == 2:
        return (int(raw[0]), int(raw[1]))
    return None


class OpenFootballProvider:
    """Results and fixtures for five European top divisions."""

    __slots__ = ("_cache", "_timeout")

    def __init__(self, cache: Cache | None = None, *, timeout: float = 30.0) -> None:
        self._cache = cache
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "openfootball"

    @property
    def upstream(self) -> str:
        return "openfootball/football.json"

    @property
    def capabilities(self) -> frozenset[Capability]:
        return frozenset({Capability.RESULTS, Capability.FIXTURES})

    def competitions(self) -> Sequence[str]:
        return tuple(COMPETITIONS)

    def source(self, url: str) -> Source:
        return Source(
            name="openfootball (GitHub)",
            provider=self.upstream,
            url=url,
            official=False,
        )

    def url_for(self, competition: str, season: str) -> str:
        if competition not in COMPETITIONS:
            raise CollectionError(
                f"compétition inconnue pour openfootball : {competition!r} "
                f"(disponibles : {', '.join(COMPETITIONS)})"
            )
        return f"{_BASE}/{season}/{competition}.json"

    # -- Probing -----------------------------------------------------------
    def probe(self, *, seasons: Sequence[str] = ("2026-27",)) -> ProviderStatus:
        """Fetch one small file per competition to measure real coverage.

        The status reports only what came back with data.  A competition that
        404s or returns an empty file is left out of ``coverage`` rather than
        listed hopefully.
        """
        checked_at = utcnow()
        covered: list[str] = []
        detail = ""
        reachability = Reachability.OK
        for competition in COMPETITIONS:
            try:
                data = self._load(competition, seasons[0])
            except ProviderBlockedError as error:
                return ProviderStatus(
                    provider=self.name,
                    reachability=Reachability.BLOCKED,
                    capabilities=self.capabilities,
                    checked_at=checked_at,
                    detail=str(error),
                )
            except CollectionError as error:
                detail = str(error)
                continue
            if data.played or data.fixtures:
                covered.append(f"{competition} {seasons[0]}")
        if not covered:
            reachability = Reachability.ERROR
            detail = detail or "aucune compétition n'a renvoyé de données"
        return ProviderStatus(
            provider=self.name,
            reachability=reachability,
            capabilities=self.capabilities,
            checked_at=checked_at,
            detail=detail,
            coverage=tuple(covered),
        )

    # -- Collection --------------------------------------------------------
    def _load(self, competition: str, season: str) -> SeasonData:
        url = self.url_for(competition, season)
        payload: object
        if self._cache is not None:
            cached = self._cache.load(url)
            if cached is not None:
                payload, retrieved_at = cached.payload, cached.retrieved_at
            else:
                payload, retrieved_at = fetch_json(url, timeout=self._timeout)
                self._cache.store(url, payload, retrieved_at)
        else:
            payload, retrieved_at = fetch_json(url, timeout=self._timeout)

        if not isinstance(payload, dict) or "matches" not in payload:
            raise CollectionError(f"{url} : structure inattendue")
        raw_matches = payload.get("matches")
        if not isinstance(raw_matches, list):
            raise CollectionError(f"{url} : 'matches' n'est pas une liste")

        label = str(payload.get("name") or f"{competition} {season}")
        played: list[Match] = []
        fixtures: list[Fixture] = []
        for raw in raw_matches:
            if not isinstance(raw, dict):
                continue
            try:
                date = dt.date.fromisoformat(str(raw["date"]))
                home = str(raw["team1"]).strip()
                away = str(raw["team2"]).strip()
            except (KeyError, ValueError):
                continue  # a malformed row is skipped, never guessed at
            if not home or not away or home == away:
                continue
            score = _parse_score(raw.get("score"))
            if score is None:
                fixtures.append(Fixture(home, away, date, competition=label))
            else:
                played.append(
                    Match(home, away, date, Score(*score), competition=label)
                )
        return SeasonData(
            competition=competition,
            season=season,
            label=label,
            played=MatchLog(played),
            fixtures=tuple(fixtures),
            retrieved_at=retrieved_at,
            url=url,
        )

    def season(self, competition: str, season: str) -> SeasonData:
        """Load one season file, results and fixtures together."""
        return self._load(competition, season)

    def results(self, competition: str, season: str) -> ResultSet:
        """Played matches only, with evidence documenting the fetch."""
        data = self._load(competition, season)
        source = self.source(data.url)
        evidence = [
            Evidence(
                key=f"résultats::{competition}::{season}",
                value=f"{len(data.played)} matchs joués",
                source=source,
                retrieved_at=data.retrieved_at,
                status=Confidence.CONFIRMED if data.played else Confidence.UNAVAILABLE,
                fact_date=data.played.end if data.played else None,
                note=data.label,
            )
        ] if data.played else [
            Evidence(
                key=f"résultats::{competition}::{season}",
                value=None,
                source=source,
                retrieved_at=data.retrieved_at,
                status=Confidence.UNAVAILABLE,
                note=f"{data.label} : aucun match joué dans ce fichier",
            )
        ]
        return ResultSet(
            matches=data.played,
            evidence=tuple(evidence),
            source=source,
            retrieved_at=data.retrieved_at,
        )

    def history(
        self, competition: str, seasons: Sequence[str]
    ) -> tuple[MatchLog, list[Evidence]]:
        """Concatenate several seasons into one training history.

        Seasons that cannot be fetched are reported as evidence with status
        ``UNAVAILABLE`` and simply left out — never back-filled.
        """
        matches: list[Match] = []
        evidence: list[Evidence] = []
        for season in seasons:
            try:
                data = self._load(competition, season)
            except CollectionError as error:
                evidence.append(
                    Evidence(
                        key=f"résultats::{competition}::{season}",
                        value=None,
                        source=self.source(self.url_for(competition, season)),
                        retrieved_at=utcnow(),
                        status=Confidence.UNAVAILABLE,
                        note=str(error),
                    )
                )
                continue
            matches.extend(data.played)
            evidence.append(
                Evidence(
                    key=f"résultats::{competition}::{season}",
                    value=f"{len(data.played)} matchs",
                    source=self.source(data.url),
                    retrieved_at=data.retrieved_at,
                    status=Confidence.CONFIRMED,
                    fact_date=data.played.end if data.played else None,
                    note=data.label,
                )
            )
        return MatchLog(matches), evidence
