"""Contexte fourni par l'opérateur : xG, absences et compositions par import.

Aucun fournisseur accessible ne sert les statistiques avancées, les blessures ni
les feuilles de match, donc les rubriques qui en dépendent resteraient
définitivement sans réponse.  Ce n'est pas une fatalité : l'opérateur peut
fournir la donnée, et ce module donne à cette voie le même contrat qu'à
n'importe quelle source — lignes typées, provenance complète, statut de
confirmation explicite.

Ce que la donnée importée a le droit de faire est délibérément borné :

* les **xG** sont comparés aux buts réellement marqués et rapportés. Ils
  n'entrent **pas** dans l'estimation : aucune calibration d'une vraisemblance
  pondérée par les xG n'a été validée ici, et une pondération non validée
  présentée comme un modèle serait exactement l'ajustement arbitraire que le
  protocole interdit ;
* les **absences** produisent un *scénario sportif documenté* — le seul type
  habilité à parler d'un pire cas crédible — en portant la source qui les
  rapporte. Aucun coefficient « −15 % par absent » n'est appliqué où que ce soit ;
* les **compositions** alimentent le contrôle T−75/T−60 et le verdict de
  réévaluation.

Trois dates sont distinguées, jamais confondues :

``date``
    la date du **fait** : jour du match pour une ligne xG, jour de l'annonce
    pour une absence, jour de la rencontre pour une feuille ;
``publication``
    l'instant où l'information est devenue **publique**. Avec une heure, il fait
    foi. Sans heure, l'antériorité n'est pas démontrable dans la journée et la
    ligne n'est réputée disponible qu'au lendemain — la fiche l'indique ;
``retrieved_at``
    l'instant où l'opérateur l'a saisie, porté par la preuve.

Une ligne illisible n'est jamais devinée : elle est **rejetée avec son motif**,
et le motif remonte jusqu'à l'interface pour que l'opérateur puisse corriger.
"""

from __future__ import annotations

import csv
import datetime as dt
import math
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from zoneinfo import ZoneInfo

from foot.data.csv_source import parse_date
from foot.provenance import Confidence, Evidence, Source, utcnow

__all__ = [
    "DEFAULT_ABSENCE_WINDOW",
    "FULL_LINEUP",
    "AbsenceRow",
    "LineupRow",
    "SupplementSet",
    "XgRow",
    "load_absences",
    "load_lineups",
    "load_supplements",
    "load_xg",
    "supplements_from_text",
]

DEFAULT_ABSENCE_WINDOW = dt.timedelta(days=21)
"""Durée de validité d'une absence sans date de fin déclarée.

Hypothèse, pas une mesure : passé ce délai une blessure annoncée n'est plus une
information sur la rencontre analysée.  Une ligne peut la remplacer en donnant
``jusqu_au`` ou ``retour``.
"""

FULL_LINEUP = 11
"""Nombre de titulaires attendus pour qu'une feuille soit dite complète."""

DEFAULT_TZ = ZoneInfo("Europe/Paris")
"""Fuseau retenu pour lire un horodatage de publication sans décalage explicite.

C'est le fuseau par défaut de l'application. Lire « 19:30 » en UTC alors que
l'opérateur pense heure de Paris décalerait la disponibilité de deux heures — et
c'est exactement le genre d'écart qui fait entrer une information trop tôt.
"""


def _status(text: str) -> Confidence:
    """Map a free-text status onto a confirmation level, defaulting to probable."""
    cleaned = text.strip().lower()
    if cleaned in ("officiel", "official", "confirmé", "confirme", "confirmed"):
        return Confidence.CONFIRMED
    if cleaned in ("contradictoire", "conflit", "contradicted"):
        return Confidence.CONTRADICTED
    if cleaned in ("indisponible", "inconnu", "unavailable", ""):
        return Confidence.UNAVAILABLE
    return Confidence.PROBABLE


# --------------------------------------------------------------------------- #
# Disponibilité de l'information
# --------------------------------------------------------------------------- #


