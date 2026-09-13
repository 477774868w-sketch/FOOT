"""Operator-supplied context: xG, absences and lineups by CSV import.

No reachable provider serves advanced statistics, injuries or team sheets, so
the rubrics that need them would stay permanently unanswered.  They need not:
the operator can supply the data, and this module gives that route the same
contract as any other source — typed rows, full provenance, explicit
confirmation status.

What the imported data is allowed to do is deliberately bounded:

* **xG** is compared with goals actually scored and reported.  It does **not**
  enter the estimation, because no calibration of an xG-weighted likelihood has
  been validated here, and an unvalidated weighting presented as a model would
  be exactly the arbitrary adjustment the protocol forbids.
* **Absences** produce a *documented sporting scenario* — the only scenario kind
  entitled to speak of a credible worst case — carrying the source that reported
  them.  No "-15 % attack per missing player" coefficient is applied anywhere.
* **Lineups** feed the T−75/T−60 check and the re-evaluation verdict.

Every row keeps its own date and confirmation status, so a probable sheet is
never reported as an official one.
"""

from __future__ import annotations

import csv
import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from foot.data.csv_source import parse_date
from foot.provenance import Confidence, Evidence, Source, utcnow

__all__ = [
    "AbsenceRow",
    "LineupRow",
    "SupplementSet",
    "XgRow",
    "load_absences",
    "load_lineups",
    "load_supplements",
    "load_xg",
]


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


@dataclass(frozen=True, slots=True)
class XgRow:
    """Expected goals for one played match, as supplied."""

    date: dt.date
    home: str
    away: str
    home_xg: float
    away_xg: float
    source: str = "import opérateur"
    status: Confidence = Confidence.PROBABLE

    def evidence(self, retrieved_at: dt.datetime) -> Evidence:
        return Evidence(
            key=f"xg::{self.home} vs {self.away}::{self.date.isoformat()}",
            value=f"{self.home_xg:.2f} – {self.away_xg:.2f}",
            unit="buts attendus",
            source=Source(name=self.source, provider=self.source),
            retrieved_at=retrieved_at,
            status=self.status,
            fact_date=self.date,
            note="xG importé ; comparé aux buts marqués, n'entre pas dans l'estimation",
        )


@dataclass(frozen=True, slots=True)
class AbsenceRow:
    """One player reported unavailable."""

    date: dt.date
    team: str
    player: str
    role: str = ""
    reason: str = ""
    source: str = "import opérateur"
    status: Confidence = Confidence.PROBABLE

    @property
    def decisive(self) -> bool:
        """Whether the position is one the protocol singles out as material."""
        return self.role.strip().lower() in {
            "gardien", "défenseur central", "charnière", "milieu défensif",
            "créateur", "buteur", "tireur de penalty",
        }

    def evidence(self, retrieved_at: dt.datetime) -> Evidence:
        return Evidence(
            key=f"absence::{self.team}::{self.player}",
            value=f"{self.player} ({self.role or 'poste non précisé'})",
            source=Source(name=self.source, provider=self.source,
                          official=self.status is Confidence.CONFIRMED),
            retrieved_at=retrieved_at,
            status=self.status,
            fact_date=self.date,
            note=self.reason or None,
        )


@dataclass(frozen=True, slots=True)
class LineupRow:
    """One announced starter or substitute."""

    date: dt.date
    team: str
    player: str
    role: str = ""
    starting: bool = True
    source: str = "import opérateur"
    status: Confidence = Confidence.PROBABLE

    def evidence(self, retrieved_at: dt.datetime) -> Evidence:
        return Evidence(
            key=f"composition::{self.team}::{self.role or self.player}",
            value=self.player,
            source=Source(name=self.source, provider=self.source,
                          official=self.status is Confidence.CONFIRMED),
            retrieved_at=retrieved_at,
            status=self.status,
            fact_date=self.date,
            note="titulaire" if self.starting else "remplaçant",
        )


