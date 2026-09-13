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
from collections.abc import Iterable
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
            value=f"{self.home_xg:.2f} – {self.away_xg:.2f} xG",
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
            value=f"{self.player} ({self.role or 'poste non précisé'})",
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
            value=self.player,
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
        """Absences still claimed to hold for ``team`` on the match date."""
        rows = self.absences
        if as_of is not None:
            rows = tuple(row for row in rows if row.available_at(as_of))
        return tuple(
            row for row in rows if row.team == team and row.holds_on(on_or_before)
        )

    def lineup_for(
        self,
        team: str,
        *,
        match_date: dt.date,
        opponent: str = "",
        as_of: dt.datetime | None = None,
    ) -> tuple[LineupRow, ...]:
        """This team's sheet **for this fixture**, or nothing."""
        rows = self.lineups
        if as_of is not None:
            rows = tuple(row for row in rows if row.available_at(as_of))
        return tuple(
            row
            for row in rows
            if row.concerns(team=team, match_date=match_date, opponent=opponent)
        )

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
            _seen((date, team, player), seen, label=f"{player} ({team}) du {date}")
            out.append(
                AbsenceRow(
                    date=date,
                    published_at=_published(
                        _get(row, "publication", "published", "publie_le"), tzinfo=tzinfo
                    ),
                    team=team,
                    player=player,
                    role=_get(row, "role", "poste"),
                    reason=_get(row, "reason", "motif", "raison"),
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
            _seen((date, team, player), seen, label=f"{player} ({team}) du {date}")
            starting = _get(row, "titulaire", "starting", default="oui").lower()
            out.append(
                LineupRow(
                    date=date,
                    published_at=_published(
                        _get(row, "publication", "published", "publie_le"), tzinfo=tzinfo
                    ),
                    team=team,
                    player=player,
                    role=_get(row, "role", "poste"),
                    starting=starting not in ("non", "no", "false", "0"),
                    opponent=_get(row, "adversaire", "opponent"),
                    source=_get(row, "source", default=source),
                    status=_status(_get(row, "statut", "status")),
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