def _day_after(day: dt.date, tzinfo: dt.tzinfo) -> dt.datetime:
    """Minuit du lendemain : la première heure où une date nue est acquise."""
    return dt.datetime.combine(day + dt.timedelta(days=1), dt.time.min, tzinfo=tzinfo)


@dataclass(frozen=True, slots=True)
class _Timed:
    """Base commune : une date de fait, et éventuellement un instant de publication."""

    date: dt.date
    published_at: dt.datetime | None = None

    def available_at(self, as_of: dt.datetime) -> bool:
        """Cette information pouvait-elle être connue à ``as_of`` ?"""
        tzinfo = as_of.tzinfo or dt.timezone.utc
        if self.published_at is not None:
            return self.published_at <= as_of
        return _day_after(self.date, tzinfo) <= as_of

    @property
    def availability_rule(self) -> str:
        if self.published_at is not None:
            return (
                f"publication horodatée au "
                f"{self.published_at.strftime('%Y-%m-%d %H:%M %Z')} : fait foi"
            )
        return (
            f"aucun horodatage de publication : antériorité non démontrable dans la "
            f"journée du {self.date.isoformat()}, information réputée disponible le "
            f"lendemain à 00:00"
        )

    def unavailable_because(self, as_of: dt.datetime) -> str:
        """Why this line cannot be used at ``as_of`` — two causes, two messages.

        Telling an operator to add a publication hour they already supplied
        sends them to fix a correct file. A line published *after* the analysis
        is a different problem from a line published at an unknown hour, and
        only one of them is theirs to correct.
        """
        if self.available_at(as_of):
            return ""
        if self.published_at is not None:
            return (
                f"publication du "
                f"{self.published_at.strftime('%Y-%m-%d %H:%M %Z')} postérieure "
                f"à l'heure d'analyse "
                f"({as_of.strftime('%Y-%m-%d %H:%M %Z')}) : hors de ce dossier"
            )
        return (
            f"sans heure de publication : l'antériorité dans la journée du "
            f"{self.date.isoformat()} n'est pas démontrable, la ligne ne sera "
            f"connue qu'au lendemain 00:00"
        )


@dataclass(frozen=True, slots=True)
class XgRow(_Timed):
    """Expected goals for one played match, as supplied."""

    home: str = ""
    away: str = ""
    home_xg: float = 0.0
    away_xg: float = 0.0
    source: str = "import opérateur"
    status: Confidence = Confidence.PROBABLE

    @property
    def key(self) -> str:
        return f"xg::{self.home} vs {self.away}::{self.date.isoformat()}"

    def evidence(self, retrieved_at: dt.datetime) -> Evidence:
        return Evidence(
            key=self.key,
            # An unavailable fact carries no value — the ledger enforces it, and
            # a row can reach here marked unavailable (a status read from the
            # file, a line the availability rule rejected). Passing a number
            # alongside that status raised instead of reporting.
            value=(
                f"{self.home_xg:.2f} – {self.away_xg:.2f} xG"
                if self.status is not Confidence.UNAVAILABLE
                else None
            ),
            source=Source(name=self.source, provider=self.source, official=False),
            retrieved_at=retrieved_at,
            status=self.status,
            fact_date=self.date,
            note=self.availability_rule,
        )


