"""Adaptateur API-Football : absences, compositions, statistiques.

C'est le fournisseur qui débloquerait les rubriques que rien ne sert aujourd'hui
— R07 (xG et tirs), R10 (gardien), R11 (absences), R21 (compositions) — et il est
donc écrit avec une exigence supplémentaire : **ne jamais présenter un indicateur
générique comme la confirmation de chaque champ**.

Ce que le module garantit :

* chaque champ est vérifié **individuellement** dans la réponse reçue. « Des
  statistiques sont disponibles » ne vaut pas « xG disponible » : une réponse
  peut porter les tirs et pas les xG, et :func:`field_coverage` le dit champ par
  champ, par championnat et par saison ;
* **zéro n'est pas une absence**. Une équipe qui a tiré zéro fois a tiré zéro
  fois ; un champ que la réponse ne porte pas est ``None``. Les deux traversent
  le logiciel différemment, et les confondre inventerait des faits ;
* rien n'est **dérivé** de ce qui n'est pas servi. L'API donne des totaux de
  match ; elle ne donne pas les xG à onze contre onze. Fabriquer les seconds à
  partir des premiers produirait un chiffre d'apparence rigoureuse et sans
  fondement — la rubrique reste marquée indisponible ;
* toute donnée porte sa **provenance** et sa **disponibilité temporelle** : une
  composition publiée à 19:35 n'est pas connue d'une analyse datée de 18:00.

Ce que le module ne fait pas : souscrire quoi que ce soit. Sans clé, la sonde
dit ce qui manque et rien d'autre ne se produit.
"""

from __future__ import annotations

import datetime as dt
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

from foot.analysis.naming import normalise
from foot.collect.base import (
    Capability,
    CollectionError,
    ProviderBlockedError,
    ProviderStatus,
    Reachability,
)
from foot.collect.cache import Cache
from foot.collect.http import fetch_json
from foot.collect.supplements import AbsenceRow, LineupRow, XgRow
from foot.domain import Fixture
from foot.provenance import Confidence, Evidence, Source, utcnow

__all__ = [
    "COMPETITION_IDS",
    "CREDENTIAL",
    "ApiFootballProvider",
    "CoverageMatrix",
    "FieldState",
    "FieldSupport",
    "MatchStatistics",
    "field_coverage",
    "parse_injuries",
    "parse_lineups",
    "parse_statistics",
]

CREDENTIAL = "API_FOOTBALL_KEY"
"""Environment variable holding the key. Never hard-code one, never render one."""

_BASE = "https://v3.football.api-sports.io"

COMPETITION_IDS: Mapping[str, int] = {
    "en.1": 39,
    "es.1": 140,
    "de.1": 78,
    "it.1": 135,
    "fr.1": 61,
}
"""Our competition keys mapped onto API-Football league ids."""

_WATCHED_ROLES: Mapping[str, str] = {
    "g": "gardien",
    "d": "défenseur",
    "m": "milieu",
    "f": "attaquant",
}

FULL_ELEVEN = 11


class FieldState(Enum):
    """What a probe actually found for one field, on one competition-season."""

    SERVED = "servi"
    """The field is present and carries a value."""

    PRESENT_BUT_NULL = "présent, sans valeur"
    """The key exists and is null — the provider has no value, and says so."""

    ABSENT = "absent de la réponse"
    """The key is not in the response at all: this plan does not serve it."""

    NOT_PROBED = "non sondé"
    """No request was made — no key, or the competition was not asked for."""

    @property
    def symbol(self) -> str:
        return {
            FieldState.SERVED: "●",
            FieldState.PRESENT_BUT_NULL: "◐",
            FieldState.ABSENT: "·",
            FieldState.NOT_PROBED: "?",
        }[self]


@dataclass(frozen=True, slots=True)
class FieldSupport:
    """One field, one competition, one season — and what came back."""

    field: str
    competition: str
    season: str
    state: FieldState
    sampled: int = 0
    """How many rows were inspected. A verdict from one row is a weak verdict."""

    detail: str = ""

    def render(self) -> str:
        line = (
            f"{self.state.symbol} {self.field:<22} {self.competition} {self.season} "
            f"— {self.state.value}"
        )
        if self.sampled:
            line += f" (sur {self.sampled} ligne(s))"
        if self.detail:
            line += f" · {self.detail}"
        return line


