"""Core domain types for football modelling.

Everything in this module is immutable, hashable, validated on construction and
free of any dependency beyond the standard library.  The types here form the
vocabulary that every other module in :mod:`foot` speaks.
"""

from __future__ import annotations

import bisect
import datetime as dt
import itertools
import math
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import overload

__all__ = [
    "Fixture",
    "Match",
    "MatchLog",
    "Outcome",
    "OutcomeProbabilities",
    "Score",
]


class Outcome(Enum):
    """The three mutually exclusive results of a football match."""

    HOME_WIN = "H"
    DRAW = "D"
    AWAY_WIN = "A"

    @classmethod
    def parse(cls, token: str) -> Outcome:
        """Parse a result token, accepting the usual notations.

        ``H``/``1``/``home`` map to :attr:`HOME_WIN`, ``D``/``X``/``draw`` to
        :attr:`DRAW` and ``A``/``2``/``away`` to :attr:`AWAY_WIN`.
        """
        key = token.strip().upper()
        table = {
            "H": cls.HOME_WIN, "1": cls.HOME_WIN, "HOME": cls.HOME_WIN, "HOME_WIN": cls.HOME_WIN,
            "D": cls.DRAW, "X": cls.DRAW, "DRAW": cls.DRAW, "TIE": cls.DRAW,
            "A": cls.AWAY_WIN, "2": cls.AWAY_WIN, "AWAY": cls.AWAY_WIN, "AWAY_WIN": cls.AWAY_WIN,
        }
        try:
            return table[key]
        except KeyError:
            raise ValueError(f"unrecognised outcome token: {token!r}") from None

    @property
    def index(self) -> int:
        """Position in the canonical ``(home, draw, away)`` ordering."""
        return _OUTCOME_ORDER.index(self)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


_OUTCOME_ORDER: tuple[Outcome, ...] = (Outcome.HOME_WIN, Outcome.DRAW, Outcome.AWAY_WIN)


@dataclass(frozen=True, slots=True, order=True)
class Score:
    """A final scoreline.

    >>> Score(2, 1).outcome
    <Outcome.HOME_WIN: 'H'>
    >>> Score(1, 1).margin
    0
    """

    home: int
    away: int

    def __post_init__(self) -> None:
        for name, value in (("home", self.home), ("away", self.away)):
            if not isinstance(value, int) or isinstance(value, bool):
                raise TypeError(f"{name} goals must be an int, got {type(value).__name__}")
            if value < 0:
                raise ValueError(f"{name} goals must be non-negative, got {value}")

    @property
    def outcome(self) -> Outcome:
        if self.home > self.away:
            return Outcome.HOME_WIN
        if self.home < self.away:
            return Outcome.AWAY_WIN
        return Outcome.DRAW

    @property
    def total(self) -> int:
        """Combined goals scored by both teams."""
        return self.home + self.away

    @property
    def margin(self) -> int:
        """Goal difference from the home team's perspective."""
        return self.home - self.away

    @property
    def both_teams_scored(self) -> bool:
        return self.home > 0 and self.away > 0

    def reversed(self) -> Score:
        """The same scoreline seen from the away team's perspective."""
        return Score(self.away, self.home)

    @classmethod
    def parse(cls, text: str) -> Score:
        """Parse ``"2-1"``, ``"2:1"`` or ``"2 1"`` into a :class:`Score`."""
        cleaned = text.strip().replace(":", "-").replace(" ", "-")
        parts = [p for p in cleaned.split("-") if p]
        if len(parts) != 2:
            raise ValueError(f"cannot parse scoreline: {text!r}")
        try:
            return cls(int(parts[0]), int(parts[1]))
        except ValueError:
            raise ValueError(f"cannot parse scoreline: {text!r}") from None

    def __str__(self) -> str:
        return f"{self.home}-{self.away}"