@dataclass(frozen=True, slots=True)
class AbsenceRow(_Timed):
    """One reported absence, with the window over which it is claimed to hold."""

    team: str = ""
    player: str = ""
    role: str = ""
    reason: str = ""
    replacement: str = ""
    """Who is announced to take the place, when the source names someone."""

    until: dt.date | None = None
    """Declared end of the absence, when the source gives one."""

    returned_on: dt.date | None = None
    """Confirmed return to availability, which cancels the absence."""

    source: str = "import opérateur"
    status: Confidence = Confidence.PROBABLE

    @property
    def declares_return(self) -> bool:
        """Whether this line says the player is available again.

        A return is declared either by the ``retour`` column or by a reason that
        says so in as many words. Both forms occur in real sources, and reading
        only the column left a squad list permanently injured.
        """
        if self.returned_on is not None:
            return True
        return self.reason.strip().lower() in {
            "retour", "retour confirmé", "retour confirme", "disponible",
            "rétabli", "retabli", "apte", "return", "available", "fit",
        }

    @property
    def effective_from(self) -> dt.date:
        """The day this declaration starts to describe the player."""
        return self.returned_on or self.date

    @property
    def declared_at(self) -> dt.datetime:
        """When this declaration became public — the key to ordering them."""
        return self.published_at or _day_after(self.date, DEFAULT_TZ)

    @property
    def decisive(self) -> bool:
        """Whether the position is one the protocol singles out as material."""
        return self.role.strip().lower() in {
            "gardien", "défenseur central", "charnière", "milieu défensif",
            "créateur", "buteur", "tireur de penalty",
        }

    def holds_on(self, day: dt.date) -> bool:
        """Is this absence still claimed to apply on ``day``?"""
        if self.date > day:
            return False
        if self.returned_on is not None and self.returned_on <= day:
            return False
        if self.until is not None:
            return day <= self.until
        return day - self.date <= DEFAULT_ABSENCE_WINDOW

    @property
    def validity(self) -> str:
        if self.returned_on is not None:
            return f"retour confirmé le {self.returned_on.isoformat()}"
        if self.until is not None:
            return f"déclarée jusqu'au {self.until.isoformat()}"
        return (
            f"sans date de fin : validité supposée de "
            f"{DEFAULT_ABSENCE_WINDOW.days} jours (hypothèse)"
        )

    @property
    def key(self) -> str:
        return f"absence::{self.team}::{self.player}"

    def evidence(self, retrieved_at: dt.datetime) -> Evidence:
        return Evidence(
            key=self.key,
            value=(
                f"{self.player} ({self.role or 'poste non précisé'})"
                if self.status is not Confidence.UNAVAILABLE
                else None
            ),
            source=Source(
                name=self.source,
                provider=self.source,
                official=self.status is Confidence.CONFIRMED,
            ),
            retrieved_at=retrieved_at,
            status=self.status,
            fact_date=self.date,
            note=f"{self.reason or 'motif non précisé'} ; {self.validity}",
        )


@dataclass(frozen=True, slots=True)
class LineupRow(_Timed):
    """One announced starter or substitute, attached to a fixture."""

    team: str = ""
    player: str = ""
    role: str = ""
    starting: bool = True
    opponent: str = ""
    """The other side, when the file names it — makes the attachment explicit."""

    source: str = "import opérateur"
    status: Confidence = Confidence.PROBABLE

    @property
    def key(self) -> str:
        return f"composition::{self.team}::{self.role or self.player}"

    @property
    def version(self) -> tuple[dt.date, str, str, bool]:
        """Which published sheet this row belongs to.

        A team publishes several sheets for one match — a probable eleven, then
        the official one. Identifying a row by player alone made the second
        publication look like eleven duplicates of the first, so the official
        sheet could never replace the probable one.
        """
        return (
            self.date,
            self.published_at.isoformat() if self.published_at else "",
            self.source,
            self.status is Confidence.CONFIRMED,
        )

    @property
    def version_order(self) -> tuple[dt.datetime, int]:
        """Chronological rank of this row's sheet, independent of file order.

        Sheets are ordered by publication instant; an official sheet outranks a
        probable one published at the same moment, because that is what it is.
        """
        moment = self.published_at or _day_after(self.date, DEFAULT_TZ)
        return (moment, 1 if self.status is Confidence.CONFIRMED else 0)

    def concerns(self, *, team: str, match_date: dt.date, opponent: str = "") -> bool:
        """Is this row this team's sheet **for this match**?

        A sheet is attached to a fixture, not to a team in general.  Accepting
        any older row is how a 1 August team sheet became "today's official
        lineup" for a 14 September match.
        """
        if self.team != team or self.date != match_date:
            return False
        return not (self.opponent and opponent and self.opponent != opponent)

    def evidence(self, retrieved_at: dt.datetime) -> Evidence:
        return Evidence(
            key=self.key,
            value=self.player if self.status is not Confidence.UNAVAILABLE else None,
            source=Source(
                name=self.source,
                provider=self.source,
                official=self.status is Confidence.CONFIRMED,
            ),
            retrieved_at=retrieved_at,
            status=self.status,
            fact_date=self.date,
            note=("titulaire" if self.starting else "remplaçant")
            + f" ; {self.availability_rule}",
        )