@dataclass(frozen=True, slots=True)
class MatchStatistics:
    """Per-team match statistics, with absence and zero kept apart.

    Every field is ``None`` when the response did not carry it and a number when
    it did — **including zero**. A team that had no shot on target had none; a
    plan that does not serve shots on target is a different fact, and the two
    must never collapse into ``0``.
    """

    team: str
    opponent: str
    date: dt.date
    home: bool
    xg: float | None = None
    npxg: float | None = None
    shots: int | None = None
    shots_on_target: int | None = None
    big_chances: int | None = None
    red_cards: int | None = None
    yellow_cards: int | None = None
    penalties: int | None = None
    source: str = "API-Football"

    def served(self) -> tuple[str, ...]:
        """Names of the fields this row actually carries."""
        return tuple(
            name
            for name in (
                "xg",
                "npxg",
                "shots",
                "shots_on_target",
                "big_chances",
                "red_cards",
                "yellow_cards",
                "penalties",
            )
            if getattr(self, name) is not None
        )

    def evidence(self, retrieved_at: dt.datetime) -> Evidence:
        served = self.served()
        return Evidence(
            key=f"stats::{self.team}::{self.date.isoformat()}",
            value=", ".join(f"{n}={getattr(self, n)}" for n in served) or None,
            source=Source(name=self.source, provider="API-Football", official=False),
            retrieved_at=retrieved_at,
            status=Confidence.CONFIRMED if served else Confidence.UNAVAILABLE,
            fact_date=self.date,
            note=(
                "champs servis : " + ", ".join(served)
                if served
                else "aucun champ statistique dans la réponse pour cette équipe"
            ),
        )


def _text(value: object, default: str = "") -> str:
    return value.strip() if isinstance(value, str) else default


def _number(value: object) -> float | None:
    """Read a numeric statistic, keeping ``0`` and refusing to invent one.

    API-Football writes percentages as ``"52%"`` and missing values as ``None``.
    ``None`` stays ``None``: it means the plan did not serve the field, which is
    not the same statement as "the value is zero".
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = _text(value).rstrip("%")
    try:
        return float(text)
    except ValueError:
        return None


def _count(value: object) -> int | None:
    number = _number(value)
    return None if number is None else int(number)


def _as_date(value: object) -> dt.date | None:
    text = _text(value)
    if not text:
        return None
    try:
        return dt.datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _as_moment(value: object) -> dt.datetime | None:
    text = _text(value)
    if not text:
        return None
    try:
        moment = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=dt.timezone.utc)


def _rows(payload: object) -> Sequence[Mapping[str, Any]]:
    """The ``response`` list of an API-Football payload, or nothing."""
    if not isinstance(payload, Mapping):
        return ()
    rows = payload.get("response")
    return [row for row in rows if isinstance(row, Mapping)] if isinstance(rows, Sequence) else ()


_STAT_FIELDS: Mapping[str, tuple[str, ...]] = {
    "xg": ("expected_goals",),
    "npxg": ("expected_goals_non_penalty", "npxg"),
    "shots": ("total shots", "shots total"),
    "shots_on_target": ("shots on goal", "shots on target"),
    "big_chances": ("big chances", "big chances created"),
    "red_cards": ("red cards",),
    "yellow_cards": ("yellow cards",),
    "penalties": ("penalty goals", "penalties scored"),
}
"""Our field names mapped onto the labels API-Football uses for them.

