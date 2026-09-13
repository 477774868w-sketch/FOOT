"""Adaptateur football-data.org : calendrier, résultats, compositions.

Ce que ce module **fait** : parler l'API v4 de football-data.org, en lire les
rencontres et, quand le plan y donne droit, les compositions officielles.

Ce qu'il ne fait **pas** : promettre que votre compte obtiendra tout cela. Le
plan gratuit sert douze compétitions à dix requêtes par minute et **ne renvoie
pas les compositions** ; la même URL répond alors 403, ou renvoie un objet sans
le champ attendu. :meth:`FootballDataOrgProvider.probe` appelle donc réellement
le service et rapporte ce qu'il a reçu — c'est la seule preuve qui vaille.

Le parsing est séparé du réseau (:func:`parse_matches`, :func:`parse_lineups`),
si bien que les tests s'exécutent sur des **réponses enregistrées**, sans clé ni
connexion : la lecture des formats est vérifiée ici, l'accès du compte l'est par
la sonde, chez l'opérateur.

Toute donnée lue porte sa provenance : l'URL appelée, l'instant de récupération,
et le statut de confirmation que la source lui donne.
"""

from __future__ import annotations

import datetime as dt
import os
from collections.abc import Mapping, Sequence
from typing import Any

from foot.analysis.naming import normalise
from foot.collect.base import (
    Capability,
    CollectionError,
    ProviderBlockedError,
    ProviderStatus,
    Reachability,
    SeasonData,
)
from foot.collect.cache import Cache
from foot.collect.http import fetch_json
from foot.collect.supplements import LineupRow
from foot.domain import Fixture, Match, MatchLog, Score
from foot.provenance import Confidence, Evidence, Source, utcnow

__all__ = [
    "COMPETITION_CODES",
    "CREDENTIAL",
    "FootballDataOrgProvider",
    "match_index",
    "parse_lineups",
    "parse_matches",
]

CREDENTIAL = "FOOTBALL_DATA_ORG_TOKEN"
"""Environment variable holding the API token. Never hard-code a key."""

_BASE = "https://api.football-data.org/v4"

COMPETITION_CODES: Mapping[str, str] = {
    "en.1": "PL",
    "es.1": "PD",
    "de.1": "BL1",
    "it.1": "SA",
    "fr.1": "FL1",
}
"""Our competition keys mapped onto the provider's codes.

Only the five leagues the rest of the engine already covers: adding a code the
engine cannot name would produce fixtures nothing can resolve.
"""

_FINISHED = frozenset({"FINISHED", "AWARDED"})
_UPCOMING = frozenset({"SCHEDULED", "TIMED", "POSTPONED", "SUSPENDED"})


def _text(value: object, default: str = "") -> str:
    return value.strip() if isinstance(value, str) else default


def _as_date(value: object) -> dt.date | None:
    """Read the provider's ISO kickoff, keeping only what is unambiguous."""
    text = _text(value)
    if not text:
        return None
    try:
        moment = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment.date()


def parse_matches(
    payload: object, *, competition: str
) -> tuple[MatchLog, tuple[Fixture, ...]]:
    """Split one ``/matches`` response into played matches and future fixtures.

    A match with no usable score is a **fixture**, never a 0-0: inventing a
    scoreline would corrupt every rating downstream. A row whose teams or date
    cannot be read is skipped rather than guessed at.
    """
    if not isinstance(payload, Mapping):
        raise CollectionError("réponse football-data.org inattendue : objet attendu")
    rows = payload.get("matches")
    if not isinstance(rows, Sequence):
        raise CollectionError("réponse football-data.org sans liste « matches »")

    played: list[Match] = []
    fixtures: list[Fixture] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        home = _text(_nested(row, "homeTeam", "name"))
        away = _text(_nested(row, "awayTeam", "name"))
        date = _as_date(row.get("utcDate"))
        if not home or not away or date is None:
            continue
        status = _text(row.get("status")).upper()
        score = _read_score(row)
        if status in _FINISHED and score is not None:
            played.append(
                Match(
                    home=home, away=away, date=date, score=score, competition=competition
                )
            )
        elif status in _UPCOMING or score is None:
            fixtures.append(
                Fixture(home=home, away=away, date=date, competition=competition)
            )
    return (MatchLog(played), tuple(fixtures))