@dataclass(frozen=True, slots=True)
class SupplementSet:
    """Everything the operator supplied, indexed for the engine."""

    xg: tuple[XgRow, ...] = ()
    absences: tuple[AbsenceRow, ...] = ()
    lineups: tuple[LineupRow, ...] = ()
    retrieved_at: dt.datetime | None = None
    rejected: tuple[str, ...] = field(default_factory=tuple)
    """Lines refused, each with the reason, so the operator can fix them."""

    def __bool__(self) -> bool:
        return bool(self.xg or self.absences or self.lineups)

    def available_at(self, as_of: dt.datetime) -> SupplementSet:
        """The subset that could genuinely be known at ``as_of``.

        Filtering ledger evidence is not enough: what matters is the data the
        scenarios, the lineup check and the decision actually consume.  Applying
        the cut once, here, is what makes a replayed analysis reproducible.
        """
        return replace(
            self,
            xg=tuple(row for row in self.xg if row.available_at(as_of)),
            absences=tuple(row for row in self.absences if row.available_at(as_of)),
            lineups=tuple(row for row in self.lineups if row.available_at(as_of)),
        )

    def absences_for(
        self,
        team: str,
        *,
        on_or_before: dt.date,
        as_of: dt.datetime | None = None,
    ) -> tuple[AbsenceRow, ...]:
        """Absences still claimed to hold for ``team`` on the match date.

        Each player's state is **resolved chronologically** before anything is
        counted: keeping every declaration ever filed left a keeper injured on
        the day his club confirmed his return. The last declaration known at
        ``as_of`` decides — a confirmed return clears the absence, and a later
        injury reinstates it.
        """
        known = [
            row
            for row in self.absences
            if row.team == team
            and (as_of is None or row.available_at(as_of))
            and row.effective_from <= on_or_before
        ]
        latest: dict[str, AbsenceRow] = {}
        for row in sorted(known, key=lambda r: (r.declared_at, r.effective_from)):
            latest[row.player] = row
        return tuple(
            row
            for row in latest.values()
            if not row.declares_return and row.holds_on(on_or_before)
        )

    def pending_absence_updates(
        self,
        team: str,
        *,
        on_or_before: dt.date,
        as_of: dt.datetime,
    ) -> tuple[AbsenceRow, ...]:
        """Declarations that would change a player's state but are not yet knowable.

        The availability rule is right to refuse them — an undated line from
        today is not demonstrably public today. But refusing them *silently* is
        not: an injury filed yesterday is admitted while the return filed today
        by the same club is not, so the report asserts an absence its own source
        has already lifted. Naming these pending lines is what keeps the asymmetry
        visible instead of letting it pass for a fact.
        """
        active = {row.player for row in self.absences_for(
            team, on_or_before=on_or_before, as_of=as_of
        )}
        pending: dict[str, AbsenceRow] = {}
        for row in sorted(
            (r for r in self.absences if r.team == team and not r.available_at(as_of)),
            key=lambda r: r.declared_at,
        ):
            if row.declares_return and row.player not in active:
                continue
            pending[row.player] = row
        return tuple(pending.values())

    def import_notes(
        self,
        *,
        teams: Sequence[str],
        on_or_before: dt.date,
        as_of: dt.datetime,
    ) -> tuple[str, ...]:
        """Lines the operator supplied that this analysis could not use.

        These belong to the **import report**, never to the sealed dossier: a
        historical dossier cannot depend on information that did not exist at
        its own date, and adding a note about it changed the fingerprint.
        """
        notes: list[str] = []
        for team in teams:
            for row in self.pending_absence_updates(
                team, on_or_before=on_or_before, as_of=as_of
            ):
                notes.append(
                    f"{row.team} — {row.player} "
                    f"({row.reason or 'motif non précisé'}, "
                    f"{row.date.isoformat()}) : {row.unavailable_because(as_of)}"
                )
        return tuple(notes)

    def lineup_versions(
        self,
        team: str,
        *,
        match_date: dt.date,
        opponent: str = "",
        as_of: dt.datetime | None = None,
    ) -> tuple[tuple[LineupRow, ...], ...]:
        """Every sheet published for this team and this fixture, oldest first.

        Grouping by version — and sorting rather than trusting the file — is
        what makes the result independent of the order the rows were written in.
        """
        rows = [
            row
            for row in self.lineups
            if row.concerns(team=team, match_date=match_date, opponent=opponent)
            and (as_of is None or row.available_at(as_of))
        ]
        grouped: dict[tuple[dt.date, str, str, bool], list[LineupRow]] = {}
        for row in rows:
            grouped.setdefault(row.version, []).append(row)
        return tuple(
            tuple(sheet)
            for _key, sheet in sorted(
                grouped.items(), key=lambda item: item[1][0].version_order
            )
        )

    def lineup_for(
        self,
        team: str,
        *,
        match_date: dt.date,
        opponent: str = "",
        as_of: dt.datetime | None = None,
    ) -> tuple[LineupRow, ...]:
        """The **latest** sheet available for this team and fixture, or nothing.

        Returning every row ever published would merge a probable eleven with
        the official one into a twenty-two-man team sheet.
        """
        versions = self.lineup_versions(
            team, match_date=match_date, opponent=opponent, as_of=as_of
        )
        return versions[-1] if versions else ()

    def xg_for(self, team: str, *, on_or_before: dt.date) -> list[tuple[float, float]]:
        """``(xG pour, xG contre)`` for a team's supplied matches, oldest first."""
        out: list[tuple[float, float]] = []
        for row in sorted(self.xg, key=lambda r: r.date):
            if row.date > on_or_before:
                continue
            if row.home == team:
                out.append((row.home_xg, row.away_xg))
            elif row.away == team:
                out.append((row.away_xg, row.home_xg))
        return out

    def evidence(self) -> list[Evidence]:
        moment = self.retrieved_at or utcnow()
        items: list[Evidence] = [row.evidence(moment) for row in self.xg]
        items.extend(row.evidence(moment) for row in self.absences)
        items.extend(row.evidence(moment) for row in self.lineups)
        return items


