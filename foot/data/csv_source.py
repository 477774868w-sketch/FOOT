"""Reading and writing match data as CSV.

The de facto standard for historical European league results is the layout
published by football-data.co.uk (``Date``, ``HomeTeam``, ``AwayTeam``,
``FTHG``, ``FTAG``, plus bookmaker columns), so that layout is auto-detected.
Any other column naming can be supplied explicitly.

Nothing here touches the network: a modelling library that silently downloads
data cannot be reproduced, and reproducibility is the whole point.
"""

from __future__ import annotations

import csv
import datetime as dt
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

from foot.domain import Fixture, Match, MatchLog, Score
from foot.market.odds import MatchOdds

__all__ = ["load_fixtures", "load_matches", "load_odds", "write_matches"]

_DATE_FORMATS = ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%m/%d/%Y", "%d.%m.%Y")

_HOME_ALIASES = ("HomeTeam", "Home", "home", "home_team", "HT")
_AWAY_ALIASES = ("AwayTeam", "Away", "away", "away_team", "AT")
_HOME_GOAL_ALIASES = ("FTHG", "HG", "home_goals", "HomeGoals", "home_score")
_AWAY_GOAL_ALIASES = ("FTAG", "AG", "away_goals", "AwayGoals", "away_score")
_DATE_ALIASES = ("Date", "date", "MatchDate", "kickoff")


def _pick(row: Mapping[str, str], aliases: Sequence[str], *, label: str) -> str:
    for alias in aliases:
        if alias in row and row[alias] not in ("", None):
            return row[alias]
    raise KeyError(
        f"could not find a {label} column; tried {list(aliases)} "
        f"against {sorted(row)[:12]}"
    )


def parse_date(text: str, formats: Sequence[str] = _DATE_FORMATS) -> dt.date:
    """Parse a date, trying the common European and ISO layouts in turn."""
    cleaned = text.strip()
    for fmt in formats:
        try:
            return dt.datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    # The message reaches the operator's own screen, in a French interface:
    # it names an example rather than dumping strptime patterns at them.
    raise ValueError(
        f"date illisible : {text!r} — formats acceptés : "
        f"13/09/2026, 13/09/26, 2026-09-13, 13.09.2026"
    )


def load_matches(
    path: str | Path,
    *,
    competition: str | None = None,
    date_formats: Sequence[str] = _DATE_FORMATS,
    encoding: str = "utf-8-sig",
) -> MatchLog:
    """Read played matches from a CSV file.

    Rows missing a team, a date or a score are skipped rather than guessed at:
    silently inventing a 0-0 would corrupt every model downstream.
    """
    matches: list[Match] = []
    with Path(path).open(newline="", encoding=encoding) as handle:
        for row in csv.DictReader(handle):
            try:
                date = parse_date(_pick(row, _DATE_ALIASES, label="date"), date_formats)
                home = _pick(row, _HOME_ALIASES, label="home team").strip()
                away = _pick(row, _AWAY_ALIASES, label="away team").strip()
                home_goals = int(float(_pick(row, _HOME_GOAL_ALIASES, label="home goals")))
                away_goals = int(float(_pick(row, _AWAY_GOAL_ALIASES, label="away goals")))
            except (KeyError, ValueError):
                continue
            matches.append(
                Match(
                    home=home,
                    away=away,
                    date=date,
                    score=Score(home_goals, away_goals),
                    competition=competition or row.get("Div") or row.get("competition"),
                )
            )
    return MatchLog(matches)


def load_fixtures(
    path: str | Path,
    *,
    competition: str | None = None,
    date_formats: Sequence[str] = _DATE_FORMATS,
    encoding: str = "utf-8-sig",
) -> tuple[Fixture, ...]:
    """Read upcoming fixtures from a CSV file: ``date, home, away [, neutre]``.

    The operator needs this route wherever no automatic calendar reaches: a
    fixture that cannot be verified is never recommended, so without it a whole
    competition stays unanalysable however good the history is.
    """
    fixtures: list[Fixture] = []
    with Path(path).open(newline="", encoding=encoding) as handle:
        for row in csv.DictReader(handle):
            try:
                date = parse_date(_pick(row, _DATE_ALIASES, label="date"), date_formats)
                home = _pick(row, _HOME_ALIASES, label="home team").strip()
                away = _pick(row, _AWAY_ALIASES, label="away team").strip()
            except (KeyError, ValueError):
                continue
            if not home or not away:
                continue
            neutral = (row.get("neutre") or row.get("neutral") or "").strip().lower()
            fixtures.append(
                Fixture(
                    home=home,
                    away=away,
                    date=date,
                    neutral=neutral in ("oui", "yes", "true", "1"),
                    competition=competition
                    or row.get("Div")
                    or row.get("competition")
                    or "",
                )
            )
    return tuple(fixtures)


def load_odds(
    path: str | Path,
    *,
    prefix: str = "B365",
    bookmaker: str | None = None,
    date_formats: Sequence[str] = _DATE_FORMATS,
    encoding: str = "utf-8-sig",
) -> dict[Fixture, MatchOdds]:
    """Read 1X2 decimal odds keyed by fixture.

    Args:
        prefix: bookmaker column prefix; football-data.co.uk uses ``B365``,
            ``PS`` (Pinnacle), ``WH`` and others, each suffixed ``H``/``D``/``A``.
    """
    columns = (f"{prefix}H", f"{prefix}D", f"{prefix}A")
    book: dict[Fixture, MatchOdds] = {}
    with Path(path).open(newline="", encoding=encoding) as handle:
        for row in csv.DictReader(handle):
            try:
                date = parse_date(_pick(row, _DATE_ALIASES, label="date"), date_formats)
                fixture = Fixture(
                    home=_pick(row, _HOME_ALIASES, label="home team").strip(),
                    away=_pick(row, _AWAY_ALIASES, label="away team").strip(),
                    date=date,
                    competition=row.get("Div") or row.get("competition"),
                )
                prices = [float(row[column]) for column in columns]
            except (KeyError, ValueError):
                continue
            book[fixture] = MatchOdds(
                prices[0], prices[1], prices[2], bookmaker=bookmaker or prefix
            )
    return book


def write_matches(path: str | Path, matches: Iterable[Match]) -> int:
    """Write matches in the football-data.co.uk layout; returns the row count."""
    rows = list(matches)
    with Path(path).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Div", "Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR"])
        for match in rows:
            writer.writerow(
                [
                    match.competition or "",
                    match.date.strftime("%d/%m/%Y"),
                    match.home,
                    match.away,
                    match.score.home,
                    match.score.away,
                    match.outcome.value,
                ]
            )
    return len(rows)
