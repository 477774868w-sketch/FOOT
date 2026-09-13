"""Reading what the operator typed, and never losing a match.

The contract is strict: **one input line produces exactly one entry in the
output**.  A fixture that cannot be identified is reported with the precise
thing that is missing — an unknown club, an ambiguous short name, a date that
matches no scheduled meeting — rather than dropped because it was awkward.

Kickoff status is computed against ``as_of`` in the operator's timezone.  A
match that has already started is flagged and excluded from pre-match
recommendation, because presenting a pre-match read as if it were live is the
one mistake that cannot be corrected downstream.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from foot.collect.manual import parse_odds_line
from foot.domain import Fixture

__all__ = [
    "DEFAULT_TIMEZONE",
    "KickoffStatus",
    "MatchRequest",
    "RequestStatus",
    "ResolvedMatch",
    "parse_requests",
    "resolve_timezone",
]

DEFAULT_TIMEZONE = "Europe/Paris"

_SEPARATORS = (" vs ", " v ", " - ", " – ", " — ", " x ", " contre ")
_DATE_PATTERNS = (
    (re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"), "ymd"),
    (re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b"), "dmy"),
    (re.compile(r"\b(\d{1,2})/(\d{1,2})\b"), "dm"),
)
_TIME_PATTERN = re.compile(r"\b(\d{1,2})[:hH](\d{2})\b")
_ODDS_PATTERN = re.compile(r"@\s*([\d.,]+[\s/;|]+[\d.,]+[\s/;|]+[\d.,]+)\s*$")

# A league match plus stoppages and half-time; used only to judge whether a
# kickoff is still in progress, never to model anything.
_MATCH_DURATION = dt.timedelta(hours=2)


def resolve_timezone(name: str | None) -> ZoneInfo:
    """Look up a timezone, falling back to Europe/Paris with no silent failure."""
    try:
        return ZoneInfo(name or DEFAULT_TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ValueError(
            f"fuseau horaire inconnu : {name!r} (exemple valide : {DEFAULT_TIMEZONE})"
        ) from error


class RequestStatus(Enum):
    """How far a typed line got toward being an analysable fixture."""

    RESOLVED = "identifiée"
    AMBIGUOUS = "ambiguë"
    UNKNOWN_TEAM = "équipe inconnue"
    UNKNOWN_COMPETITION = "compétition inconnue"
    NOT_SCHEDULED = "aucune rencontre programmée correspondante"
    UNPARSED = "ligne illisible"

    @property
    def resolved(self) -> bool:
        return self is RequestStatus.RESOLVED


class KickoffStatus(Enum):
    """Where the match sits relative to the analysis instant."""

    SCHEDULED = "à venir"
    IN_PLAY = "commencé (en cours)"
    FINISHED = "terminé"
    UNKNOWN_TIME = "horaire inconnu"
    POSTPONED = "reporté"
    CANCELLED = "annulé"

    @property
    def allows_prematch(self) -> bool:
        """Only a match that has not kicked off may receive a pre-match bet."""
        return self in (KickoffStatus.SCHEDULED, KickoffStatus.UNKNOWN_TIME)


@dataclass(frozen=True, slots=True)
class MatchRequest:
    """One line as typed, decomposed but not yet resolved."""

    raw: str
    line_number: int
    home_text: str = ""
    away_text: str = ""
    competition_text: str = ""
    date: dt.date | None = None
    time: dt.time | None = None
    odds: tuple[float, float, float] | None = None

    @property
    def parsed(self) -> bool:
        return bool(self.home_text and self.away_text)

    def label(self) -> str:
        return f"{self.home_text} – {self.away_text}" if self.parsed else self.raw


@dataclass(frozen=True, slots=True)
class ResolvedMatch:
    """A request after identification, whatever the outcome."""

    request: MatchRequest
    status: RequestStatus
    fixture: Fixture | None = None
    competition_key: str = ""
    competition_label: str = ""
    kickoff: dt.datetime | None = None
    kickoff_status: KickoffStatus = KickoffStatus.UNKNOWN_TIME
    timezone: str = DEFAULT_TIMEZONE
    missing: tuple[str, ...] = ()
    candidates: tuple[str, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def analysable(self) -> bool:
        return self.status.resolved and self.fixture is not None

    @property
    def bettable(self) -> bool:
        return self.analysable and self.kickoff_status.allows_prematch

    def kickoff_local(self) -> str:
        if self.kickoff is None:
            return "horaire non confirmé"
        local = self.kickoff.astimezone(resolve_timezone(self.timezone))
        return local.strftime("%a %d/%m/%Y %H:%M %Z")

    def explain(self) -> str:
        """What exactly is needed, when the match could not be identified."""
        if self.analysable:
            return ""
        parts = [f"{self.status.value}"]
        if self.missing:
            parts.append("manque : " + " ; ".join(self.missing))
        if self.candidates:
            parts.append("candidats : " + ", ".join(self.candidates))
        return " — ".join(parts)


def _extract_odds(text: str) -> tuple[str, tuple[float, float, float] | None]:
    match = _ODDS_PATTERN.search(text)
    if match:
        parsed = parse_odds_line(match.group(1))
        if parsed is not None:
            return (text[: match.start()].strip(), parsed)
    # A bare trailing triple, without the '@' marker.
    fields = [f for f in re.split(r"[|;]", text) if f.strip()]
    if len(fields) >= 2:
        parsed = parse_odds_line(fields[-1])
        if parsed is not None:
            return ("|".join(fields[:-1]).strip(), parsed)
    return (text, None)


def _extract_date(text: str, *, today: dt.date) -> tuple[str, dt.date | None]:
    for pattern, kind in _DATE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        try:
            if kind == "ymd":
                found = dt.date(int(match[1]), int(match[2]), int(match[3]))
            elif kind == "dmy":
                found = dt.date(int(match[3]), int(match[2]), int(match[1]))
            else:  # day/month without a year: choose the next occurrence
                day, month = int(match[1]), int(match[2])
                found = dt.date(today.year, month, day)
                if (found - today).days < -180:
                    found = dt.date(today.year + 1, month, day)
        except ValueError:
            continue
        return (text[: match.start()] + " " + text[match.end() :], found)
    return (text, None)


def _extract_time(text: str) -> tuple[str, dt.time | None]:
    match = _TIME_PATTERN.search(text)
    if not match:
        return (text, None)
    try:
        found = dt.time(int(match[1]), int(match[2]))
    except ValueError:
        return (text, None)
    return (text[: match.start()] + " " + text[match.end() :], found)


def parse_line(raw: str, line_number: int, *, today: dt.date) -> MatchRequest:
    """Decompose one typed line.  Never raises: an unreadable line is reported."""
    text = raw.strip()
    if not text or text.startswith("#"):
        return MatchRequest(raw=raw, line_number=line_number)

    text, odds = _extract_odds(text)
    competition = ""
    if "|" in text:
        fields = [f.strip() for f in text.split("|") if f.strip()]
        if len(fields) >= 2:
            competition, text = fields[0], " ".join(fields[1:])
    elif ":" in text and not _TIME_PATTERN.search(text.split(":")[0] + ":00"):
        head, _, tail = text.partition(":")
        if tail.strip() and len(head.split()) <= 4:
            competition, text = head.strip(), tail.strip()

    text, date = _extract_date(text, today=today)
    text, time = _extract_time(text)

    lowered = f" {text.lower()} "
    for separator in _SEPARATORS:
        if separator in lowered:
            index = lowered.index(separator)
            home = text[: max(index - 1, 0)].strip(" -–—|,")
            away = text[index - 1 + len(separator) :].strip(" -–—|,")
            if home and away:
                return MatchRequest(
                    raw=raw, line_number=line_number, home_text=home, away_text=away,
                    competition_text=competition, date=date, time=time, odds=odds,
                )
    return MatchRequest(
        raw=raw, line_number=line_number, competition_text=competition,
        date=date, time=time, odds=odds,
    )


def parse_requests(text: str, *, today: dt.date | None = None) -> list[MatchRequest]:
    """Parse a block of text into one request per non-empty, non-comment line."""
    reference = today or dt.date.today()
    requests: list[MatchRequest] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip() or line.strip().startswith("#"):
            continue
        requests.append(parse_line(line, number, today=reference))
    return requests


def kickoff_status(
    kickoff: dt.datetime | None, as_of: dt.datetime, *, declared: KickoffStatus | None = None
) -> KickoffStatus:
    """Classify a kickoff against the analysis instant.

    ``declared`` wins when a source states the match is postponed or cancelled:
    a calendar comparison cannot know that.
    """
    if declared in (KickoffStatus.POSTPONED, KickoffStatus.CANCELLED):
        return declared
    if kickoff is None:
        return KickoffStatus.UNKNOWN_TIME
    if as_of < kickoff:
        return KickoffStatus.SCHEDULED
    if as_of < kickoff + _MATCH_DURATION:
        return KickoffStatus.IN_PLAY
    return KickoffStatus.FINISHED


def combine_kickoff(
    date: dt.date, time: dt.time | None, timezone: str
) -> dt.datetime | None:
    """Build an aware kickoff instant from a local date and time."""
    if time is None:
        return None
    return dt.datetime.combine(date, time, tzinfo=resolve_timezone(timezone))


def summarise(resolved: Sequence[ResolvedMatch]) -> str:
    """A one-line census proving every input line is accounted for."""
    total = len(resolved)
    ok = sum(1 for r in resolved if r.analysable)
    bettable = sum(1 for r in resolved if r.bettable)
    return (
        f"{total} ligne(s) saisie(s) — {ok} identifiée(s), "
        f"{total - ok} à préciser, {ok - bettable} hors prématch"
    )