# --------------------------------------------------------------------------- #
# Lecture des fichiers
# --------------------------------------------------------------------------- #


def _rows(path: str | Path, encoding: str = "utf-8-sig") -> Iterable[dict[str, str]]:
    with Path(path).open(newline="", encoding=encoding) as handle:
        yield from csv.DictReader(handle)


def _get(row: dict[str, str], *names: str, default: str = "") -> str:
    for name in names:
        value = row.get(name)
        if value:
            return value.strip()
    return default


def _number(text: str, *, label: str) -> float:
    """Read a finite, non-negative quantity, or say precisely what is wrong."""
    value = float(text.replace(",", "."))
    if not math.isfinite(value):
        raise ValueError(f"{label} non fini : {text!r}")
    if value < 0.0:
        raise ValueError(f"{label} négatif : {text!r}")
    return value


def _optional_date(text: str, *, label: str) -> dt.date | None:
    if not text:
        return None
    try:
        return parse_date(text)
    except ValueError as error:
        raise ValueError(f"{label} illisible : {text!r}") from error


def _published(text: str, *, tzinfo: dt.tzinfo) -> dt.datetime | None:
    """Read a publication instant. A bare date carries no time, so it is refused.

    Returning ``None`` for a date-only value is deliberate: the caller then falls
    back to "available the next day", which is the honest reading.
    """
    cleaned = text.strip()
    if not cleaned:
        return None
    for pattern in ("%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M:%S"):
        try:
            return dt.datetime.strptime(cleaned, pattern).replace(tzinfo=tzinfo)
        except ValueError:
            continue
    try:
        parsed = dt.datetime.fromisoformat(cleaned)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tzinfo)
    return parsed if (parsed.hour or parsed.minute or parsed.second) else None