Several labels per field because the service has renamed some over time, and a
field read under one name but not the other would be reported absent when it is
served.
"""


def parse_statistics(
    payload: object, *, match_date: dt.date, home_team: str = ""
) -> tuple[MatchStatistics, ...]:
    """Read a ``/fixtures/statistics`` response into per-team rows.

    A statistic the response does not carry stays ``None``. A statistic it
    carries as zero is zero. This function never fills one in from the other.
    """
    rows: list[MatchStatistics] = []
    blocks = _rows(payload)
    names = [_text(_nested(block, "team", "name")) for block in blocks]
    for index, block in enumerate(blocks):
        team = names[index]
        if not team:
            continue
        opponent = next((n for i, n in enumerate(names) if i != index and n), "")
        values: dict[str, object] = {}
        entries = block.get("statistics")
        if isinstance(entries, Sequence):
            for entry in entries:
                if not isinstance(entry, Mapping):
                    continue
                label = _text(entry.get("type")).lower()
                for field_name, labels in _STAT_FIELDS.items():
                    if label in labels:
                        values[field_name] = entry.get("value")
        rows.append(
            MatchStatistics(
                team=team,
                opponent=opponent,
                date=match_date,
                home=bool(home_team) and team == home_team,
                xg=_number(values.get("xg")) if "xg" in values else None,
                npxg=_number(values.get("npxg")) if "npxg" in values else None,
                shots=_count(values.get("shots")) if "shots" in values else None,
                shots_on_target=(
                    _count(values.get("shots_on_target"))
                    if "shots_on_target" in values
                    else None
                ),
                big_chances=(
                    _count(values.get("big_chances")) if "big_chances" in values else None
                ),
                red_cards=(
                    _count(values.get("red_cards")) if "red_cards" in values else None
                ),
                yellow_cards=(
                    _count(values.get("yellow_cards"))
                    if "yellow_cards" in values
                    else None
                ),
                penalties=(
                    _count(values.get("penalties")) if "penalties" in values else None
                ),
            )
        )
    return tuple(rows)


def _nested(row: Mapping[str, Any], *path: str) -> object:
    current: object = row
    for key in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def parse_injuries(
    payload: object, *, as_of: dt.datetime | None = None
) -> tuple[AbsenceRow, ...]:
    """Read an ``/injuries`` response into absence rows.

    The service reports two kinds of line: « Missing Fixture » (out) and
    « Questionable » (doubtful). Only the first is an absence; a doubt is
    recorded as *probable*, never as a confirmed absence, because treating a
    doubt as an absence would fabricate a documented sporting scenario.
    """
    rows: list[AbsenceRow] = []
    for block in _rows(payload):
        player = _text(_nested(block, "player", "name"))
        team = _text(_nested(block, "team", "name"))
        date = _as_date(_nested(block, "fixture", "date"))
        if not player or not team or date is None:
            continue
        kind = _text(_nested(block, "player", "type")).lower()
        reason = _text(_nested(block, "player", "reason"))
        if "questionable" in kind or "doubt" in kind:
            status = Confidence.PROBABLE
        elif "missing" in kind or "out" in kind:
            status = Confidence.CONFIRMED
        else:
            status = Confidence.PROBABLE
        rows.append(
            AbsenceRow(
                date=date,
                published_at=as_of,
                team=team,
                player=player,
                role=_role(_text(_nested(block, "player", "position"))),
                reason=reason or kind or "non précisé",
                source="API-Football",
                status=status,
            )
        )
    return tuple(rows)


def parse_lineups(
    payload: object, *, match_date: dt.date, published_at: dt.datetime | None = None
) -> tuple[LineupRow, ...]:
    """Read a ``/fixtures/lineups`` response into team-sheet rows."""
    blocks = _rows(payload)
    names = [_text(_nested(block, "team", "name")) for block in blocks]
    rows: list[LineupRow] = []
    for index, block in enumerate(blocks):
        team = names[index]
        if not team:
            continue
        opponent = next((n for i, n in enumerate(names) if i != index and n), "")
        for starting, key in ((True, "startXI"), (False, "substitutes")):
            players = block.get(key)
            if not isinstance(players, Sequence):
                continue
            for slot in players:
                if not isinstance(slot, Mapping):
                    continue
                name = _text(_nested(slot, "player", "name"))
                if not name:
                    continue
                rows.append(
                    LineupRow(
                        date=match_date,
                        published_at=published_at,
                        team=team,
                        player=name,
                        role=_role(_text(_nested(slot, "player", "pos"))),
                        starting=starting,
                        opponent=opponent,
                        source="API-Football",
                        status=Confidence.CONFIRMED,
                    )
                )
    return tuple(rows)


def _role(position: str) -> str:
    """Map a position code onto the protocol's watched roles.

    An unrecognised code keeps its own text: forcing it into a watched role
    would silently create or suppress a decisive-absence scenario.
    """
    code = position.strip().lower()
    return _WATCHED_ROLES.get(code, code)


def field_coverage(
    responses: Mapping[tuple[str, str], object],
    *,
    fields: Sequence[str] = tuple(_STAT_FIELDS),
) -> tuple[FieldSupport, ...]:
    """Which fields a set of responses actually carries, one verdict per field.

    This is the matrix the operator needs before paying for anything: a plan can
    answer « statistics available » and carry shots without xG. Each field is
    looked for by name in the response, and three answers are told apart —
    served, present but null, absent — because they call for different decisions.
    """
    found: list[FieldSupport] = []
    for (competition, season), payload in sorted(responses.items()):
        blocks = _rows(payload)
        labels: dict[str, list[object]] = {}
        for block in blocks:
            entries = block.get("statistics")
            if not isinstance(entries, Sequence):
                continue
            for entry in entries:
                if isinstance(entry, Mapping):
                    labels.setdefault(_text(entry.get("type")).lower(), []).append(
                        entry.get("value")
                    )
        for name in fields:
            values = [
                value
                for label in _STAT_FIELDS.get(name, ())
                for value in labels.get(label, [])
            ]
            if not values:
                state, detail = FieldState.ABSENT, "clé absente de la réponse"
            elif all(value is None for value in values):
                state, detail = FieldState.PRESENT_BUT_NULL, "clé présente, valeurs nulles"
            else:
                state, detail = FieldState.SERVED, ""
            found.append(
                FieldSupport(
                    field=name,
                    competition=competition,
                    season=season,
                    state=state,
                    sampled=len(blocks),
                    detail=detail,
                )
            )
    return tuple(found)


@dataclass(frozen=True, slots=True)
class CoverageMatrix:
    """A coverage table, ready to print before anyone pays for anything."""

    entries: tuple[FieldSupport, ...] = ()
    note: str = ""

    def render(self) -> str:
        if not self.entries:
            return (
                "Aucune couverture mesurée : sans clé API_FOOTBALL_KEY, rien n'a "
                "été demandé et rien n'est affirmé."
            )
        lines = ["COUVERTURE VÉRIFIÉE CHAMP PAR CHAMP (mesurée, non annoncée)"]
        lines.extend(f"  {entry.render()}" for entry in self.entries)
        served = {e.field for e in self.entries if e.state is FieldState.SERVED}
        missing = sorted({e.field for e in self.entries} - served)
        if missing:
            lines.append(
                "  champs non servis à ce compte : " + ", ".join(missing)
            )
        if self.note:
            lines.append(f"  {self.note}")
        return "\n".join(lines)


class ApiFootballProvider:
    """Absences, lineups and match statistics — for an account that has a key.

    Written and tested against recorded responses. What **your** plan returns is
    a different question, and :meth:`probe` answers it by calling the service and
    reading what comes back, field by field.
    """

    __slots__ = ("_cache", "_fetch", "_now", "_timeout", "_token")

    def __init__(
        self,
        cache: Cache | None = None,
        *,
        token: str | None = None,
        timeout: float = 20.0,
        now: Callable[[], dt.datetime] | None = None,
        fetch: Callable[..., tuple[object, dt.datetime]] | None = None,
    ) -> None:
        self._cache = cache
        self._token = token if token is not None else os.environ.get(CREDENTIAL, "")
        self._timeout = timeout
        self._now = now or utcnow
        self._fetch = fetch or fetch_json

    @property
    def name(self) -> str:
        return "API-Football"

    @property
    def upstream(self) -> str:
        return "API-Football (api-sports v3)"

    @property
    def configured(self) -> bool:
        return bool(self._token.strip())

    @property
    def capabilities(self) -> frozenset[Capability]:
        """What this adapter can *read* — never what a given plan will serve."""
        return frozenset(
            {
                Capability.LINEUPS,
                Capability.INJURIES,
                Capability.ADVANCED_STATS,
                Capability.FIXTURES,
                Capability.RESULTS,
            }
        )

    def competitions(self) -> Sequence[str]:
        return tuple(COMPETITION_IDS)

    def source(self, url: str = "") -> Source:
        return Source(
            name="API-Football",
            provider="API-Football (api-sports v3)",
            url=url or _BASE,
            official=False,
        )

    # -- Network ----------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        if not self.configured:
            raise ProviderBlockedError(
                f"API-Football : aucune clé configurée. Renseignez {CREDENTIAL} "
                f"(voir « foot config »). Aucune requête n'a été faite."
            )
        return {"x-apisports-key": self._token}

    def _get(self, path: str) -> tuple[object, dt.datetime]:
        url = f"{_BASE}{path}"
        if self._cache is not None:
            cached = self._cache.load(url, now=self._now())
            if cached is not None:
                return (cached.payload, cached.retrieved_at)
        payload, retrieved = self._fetch(
            url, timeout=self._timeout, headers=self._headers()
        )
        _refuse_errors(payload)
        if self._cache is not None:
            self._cache.store(url, payload, retrieved)
        return (payload, retrieved)

    # -- LineupSource ------------------------------------------------------
    def team_sheets(
        self, fixture: Fixture
    ) -> tuple[tuple[LineupRow, ...], tuple[Evidence, ...]]:
        """Team sheets for one fixture, resolved from its teams and its date."""
        identifier = self._fixture_id(fixture)
        if identifier is None:
            return ((), (self._note(fixture, "rencontre introuvable chez ce fournisseur"),))
        try:
            payload, retrieved = self._get(f"/fixtures/lineups?fixture={identifier}")
        except (CollectionError, ProviderBlockedError) as error:
            return ((), (self._note(fixture, str(error)),))
        rows = parse_lineups(payload, match_date=fixture.date, published_at=retrieved)
        return (
            rows,
            (
                Evidence(
                    key=f"composition::API-Football::{identifier}",
                    value=f"{len(rows)} joueur(s)" if rows else None,
                    source=self.source(f"{_BASE}/fixtures/lineups?fixture={identifier}"),
                    retrieved_at=retrieved,
                    status=Confidence.CONFIRMED if rows else Confidence.UNAVAILABLE,
                    fact_date=fixture.date,
                    note=(
                        "compositions servies par l'API"
                        if rows
                        else "aucune composition dans la réponse : non publiée, ou "
                        "non servie par ce plan — la sonde distingue les deux"
                    ),
                ),
            ),
        )

    def absences(
        self, fixture: Fixture
    ) -> tuple[tuple[AbsenceRow, ...], tuple[Evidence, ...]]:
        """Reported absences for one fixture, with doubt kept apart from absence."""
        identifier = self._fixture_id(fixture)
        if identifier is None:
            return ((), (self._note(fixture, "rencontre introuvable chez ce fournisseur"),))
        try:
            payload, retrieved = self._get(f"/injuries?fixture={identifier}")
        except (CollectionError, ProviderBlockedError) as error:
            return ((), (self._note(fixture, str(error)),))
        rows = parse_injuries(payload, as_of=retrieved)
        return (
            rows,
            (
                Evidence(
                    key=f"absence::API-Football::{identifier}",
                    value=f"{len(rows)} absence(s) déclarée(s)" if rows else None,
                    source=self.source(f"{_BASE}/injuries?fixture={identifier}"),
                    retrieved_at=retrieved,
                    status=Confidence.CONFIRMED if rows else Confidence.UNAVAILABLE,
                    fact_date=fixture.date,
                    note=(
                        "absences servies par l'API ; « questionable » reste probable"
                        if rows
                        else "aucune absence déclarée : ce n'est pas la preuve que "
                        "l'effectif est au complet"
                    ),
                ),
            ),
        )

    def statistics(
        self, fixture: Fixture
    ) -> tuple[tuple[MatchStatistics, ...], tuple[Evidence, ...]]:
        """Per-team statistics for one played fixture."""
        identifier = self._fixture_id(fixture)
        if identifier is None:
            return ((), (self._note(fixture, "rencontre introuvable chez ce fournisseur"),))
        try:
            payload, retrieved = self._get(f"/fixtures/statistics?fixture={identifier}")
        except (CollectionError, ProviderBlockedError) as error:
            return ((), (self._note(fixture, str(error)),))
        rows = parse_statistics(
            payload, match_date=fixture.date, home_team=fixture.home
        )
        return (rows, tuple(row.evidence(retrieved) for row in rows))

    def xg_rows(self, fixture: Fixture) -> tuple[XgRow, ...]:
        """The fixture's xG as a supplement row — **only if both sides carry it**.

        A half-served match produces nothing: an xG line with one side invented
        would be worse than no line at all.
        """
        rows, _evidence = self.statistics(fixture)
        by_team = {row.team: row for row in rows}
        home = by_team.get(fixture.home)
        away = by_team.get(fixture.away)
        if home is None or away is None or home.xg is None or away.xg is None:
            return ()
        return (
            XgRow(
                date=fixture.date,
                home=fixture.home,
                away=fixture.away,
                home_xg=home.xg,
                away_xg=away.xg,
                source="API-Football",
                status=Confidence.CONFIRMED,
            ),
        )

    def _fixture_id(self, fixture: Fixture) -> int | None:
        """Find the provider's fixture id for one of our fixtures."""
        league = COMPETITION_IDS.get(fixture.competition or "")
        if league is None:
            return None
        path = (
            f"/fixtures?league={league}&season={fixture.date.year}"
            f"&date={fixture.date.isoformat()}"
        )
        try:
            payload, _retrieved = self._get(path)
        except (CollectionError, ProviderBlockedError):
            return None
        home, away = normalise(fixture.home), normalise(fixture.away)
        for block in _rows(payload):
            if (
                normalise(_text(_nested(block, "teams", "home", "name"))) == home
                and normalise(_text(_nested(block, "teams", "away", "name"))) == away
            ):
                identifier = _nested(block, "fixture", "id")
                if isinstance(identifier, int):
                    return identifier
        return None

    def _note(self, fixture: Fixture, reason: str) -> Evidence:
        return Evidence(
            key=f"api-football::{fixture.home}-{fixture.away}",
            value=None,
            source=self.source(),
            retrieved_at=self._now(),
            status=Confidence.UNAVAILABLE,
            fact_date=fixture.date,
            note=reason,
        )

    # -- Probe -------------------------------------------------------------
    def probe(self, competition: str = "en.1") -> ProviderStatus:
        """Ask the service what **this** account actually gets."""
        now = self._now()
        if not self.configured:
            return ProviderStatus(
                provider=self.name,
                reachability=Reachability.BLOCKED,
                capabilities=frozenset(),
                checked_at=now,
                detail=f"aucune clé dans {CREDENTIAL} — adaptateur écrit, non activé",
                coverage=(),
            )
        league = COMPETITION_IDS.get(competition)
        if league is None:
            return ProviderStatus(
                provider=self.name,
                reachability=Reachability.ERROR,
                capabilities=frozenset(),
                checked_at=now,
                detail=f"compétition non cartographiée : {competition!r}",
            )
        try:
            payload, retrieved = self._get(f"/fixtures?league={league}&last=1")
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
        rows = _rows(payload)
        return ProviderStatus(
            provider=self.name,
            reachability=Reachability.OK,
            capabilities=frozenset({Capability.FIXTURES}) if rows else frozenset(),
            checked_at=retrieved,
            detail=(
                f"{len(rows)} rencontre(s) renvoyée(s) pour {competition} — "
                f"la couverture champ par champ se mesure avec « foot couverture »"
            ),
            coverage=(competition,),
        )

    def coverage(
        self, competitions: Sequence[str], seasons: Sequence[str]
    ) -> CoverageMatrix:
        """Measure, field by field, what this account gets per competition-season.

        One request per competition-season, on a played fixture, and the response
        is then inspected **per field**. A generic "statistics available" is never
        turned into a confirmation of xG, npxG, shots or cards.
        """
        if not self.configured:
            return CoverageMatrix(
                note=f"aucune clé dans {CREDENTIAL} : rien n'a été demandé."
            )
        responses: dict[tuple[str, str], object] = {}
        failures: list[str] = []
        for competition in competitions:
            league = COMPETITION_IDS.get(competition)
            if league is None:
                failures.append(f"{competition} : non cartographiée")
                continue
            for season in seasons:
                year = season.split("-", maxsplit=1)[0]
                try:
                    fixtures, _ = self._get(
                        f"/fixtures?league={league}&season={year}&last=1"
                    )
                    identifier = _first_fixture_id(fixtures)
                    if identifier is None:
                        failures.append(f"{competition} {season} : aucune rencontre")
                        continue
                    payload, _ = self._get(
                        f"/fixtures/statistics?fixture={identifier}"
                    )
                except (CollectionError, ProviderBlockedError) as error:
                    failures.append(f"{competition} {season} : {error}")
                    continue
                responses[(competition, season)] = payload
        return CoverageMatrix(
            entries=field_coverage(responses),
            note=" ; ".join(failures),
        )


def _first_fixture_id(payload: object) -> int | None:
    for block in _rows(payload):
        identifier = _nested(block, "fixture", "id")
        if isinstance(identifier, int):
            return identifier
    return None


def _refuse_errors(payload: object) -> None:
    """Turn the service's own error object into a refusal, not an empty result.

    API-Football answers HTTP 200 with ``{"errors": {...}}`` when a key is
    invalid or a quota is spent. Reading that as "no data" would report an
    expired subscription as an empty league.
    """
    if not isinstance(payload, Mapping):
        return
    errors = payload.get("errors")
    if isinstance(errors, Mapping) and errors:
        detail = "; ".join(f"{k}: {v}" for k, v in errors.items())
        raise ProviderBlockedError(f"API-Football a refusé la requête — {detail}")
    if isinstance(errors, Sequence) and not isinstance(errors, (str, bytes)) and errors:
        raise ProviderBlockedError(f"API-Football a refusé la requête — {errors}")