@dataclass(frozen=True, slots=True)
class Fixture:
    """A scheduled, not-yet-played match."""

    home: str
    away: str
    date: dt.date
    neutral: bool = False
    competition: str | None = None

    def __post_init__(self) -> None:
        if not self.home or not self.away:
            raise ValueError("team names must be non-empty")
        if self.home == self.away:
            raise ValueError(f"a team cannot play itself: {self.home!r}")
        if not isinstance(self.date, dt.date):
            raise TypeError(f"date must be a datetime.date, got {type(self.date).__name__}")

    @property
    def teams(self) -> tuple[str, str]:
        return (self.home, self.away)

    def involves(self, team: str) -> bool:
        return team in (self.home, self.away)

    def played(self, home_goals: int, away_goals: int) -> Match:
        """Attach a result, producing a :class:`Match`."""
        return Match(
            home=self.home,
            away=self.away,
            date=self.date,
            score=Score(home_goals, away_goals),
            neutral=self.neutral,
            competition=self.competition,
        )

    def __str__(self) -> str:
        return f"{self.date.isoformat()}  {self.home} vs {self.away}"


@dataclass(frozen=True, slots=True)
class Match:
    """A played match: a fixture plus its final score."""

    home: str
    away: str
    date: dt.date
    score: Score
    neutral: bool = False
    competition: str | None = None

    def __post_init__(self) -> None:
        # Reuse Fixture's validation so the two types can never diverge.
        Fixture(self.home, self.away, self.date, self.neutral, self.competition)
        if not isinstance(self.score, Score):
            raise TypeError(f"score must be a Score, got {type(self.score).__name__}")

    @property
    def fixture(self) -> Fixture:
        return Fixture(self.home, self.away, self.date, self.neutral, self.competition)

    @property
    def outcome(self) -> Outcome:
        return self.score.outcome

    @property
    def teams(self) -> tuple[str, str]:
        return (self.home, self.away)

    def involves(self, team: str) -> bool:
        return team in (self.home, self.away)

    def goals_for(self, team: str) -> int:
        if team == self.home:
            return self.score.home
        if team == self.away:
            return self.score.away
        raise KeyError(f"{team!r} did not play in {self}")

    def goals_against(self, team: str) -> int:
        if team == self.home:
            return self.score.away
        if team == self.away:
            return self.score.home
        raise KeyError(f"{team!r} did not play in {self}")

    def points_for(self, team: str, *, win: int = 3, draw: int = 1) -> int:
        scored, conceded = self.goals_for(team), self.goals_against(team)
        if scored > conceded:
            return win
        if scored == conceded:
            return draw
        return 0

    def __str__(self) -> str:
        return f"{self.date.isoformat()}  {self.home} {self.score} {self.away}"