def _seen(key: object, registry: set[object], *, label: str) -> None:
    if key in registry:
        raise ValueError(f"doublon ignoré : {label}")
    registry.add(key)


def load_xg(
    path: str | Path,
    *,
    source: str = "import opérateur",
    tzinfo: dt.tzinfo = DEFAULT_TZ,
    rejected: list[str] | None = None,
) -> tuple[XgRow, ...]:
    """Read ``date, home, away, home_xg, away_xg [, source, statut, publication]``."""
    out: list[XgRow] = []
    seen: set[object] = set()
    for number, row in enumerate(_rows(path), start=2):
        try:
            date = parse_date(_get(row, "date", "Date"))
            home = _get(row, "home", "HomeTeam", "domicile")
            away = _get(row, "away", "AwayTeam", "exterieur", "extérieur")
            if not home or not away:
                raise ValueError("équipe manquante")
            _seen((date, home, away), seen, label=f"{home} – {away} du {date}")
            out.append(
                XgRow(
                    date=date,
                    published_at=_published(
                        _get(row, "publication", "published", "publie_le"), tzinfo=tzinfo
                    ),
                    home=home,
                    away=away,
                    home_xg=_number(
                        _get(row, "home_xg", "xg_home", "xGH"), label="home_xg"
                    ),
                    away_xg=_number(
                        _get(row, "away_xg", "xg_away", "xGA"), label="away_xg"
                    ),
                    source=_get(row, "source", default=source),
                    status=_status(_get(row, "statut", "status")),
                )
            )
        except (KeyError, ValueError) as error:
            if rejected is not None:
                rejected.append(f"xG ligne {number} : {error}")
    return tuple(out)


def load_absences(
    path: str | Path,
    *,
    source: str = "import opérateur",
    tzinfo: dt.tzinfo = DEFAULT_TZ,
    rejected: list[str] | None = None,
) -> tuple[AbsenceRow, ...]:
    """Read ``date, equipe, joueur [, poste, motif, source, statut, jusqu_au, retour]``."""
    out: list[AbsenceRow] = []
    seen: set[object] = set()
    for number, row in enumerate(_rows(path), start=2):
        try:
            date = parse_date(_get(row, "date", "Date"))
            team = _get(row, "team", "equipe", "équipe")
            player = _get(row, "player", "joueur")
            if not team or not player:
                raise ValueError("équipe ou joueur manquant")
            published = _published(
                _get(row, "publication", "published", "publie_le"), tzinfo=tzinfo
            )
            reason = _get(row, "reason", "motif", "raison")
            _seen(
                (date, team, player, published, reason),
                seen,
                label=f"{player} ({team}) du {date}, même déclaration",
            )
            out.append(
                AbsenceRow(
                    date=date,
                    published_at=published,
                    team=team,
                    player=player,
                    role=_get(row, "role", "poste"),
                    reason=reason,
                    replacement=_get(row, "remplacant", "remplaçant", "replacement"),
                    until=_optional_date(
                        _get(row, "jusqu_au", "jusqu_a", "until"), label="jusqu_au"
                    ),
                    returned_on=_optional_date(
                        _get(row, "retour", "returned_on"), label="retour"
                    ),
                    source=_get(row, "source", default=source),
                    status=_status(_get(row, "statut", "status")),
                )
            )
        except (KeyError, ValueError) as error:
            if rejected is not None:
                rejected.append(f"absence ligne {number} : {error}")
    return tuple(out)