@dataclass(frozen=True, slots=True)
class SupplementSet:
    """Everything the operator supplied, indexed for the engine."""

    xg: tuple[XgRow, ...] = ()
    absences: tuple[AbsenceRow, ...] = ()
    lineups: tuple[LineupRow, ...] = ()
    retrieved_at: dt.datetime | None = None

    def __bool__(self) -> bool:
        return bool(self.xg or self.absences or self.lineups)

    def absences_for(self, team: str, *, on_or_before: dt.date) -> tuple[AbsenceRow, ...]:
        return tuple(
            row for row in self.absences if row.team == team and row.date <= on_or_before
        )

    def lineups_for(self, team: str, *, on_or_before: dt.date) -> tuple[LineupRow, ...]:
        return tuple(
            row for row in self.lineups if row.team == team and row.date <= on_or_before
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


def _rows(path: str | Path, encoding: str = "utf-8-sig") -> Iterable[dict[str, str]]:
    with Path(path).open(newline="", encoding=encoding) as handle:
        yield from csv.DictReader(handle)


def _get(row: dict[str, str], *names: str, default: str = "") -> str:
    for name in names:
        value = row.get(name)
        if value:
            return value.strip()
    return default


def load_xg(path: str | Path, *, source: str = "import opérateur") -> tuple[XgRow, ...]:
    """Read ``date, home, away, home_xg, away_xg [, source, statut]``."""
    out: list[XgRow] = []
    for row in _rows(path):
        try:
            out.append(
                XgRow(
                    date=parse_date(_get(row, "date", "Date")),
                    home=_get(row, "home", "HomeTeam", "domicile"),
                    away=_get(row, "away", "AwayTeam", "exterieur", "extérieur"),
                    home_xg=float(_get(row, "home_xg", "xg_home", "xGH").replace(",", ".")),
                    away_xg=float(_get(row, "away_xg", "xg_away", "xGA").replace(",", ".")),
                    source=_get(row, "source", default=source),
                    status=_status(_get(row, "statut", "status")),
                )
            )
        except (KeyError, ValueError):
            continue  # a malformed row is skipped, never guessed at
    return tuple(out)


def load_absences(
    path: str | Path, *, source: str = "import opérateur"
) -> tuple[AbsenceRow, ...]:
    """Read ``date, team, player [, role, reason, source, statut]``."""
    out: list[AbsenceRow] = []
    for row in _rows(path):
        try:
            out.append(
                AbsenceRow(
                    date=parse_date(_get(row, "date", "Date")),
                    team=_get(row, "team", "equipe", "équipe"),
                    player=_get(row, "player", "joueur"),
                    role=_get(row, "role", "poste"),
                    reason=_get(row, "reason", "motif", "raison"),
                    source=_get(row, "source", default=source),
                    status=_status(_get(row, "statut", "status")),
                )
            )
        except (KeyError, ValueError):
            continue
    return tuple(r for r in out if r.team and r.player)


def load_lineups(
    path: str | Path, *, source: str = "import opérateur"
) -> tuple[LineupRow, ...]:
    """Read ``date, team, player [, role, titulaire, source, statut]``."""
    out: list[LineupRow] = []
    for row in _rows(path):
        try:
            starting = _get(row, "titulaire", "starting", default="oui").lower()
            out.append(
                LineupRow(
                    date=parse_date(_get(row, "date", "Date")),
                    team=_get(row, "team", "equipe", "équipe"),
                    player=_get(row, "player", "joueur"),
                    role=_get(row, "role", "poste"),
                    starting=starting not in ("non", "no", "false", "0"),
                    source=_get(row, "source", default=source),
                    status=_status(_get(row, "statut", "status")),
                )
            )
        except (KeyError, ValueError):
            continue
    return tuple(r for r in out if r.team and r.player)


def load_supplements(
    *,
    xg_csv: str | Path | None = None,
    absences_csv: str | Path | None = None,
    lineups_csv: str | Path | None = None,
    source: str = "import opérateur",
) -> SupplementSet:
    """Load whichever files the operator supplied; absent ones stay empty."""
    return SupplementSet(
        xg=load_xg(xg_csv, source=source) if xg_csv else (),
        absences=load_absences(absences_csv, source=source) if absences_csv else (),
        lineups=load_lineups(lineups_csv, source=source) if lineups_csv else (),
        retrieved_at=utcnow(),
    )