def match_index(payload: object) -> dict[tuple[dt.date, str, str], int]:
    """``{(date, home, away): identifiant}`` for one ``/matches`` response.

    The provider keys a match by an integer id; we key a fixture by its teams
    and its date.  Building the bridge here — from the response the engine has
    already fetched — avoids a second, paid round trip just to learn an id.
    """
    if not isinstance(payload, Mapping):
        return {}
    rows = payload.get("matches")
    if not isinstance(rows, Sequence):
        return {}
    index: dict[tuple[dt.date, str, str], int] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        identifier = row.get("id")
        home = _text(_nested(row, "homeTeam", "name"))
        away = _text(_nested(row, "awayTeam", "name"))
        date = _as_date(row.get("utcDate"))
        if not isinstance(identifier, int) or not home or not away or date is None:
            continue
        index[(date, home.casefold(), away.casefold())] = identifier
    return index


def _nested(row: Mapping[str, Any], *path: str) -> object:
    current: object = row
    for key in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _read_score(row: Mapping[str, Any]) -> Score | None:
    home = _nested(row, "score", "fullTime", "home")
    away = _nested(row, "score", "fullTime", "away")
    if not isinstance(home, int) or not isinstance(away, int):
        return None
    if home < 0 or away < 0:
        return None
    return Score(home, away)


def parse_lineups(
    payload: object, *, match_date: dt.date, published_at: dt.datetime | None = None
) -> tuple[LineupRow, ...]:
    """Read one ``/matches/{id}`` response into team-sheet rows.

    Returns nothing when the plan does not serve lineups — which is the normal
    case on the free tier. An empty result is reported as such by the caller,
    never as "no lineup was published".
    """
    if not isinstance(payload, Mapping):
        return ()
    rows: list[LineupRow] = []
    for side in ("homeTeam", "awayTeam"):
        team_block = payload.get(side)
        if not isinstance(team_block, Mapping):
            continue
        team = _text(team_block.get("name"))
        if not team:
            continue
        opponent = _text(
            _nested(payload, "awayTeam" if side == "homeTeam" else "homeTeam", "name")
        )
        for starting, field_name in ((True, "lineup"), (False, "bench")):
            players = team_block.get(field_name)
            if not isinstance(players, Sequence):
                continue
            for player in players:
                if not isinstance(player, Mapping):
                    continue
                name = _text(player.get("name"))
                if not name:
                    continue
                rows.append(
                    LineupRow(
                        date=match_date,
                        published_at=published_at,
                        team=team,
                        player=name,
                        role=_role(_text(player.get("position"))),
                        starting=starting,
                        opponent=opponent,
                        source="football-data.org",
                        status=Confidence.CONFIRMED,
                    )
                )
    return tuple(rows)


_POSITIONS: Mapping[str, str] = {
    "goalkeeper": "gardien",
    "centre-back": "défenseur central",
    "left-back": "défenseur",
    "right-back": "défenseur",
    "defence": "défenseur",
    "defensive midfield": "milieu défensif",
    "central midfield": "milieu",
    "attacking midfield": "créateur",
    "midfield": "milieu",
    "centre-forward": "buteur",
    "left winger": "ailier",
    "right winger": "ailier",
    "offence": "buteur",
}


def _role(position: str) -> str:
    """Map the provider's English position onto the protocol's watched roles.

    An unrecognised position keeps its original text rather than being forced
    into a watched role: a wrong role would silently create or suppress a
    decisive-absence scenario.
    """
    return _POSITIONS.get(position.strip().lower(), position.strip().lower())