class MatchLog(Sequence[Match]):
    """An immutable, chronologically sorted collection of matches.

    ``MatchLog`` is the unit of data every model consumes.  It is sorted at
    construction time, which makes the leak-free time slicing used by
    :mod:`foot.evaluation.backtest` a cheap, obviously correct operation.
    """

    __slots__ = ("_matches", "_teams")

    def __init__(self, matches: Iterable[Match] = ()) -> None:
        items = tuple(matches)
        for m in items:
            if not isinstance(m, Match):
                raise TypeError(f"MatchLog accepts Match objects, got {type(m).__name__}")
        self._matches: tuple[Match, ...] = tuple(sorted(items, key=_chronological_key))
        self._teams: tuple[str, ...] | None = None

    @classmethod
    def _from_sorted(cls, matches: tuple[Match, ...]) -> MatchLog:
        """Wrap an already-sorted tuple, skipping the sort and the validation.

        Private on purpose: it trusts its input, and every caller is a slicing
        method of this class that derives its argument from ``self._matches``.
        """
        log = cls.__new__(cls)
        log._matches = matches
        log._teams = None
        return log

    def _index_of_date(self, cutoff: dt.date) -> int:
        """First position whose match date is not before ``cutoff``."""
        return bisect.bisect_left(_DateView(self._matches), cutoff)

    # -- Sequence protocol -------------------------------------------------
    @overload
    def __getitem__(self, index: int) -> Match: ...
    @overload
    def __getitem__(self, index: slice) -> MatchLog: ...

    def __getitem__(self, index: int | slice) -> Match | MatchLog:
        if isinstance(index, slice):
            return MatchLog._from_sorted(self._matches[index])
        return self._matches[index]

    def __len__(self) -> int:
        return len(self._matches)

    def __iter__(self) -> Iterator[Match]:
        return iter(self._matches)

    def __repr__(self) -> str:
        if not self._matches:
            return "MatchLog(empty)"
        return (
            f"MatchLog({len(self._matches)} matches, {len(self.teams)} teams, "
            f"{self.start.isoformat()}..{self.end.isoformat()})"
        )

    def __add__(self, other: MatchLog) -> MatchLog:
        if not isinstance(other, MatchLog):
            return NotImplemented
        return MatchLog(self._matches + tuple(other))

    def __eq__(self, other: object) -> bool:
        if isinstance(other, MatchLog):
            return self._matches == other._matches
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self._matches)

    # -- Properties --------------------------------------------------------
    @property
    def teams(self) -> tuple[str, ...]:
        """All teams appearing in the log, sorted alphabetically (computed once)."""
        if self._teams is None:
            self._teams = tuple(sorted({t for m in self._matches for t in m.teams}))
        return self._teams

    @property
    def start(self) -> dt.date:
        if not self._matches:
            raise ValueError("empty MatchLog has no start date")
        return self._matches[0].date

    @property
    def end(self) -> dt.date:
        if not self._matches:
            raise ValueError("empty MatchLog has no end date")
        return self._matches[-1].date

    # -- Slicing and filtering --------------------------------------------
    def before(self, cutoff: dt.date, *, inclusive: bool = False) -> MatchLog:
        """Matches played strictly before ``cutoff`` (or up to it if inclusive).

        This is the primitive that makes backtests honest: a model trained on
        ``log.before(kickoff)`` cannot have seen the match it is predicting.
        Because the log is kept sorted, the cut is a binary search.
        """
        edge = cutoff + dt.timedelta(days=1) if inclusive else cutoff
        return MatchLog._from_sorted(self._matches[: self._index_of_date(edge)])

    def after(self, cutoff: dt.date, *, inclusive: bool = True) -> MatchLog:
        edge = cutoff if inclusive else cutoff + dt.timedelta(days=1)
        return MatchLog._from_sorted(self._matches[self._index_of_date(edge) :])

    def between(self, start: dt.date, end: dt.date) -> MatchLog:
        """Matches with ``start <= date <= end``."""
        low = self._index_of_date(start)
        high = self._index_of_date(end + dt.timedelta(days=1))
        return MatchLog._from_sorted(self._matches[low:high])

    def involving(self, team: str) -> MatchLog:
        return MatchLog(m for m in self._matches if m.involves(team))

    def filter(self, predicate: Callable[[Match], bool]) -> MatchLog:
        return MatchLog(m for m in self._matches if predicate(m))

    def last(self, n: int) -> MatchLog:
        """The ``n`` most recent matches."""
        if n < 0:
            raise ValueError("n must be non-negative")
        return MatchLog._from_sorted(self._matches[len(self._matches) - n :] if n else ())

    def dates(self) -> tuple[dt.date, ...]:
        """Distinct match dates, in chronological order."""
        seen: dict[dt.date, None] = {}
        for m in self._matches:
            seen.setdefault(m.date, None)
        return tuple(seen)

    def with_competition(self, competition: str) -> MatchLog:
        return MatchLog(m for m in self._matches if m.competition == competition)

    def seasons(self, *, gap_days: int = 45) -> tuple[MatchLog, ...]:
        """Split the log wherever the calendar goes quiet for ``gap_days``.

        Multi-season files are usually just concatenated, with nothing marking
        the boundary but the summer break.  Splitting on the gap recovers the
        seasons without needing a column that may not exist.

        >>> from foot.data.synthetic import synthetic_league
        >>> log, _ = synthetic_league(seed=1, seasons=3)
        >>> [len(season) for season in log.seasons()]
        [380, 380, 380]
        """
        if gap_days < 1:
            raise ValueError("gap_days must be positive")
        if not self._matches:
            return ()
        boundaries = [0]
        for i in range(1, len(self._matches)):
            gap = (self._matches[i].date - self._matches[i - 1].date).days
            if gap >= gap_days:
                boundaries.append(i)
        boundaries.append(len(self._matches))
        return tuple(
            MatchLog._from_sorted(self._matches[start:end])
            for start, end in itertools.pairwise(boundaries)
        )

    # -- Summary -----------------------------------------------------------
    def goal_stats(self) -> dict[str, float]:
        """Aggregate goal statistics, the first sanity check on any dataset."""
        n = len(self._matches)
        if n == 0:
            raise ValueError("empty MatchLog has no goal statistics")
        home = sum(m.score.home for m in self._matches)
        away = sum(m.score.away for m in self._matches)
        counts = dict.fromkeys(_OUTCOME_ORDER, 0)
        for m in self._matches:
            counts[m.outcome] += 1
        return {
            "matches": float(n),
            "home_goals_per_game": home / n,
            "away_goals_per_game": away / n,
            "goals_per_game": (home + away) / n,
            "home_win_rate": counts[Outcome.HOME_WIN] / n,
            "draw_rate": counts[Outcome.DRAW] / n,
            "away_win_rate": counts[Outcome.AWAY_WIN] / n,
        }