def load_lineups(
    path: str | Path,
    *,
    source: str = "import opérateur",
    tzinfo: dt.tzinfo = DEFAULT_TZ,
    rejected: list[str] | None = None,
) -> tuple[LineupRow, ...]:
    """Read ``date, equipe, joueur [, poste, titulaire, source, statut, publication]``."""
    out: list[LineupRow] = []
    seen: set[object] = set()
    for number, row in enumerate(_rows(path), start=2):
        try:
            date = parse_date(_get(row, "date", "Date"))
            team = _get(row, "team", "equipe", "équipe")
            player = _get(row, "player", "joueur")
            if not team or not player:
                raise ValueError("équipe ou joueur manquant")
            published = _published(
                _get(row, "publication", "published", "publie_le"), tzinfo=tzinfo
            )
            status = _status(_get(row, "statut", "status"))
            _seen(
                (date, team, player, published, status),
                seen,
                label=f"{player} ({team}) du {date}, même version",
            )
            starting = _get(row, "titulaire", "starting", default="oui").lower()
            out.append(
                LineupRow(
                    date=date,
                    published_at=published,
                    team=team,
                    player=player,
                    role=_get(row, "role", "poste"),
                    starting=starting not in ("non", "no", "false", "0"),
                    opponent=_get(row, "adversaire", "opponent"),
                    source=_get(row, "source", default=source),
                    status=status,
                )
            )
        except (KeyError, ValueError) as error:
            if rejected is not None:
                rejected.append(f"composition ligne {number} : {error}")
    return tuple(out)


def load_supplements(
    *,
    xg_csv: str | Path | None = None,
    absences_csv: str | Path | None = None,
    lineups_csv: str | Path | None = None,
    source: str = "import opérateur",
    tzinfo: dt.tzinfo = DEFAULT_TZ,
) -> SupplementSet:
    """Load whichever files the operator supplied; absent ones stay empty.

    A file that cannot be opened is reported like a bad line rather than raising:
    one unreadable path must not cost the operator the rest of their analysis.
    """
    rejected: list[str] = []

    def read(
        path: str | Path | None, loader: object, label: str
    ) -> tuple[object, ...]:
        if path is None:
            return ()
        try:
            return loader(  # type: ignore[operator,no-any-return]
                path, source=source, tzinfo=tzinfo, rejected=rejected
            )
        except OSError as error:
            rejected.append(f"{label} : fichier illisible ({error})")
            return ()

    xg = read(xg_csv, load_xg, "xG")
    absences = read(absences_csv, load_absences, "absences")
    lineups = read(lineups_csv, load_lineups, "compositions")
    return SupplementSet(
        xg=xg,  # type: ignore[arg-type]
        absences=absences,  # type: ignore[arg-type]
        lineups=lineups,  # type: ignore[arg-type]
        retrieved_at=utcnow(),
        rejected=tuple(rejected),
    )


def supplements_from_text(
    *,
    xg: str = "",
    absences: str = "",
    lineups: str = "",
    source: str = "saisie opérateur",
    tzinfo: dt.tzinfo = DEFAULT_TZ,
    directory: Path | None = None,
) -> SupplementSet:
    """Same loaders, fed by pasted text rather than by files.

    The browser form lets the operator paste a block instead of uploading; going
    through the very same parser is what keeps the two routes from drifting.
    """
    blocks = {"xg.csv": xg, "absences.csv": absences, "compositions.csv": lineups}
    with tempfile.TemporaryDirectory() as temporary:
        root = directory or Path(temporary)
        paths: dict[str, Path | None] = {}
        for name, text in blocks.items():
            if not text.strip():
                paths[name] = None
                continue
            path = root / name
            path.write_text(text, encoding="utf-8")
            paths[name] = path
        return load_supplements(
            xg_csv=paths["xg.csv"],
            absences_csv=paths["absences.csv"],
            lineups_csv=paths["compositions.csv"],
            source=source,
            tzinfo=tzinfo,
        )