class FootballDataOrgProvider:
    """Season data from football-data.org, for an account that has a token."""

    __slots__ = ("_cache", "_timeout", "_token")

    def __init__(
        self,
        cache: Cache | None = None,
        *,
        token: str | None = None,
        timeout: float = 20.0,
    ) -> None:
        self._cache = cache
        self._token = token if token is not None else os.environ.get(CREDENTIAL, "")
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "football-data.org"

    @property
    def upstream(self) -> str:
        return "football-data.org (API v4)"

    @property
    def configured(self) -> bool:
        return bool(self._token.strip())

    @property
    def capabilities(self) -> frozenset[Capability]:
        """What this adapter can *read* — not what a given plan will serve.

        The difference is the whole point of :meth:`probe`.
        """
        return frozenset(
            {Capability.RESULTS, Capability.FIXTURES, Capability.LINEUPS}
        )

    def competitions(self) -> Sequence[str]:
        return tuple(COMPETITION_CODES)

    def source(self, url: str = "") -> Source:
        return Source(
            name="football-data.org",
            provider="football-data.org (API v4)",
            url=url or _BASE,
            official=False,
        )

    # -- Network ----------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        if not self.configured:
            raise ProviderBlockedError(
                f"football-data.org : aucune clé configurée. Renseignez "
                f"{CREDENTIAL} (voir « foot config »)."
            )
        return {"X-Auth-Token": self._token}

    def _get(self, path: str) -> tuple[object, dt.datetime]:
        url = f"{_BASE}{path}"
        if self._cache is not None:
            cached = self._cache.load(url)
            if cached is not None:
                return (cached.payload, cached.retrieved_at)
        payload, retrieved = fetch_json(
            url, timeout=self._timeout, headers=self._headers()
        )
        if self._cache is not None:
            self._cache.store(url, payload, retrieved)
        return (payload, retrieved)

    def season(self, competition: str, season: str) -> SeasonData:
        """Matches for one competition-season, played and upcoming."""
        code = COMPETITION_CODES.get(competition)
        if code is None:
            raise CollectionError(
                f"compétition non servie par cet adaptateur : {competition!r} "
                f"(disponibles : {', '.join(sorted(COMPETITION_CODES))})"
            )
        year = _season_year(season)
        path = f"/competitions/{code}/matches"
        if year:
            path += f"?season={year}"
        payload, retrieved = self._get(path)
        played, fixtures = parse_matches(payload, competition=competition)
        return SeasonData(
            competition=competition,
            season=season,
            label=_text(_nested(payload, "competition", "name")) or competition,  # type: ignore[arg-type]
            played=played,
            fixtures=fixtures,
            retrieved_at=retrieved,
            url=f"{_BASE}{path}",
        )

    def lineups(
        self, match_id: int, *, match_date: dt.date
    ) -> tuple[tuple[LineupRow, ...], Evidence]:
        """Team sheets for one match, with evidence saying what came back.

        On a plan that does not serve lineups the call succeeds and the sheets
        are simply absent; the evidence records that, so the report can say
        "not served to this account" rather than "no lineup published".
        """
        payload, retrieved = self._get(f"/matches/{match_id}")
        rows = parse_lineups(payload, match_date=match_date, published_at=retrieved)
        return (
            rows,
            Evidence(
                key=f"composition::football-data.org::{match_id}",
                value=f"{len(rows)} joueur(s)",
                source=self.source(f"{_BASE}/matches/{match_id}"),
                retrieved_at=retrieved,
                status=Confidence.CONFIRMED if rows else Confidence.UNAVAILABLE,
                fact_date=match_date,
                note=(
                    "compositions servies par l'API"
                    if rows
                    else "aucune composition dans la réponse : ce plan ne les sert "
                    "probablement pas (vérifié sur la réponse reçue, pas supposé)"
                ),
            ),
        )

    def team_sheets(
        self, fixture: Fixture
    ) -> tuple[tuple[LineupRow, ...], tuple[Evidence, ...]]:
        """Team sheets for one fixture, resolved from its teams and its date.

        Satisfies :class:`~foot.collect.base.LineupSource`.  Every failure comes
        back as evidence rather than as an exception: on the day of a match, an
        analysis that stops because a sheet endpoint answered 403 is worse than
        one that says so and carries on without the sheet.

        Nothing here invents a sheet.  Four different silences are told apart —
        competition not served, fixture not found, plan does not return lineups,
        service unreachable — because the operator's next move differs in each.
        """
        code = COMPETITION_CODES.get(fixture.competition or "")
        if code is None:
            return ((), (self._sheet_note(
                fixture, "compétition non servie par football-data.org"
            ),))
        try:
            payload, retrieved = self._get(f"/competitions/{code}/matches")
        except (CollectionError, ProviderBlockedError) as error:
            return ((), (self._sheet_note(fixture, str(error)),))
        identifier = match_index(payload).get(
            (fixture.date, fixture.home.casefold(), fixture.away.casefold())
        )
        if identifier is None:
            identifier = _loose_match(match_index(payload), fixture)
        if identifier is None:
            return ((), (self._sheet_note(
                fixture,
                "rencontre absente du calendrier de ce fournisseur à cette date",
            ),))
        try:
            rows, evidence = self.lineups(identifier, match_date=fixture.date)
        except (CollectionError, ProviderBlockedError) as error:
            return ((), (self._sheet_note(fixture, str(error)),))
        del retrieved
        return (rows, (evidence,))

    def _sheet_note(self, fixture: Fixture, reason: str) -> Evidence:
        """Say why no sheet came back — never that none was published."""
        return Evidence(
            key=f"composition::football-data.org::{fixture.home}-{fixture.away}",
            value="aucune composition récupérée",
            source=self.source(),
            retrieved_at=utcnow(),
            status=Confidence.UNAVAILABLE,
            fact_date=fixture.date,
            note=reason,
        )

    def probe(self, competition: str = "en.1") -> ProviderStatus:
        """Ask the service what **this** account actually gets.

        Reads the response rather than the documentation: a 403 on a plan that
        the website lists as included is exactly the case this exists for.
        """
        now = utcnow()
        if not self.configured:
            return ProviderStatus(
                provider=self.name,
                reachability=Reachability.BLOCKED,
                capabilities=frozenset(),
                checked_at=now,
                detail=f"aucune clé dans {CREDENTIAL} — adaptateur écrit, non activé",
                coverage=(),
            )
        code = COMPETITION_CODES.get(competition, "PL")
        try:
            payload, retrieved = self._get(f"/competitions/{code}/matches")
        except ProviderBlockedError as error:
            return ProviderStatus(
                provider=self.name,
                reachability=Reachability.BLOCKED,
                capabilities=frozenset(),
                checked_at=now,
                detail=str(error),
            )
        except CollectionError as error:
            return ProviderStatus(
                provider=self.name,
                reachability=Reachability.ERROR,
                capabilities=frozenset(),
                checked_at=now,
                detail=str(error),
            )
        played, fixtures = parse_matches(payload, competition=competition)
        observed: set[Capability] = set()
        if played:
            observed.add(Capability.RESULTS)
        if fixtures:
            observed.add(Capability.FIXTURES)
        return ProviderStatus(
            provider=self.name,
            reachability=Reachability.OK,
            capabilities=frozenset(observed),
            checked_at=retrieved,
            detail=f"{len(played)} joués, {len(fixtures)} à venir pour {competition}",
            coverage=(f"{competition} ({code})",),
        )


def _season_year(season: str) -> str:
    """``"2026-27"`` → ``"2026"``; the API keys a season by its opening year."""
    head = season.split("-", maxsplit=1)[0].strip()
    return head if head.isdigit() and len(head) == 4 else ""


def _loose_match(
    index: Mapping[tuple[dt.date, str, str], int], fixture: Fixture
) -> int | None:
    """Second pass on the same date, folding club names before comparing.

    « Manchester United FC » and « Manchester United » are the same club; the
    date and the pairing must still agree exactly, so a fold can never move a
    sheet from one match to another.
    """
    home, away = normalise(fixture.home), normalise(fixture.away)
    if not home or not away:
        return None
    for (date, index_home, index_away), identifier in index.items():
        if date != fixture.date:
            continue
        if normalise(index_home) == home and normalise(index_away) == away:
            return identifier
    return None