def _chronological_key(match: Match) -> tuple[dt.date, str, str]:
    """Total order on matches: by date, then by team names for determinism."""
    return (match.date, match.home, match.away)


class _DateView(Sequence[dt.date]):
    """A zero-copy view exposing only the dates of a sorted match tuple.

    ``bisect`` gained a ``key`` argument in Python 3.10, but a view keeps the
    intent explicit and costs nothing: no dates are materialised.
    """

    __slots__ = ("_matches",)

    def __init__(self, matches: tuple[Match, ...]) -> None:
        self._matches = matches

    def __len__(self) -> int:
        return len(self._matches)

    @overload
    def __getitem__(self, index: int) -> dt.date: ...
    @overload
    def __getitem__(self, index: slice) -> Sequence[dt.date]: ...

    def __getitem__(self, index: int | slice) -> dt.date | Sequence[dt.date]:
        if isinstance(index, slice):  # pragma: no cover - bisect never slices
            return [m.date for m in self._matches[index]]
        return self._matches[index].date


@dataclass(frozen=True, slots=True)
class OutcomeProbabilities:
    """A calibrated probability distribution over the three match outcomes.

    The constructor validates that the triple is a genuine probability vector,
    which turns a whole class of silent modelling bugs into loud errors.
    """

    home: float
    draw: float
    away: float

    _TOLERANCE = 1e-9

    def __post_init__(self) -> None:
        values = (self.home, self.draw, self.away)
        for name, p in zip(("home", "draw", "away"), values, strict=True):
            if not math.isfinite(p):
                raise ValueError(f"{name} probability must be finite, got {p!r}")
            if p < -self._TOLERANCE or p > 1.0 + self._TOLERANCE:
                raise ValueError(f"{name} probability out of [0, 1]: {p!r}")
        total = math.fsum(values)
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"probabilities must sum to 1, got {total!r}")

    # -- Access ------------------------------------------------------------
    def __getitem__(self, outcome: Outcome) -> float:
        if not isinstance(outcome, Outcome):
            raise TypeError(
                f"index with an Outcome, not {type(outcome).__name__}; "
                f"use as_tuple() for positional access"
            )
        return self.as_tuple()[outcome.index]

    def as_tuple(self) -> tuple[float, float, float]:
        """``(home, draw, away)`` — the canonical ordering used throughout."""
        return (self.home, self.draw, self.away)

    @property
    def most_likely(self) -> Outcome:
        probs = self.as_tuple()
        return _OUTCOME_ORDER[max(range(3), key=probs.__getitem__)]

    def fair_odds(self) -> tuple[float, float, float]:
        """Zero-margin decimal odds, ``inf`` for impossible outcomes."""
        return tuple(  # type: ignore[return-value]
            (1.0 / p if p > 0.0 else math.inf) for p in self.as_tuple()
        )

    def entropy(self, base: float = math.e) -> float:
        """Shannon entropy — how uncertain the forecast is."""
        total = 0.0
        for p in self.as_tuple():
            if p > 0.0:
                total -= p * math.log(p, base)
        return total

    # -- Construction ------------------------------------------------------
    @classmethod
    def normalised(cls, home: float, draw: float, away: float) -> OutcomeProbabilities:
        """Build from non-negative weights, rescaling them to sum to one."""
        values = (home, draw, away)
        for p in values:
            if not math.isfinite(p) or p < 0.0:
                raise ValueError(f"weights must be finite and non-negative, got {values!r}")
        total = math.fsum(values)
        if total <= 0.0:
            raise ValueError("weights must not all be zero")
        return cls(home / total, draw / total, away / total)

    def shrunk_towards(self, other: OutcomeProbabilities, weight: float) -> OutcomeProbabilities:
        """Linear blend: ``(1 - weight) * self + weight * other``."""
        if not 0.0 <= weight <= 1.0:
            raise ValueError(f"weight must lie in [0, 1], got {weight!r}")
        a, b = self.as_tuple(), other.as_tuple()
        return OutcomeProbabilities.normalised(
            *(x * (1.0 - weight) + y * weight for x, y in zip(a, b, strict=True))
        )

    def with_floor(self, floor: float) -> OutcomeProbabilities:
        """Raise every outcome to at least ``floor``, keeping the sum at one.

        Useful before scoring with logarithmic rules, which are unbounded when
        a model assigns probability zero to something that then happens.

        The mass needed to lift the floored outcomes is taken *proportionally
        from the others*, not by rescaling everything — rescaling would push
        the floored outcomes straight back below the floor.  Because each pass
        pins at least one more outcome, three passes always suffice here.

        >>> OutcomeProbabilities(1.0, 0.0, 0.0).with_floor(0.01).as_tuple()
        (0.98, 0.01, 0.01)
        """
        if not 0.0 <= floor < 1.0 / 3.0:
            raise ValueError(f"floor must lie in [0, 1/3), got {floor!r}")
        values = list(self.as_tuple())
        if floor == 0.0 or min(values) >= floor:
            return self
        for _ in range(len(values)):
            pinned = [i for i, p in enumerate(values) if p <= floor]
            free = [i for i in range(len(values)) if i not in pinned]
            free_mass = math.fsum(values[i] for i in free)
            if not free or free_mass <= 0.0:
                values = [1.0 / len(values)] * len(values)
                break
            scale = (1.0 - floor * len(pinned)) / free_mass
            for i in pinned:
                values[i] = floor
            for i in free:
                values[i] = values[i] * scale
            if min(values) >= floor:
                break
        # Push any residual rounding error onto the largest outcome, which is
        # nowhere near the floor, so the guarantee holds exactly.
        largest = max(range(len(values)), key=values.__getitem__)
        values[largest] += 1.0 - math.fsum(values)
        return OutcomeProbabilities(*values)

    def __str__(self) -> str:
        return f"H {self.home:.3f} / D {self.draw:.3f} / A {self.away:.3f}"
