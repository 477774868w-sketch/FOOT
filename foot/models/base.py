"""Score distributions and the contract every match model satisfies.

A football forecast is richer than "home / draw / away": the natural object is
the joint distribution over scorelines, from which every market — 1X2, totals,
both-teams-to-score, Asian handicaps, correct score — follows by summation.
:class:`ScoreMatrix` is that object, and it is what models return.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from foot.domain import Fixture, MatchLog, Outcome, OutcomeProbabilities, Score
from foot.numerics.stable import poisson_pmf

__all__ = [
    "HandicapSettlement",
    "MatchModel",
    "ScoreMatrix",
    "TotalsSettlement",
]


@dataclass(frozen=True, slots=True)
class TotalsSettlement:
    """Expected settlement of an over/under bet: three fractions summing to one."""

    over: float
    push: float
    under: float

    def __str__(self) -> str:
        return f"over {self.over:.3f} / push {self.push:.3f} / under {self.under:.3f}"


@dataclass(frozen=True, slots=True)
class HandicapSettlement:
    """Expected settlement of an Asian handicap, from the home team's side."""

    home: float
    push: float
    away: float

    def __str__(self) -> str:
        return f"home {self.home:.3f} / push {self.push:.3f} / away {self.away:.3f}"


@dataclass(frozen=True, slots=True)
class ScoreMatrix:
    """Joint probability distribution over scorelines ``(home, away)``.

    ``grid[h][a]`` is ``P(home scores h and away scores a)``.  The grid is
    finite, so a little probability mass always falls outside it; that mass is
    reported as :attr:`truncated_mass` and the retained grid is renormalised so
    that every derived market is a genuine probability.
    """

    grid: tuple[tuple[float, ...], ...]
    truncated_mass: float = 0.0

    def __post_init__(self) -> None:
        if not self.grid or not self.grid[0]:
            raise ValueError("score grid must be non-empty")
        width = len(self.grid[0])
        for row in self.grid:
            if len(row) != width:
                raise ValueError("score grid must be rectangular")
            for p in row:
                if not math.isfinite(p) or p < 0.0:
                    raise ValueError(f"score probabilities must be finite and non-negative: {p!r}")
        total = math.fsum(p for row in self.grid for p in row)
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"score grid must sum to 1, got {total!r}")

    # -- Construction ------------------------------------------------------
    @staticmethod
    def _auto_max_goals(home_rate: float, away_rate: float) -> int:
        """Grid size whose truncated tail is below ~1e-12 for the given rates."""
        peak = max(home_rate, away_rate, 0.0)
        return max(10, math.ceil(peak + 10.0 * math.sqrt(peak) + 6.0))

    @classmethod
    def from_rates(
        cls,
        home_rate: float,
        away_rate: float,
        *,
        max_goals: int | None = None,
        correction: Callable[[int, int, float, float], float] | None = None,
    ) -> ScoreMatrix:
        """Build from independent Poisson rates, optionally perturbed.

        Args:
            home_rate: expected goals for the home team.
            away_rate: expected goals for the away team.
            max_goals: largest scoreline retained per team; chosen automatically
                when omitted so that the truncated tail is negligible.
            correction: optional ``f(home_goals, away_goals, home_rate,
                away_rate)`` multiplier, used by Dixon-Coles to couple the two
                marginals at low scores.
        """
        for name, rate in (("home_rate", home_rate), ("away_rate", away_rate)):
            if not math.isfinite(rate) or rate < 0.0:
                raise ValueError(f"{name} must be finite and non-negative, got {rate!r}")
        limit = max_goals if max_goals is not None else cls._auto_max_goals(home_rate, away_rate)
        if limit < 1:
            raise ValueError("max_goals must be at least 1")

        home_pmf = [poisson_pmf(k, home_rate) for k in range(limit + 1)]
        away_pmf = [poisson_pmf(k, away_rate) for k in range(limit + 1)]
        raw: list[list[float]] = []
        for h in range(limit + 1):
            row = [home_pmf[h] * away_pmf[a] for a in range(limit + 1)]
            if correction is not None:
                row = [
                    row[a] * correction(h, a, home_rate, away_rate) for a in range(limit + 1)
                ]
                for p in row:
                    if p < 0.0:
                        raise ValueError("correction produced a negative probability")
            raw.append(row)

        retained = math.fsum(p for row in raw for p in row)
        if retained <= 0.0:
            raise ValueError("score grid has no probability mass")
        scale = 1.0 / retained
        grid = tuple(tuple(p * scale for p in row) for row in raw)
        return cls(grid=grid, truncated_mass=max(0.0, 1.0 - retained))

    # -- Basic access ------------------------------------------------------
    @property
    def max_goals(self) -> int:
        return len(self.grid) - 1

    def probability(self, home_goals: int, away_goals: int) -> float:
        """``P(score = home_goals-away_goals)``; zero outside the grid."""
        if not (0 <= home_goals <= self.max_goals and 0 <= away_goals <= self.max_goals):
            return 0.0
        return self.grid[home_goals][away_goals]

    def __getitem__(self, score: Score | tuple[int, int]) -> float:
        if isinstance(score, Score):
            return self.probability(score.home, score.away)
        return self.probability(*score)

    # -- Derived markets ---------------------------------------------------
    def outcome_probabilities(self) -> OutcomeProbabilities:
        """Collapse the grid onto the 1X2 market."""
        home = draw = away = 0.0
        for h, row in enumerate(self.grid):
            for a, p in enumerate(row):
                if h > a:
                    home += p
                elif h == a:
                    draw += p
                else:
                    away += p
        return OutcomeProbabilities.normalised(home, draw, away)

    def margin_distribution(self) -> dict[int, float]:
        """``P(home goals - away goals = m)`` for every attainable margin."""
        margins: dict[int, float] = {}
        for h, row in enumerate(self.grid):
            for a, p in enumerate(row):
                if p:
                    margins[h - a] = margins.get(h - a, 0.0) + p
        return dict(sorted(margins.items()))

    def total_goals_distribution(self) -> tuple[float, ...]:
        """``P(total goals = n)`` for ``n = 0 .. 2 * max_goals``."""
        totals = [0.0] * (2 * self.max_goals + 1)
        for h, row in enumerate(self.grid):
            for a, p in enumerate(row):
                totals[h + a] += p
        return tuple(totals)

    def totals(self, line: float) -> TotalsSettlement:
        """Over/under settlement for ``line`` (integer lines can push).

        Quarter lines (``2.25``) are split evenly between the two neighbouring
        half/whole lines, exactly as an Asian bookmaker settles them.
        """
        if not math.isfinite(line) or line < 0.0:
            raise ValueError(f"line must be finite and non-negative, got {line!r}")
        if _is_quarter(line):
            return _combine_totals(self.totals(line - 0.25), self.totals(line + 0.25))
        distribution = self.total_goals_distribution()
        over = push = under = 0.0
        for total, p in enumerate(distribution):
            if total > line:
                over += p
            elif total < line:
                under += p
            else:
                push += p
        return TotalsSettlement(over=over, push=push, under=under)

    def asian_handicap(self, line: float) -> HandicapSettlement:
        """Asian handicap settlement, ``line`` added to the home team's goals.

        ``line = -0.5`` means "home must win"; ``line = +1.0`` gives the home
        team a one-goal start and pushes if they lose by exactly one.
        """
        if not math.isfinite(line):
            raise ValueError(f"line must be finite, got {line!r}")
        if _is_quarter(line):
            return _combine_handicaps(
                self.asian_handicap(line - 0.25), self.asian_handicap(line + 0.25)
            )
        home = push = away = 0.0
        for margin, p in self.margin_distribution().items():
            adjusted = margin + line
            if adjusted > 0:
                home += p
            elif adjusted < 0:
                away += p
            else:
                push += p
        return HandicapSettlement(home=home, push=push, away=away)

    def both_teams_to_score(self) -> float:
        return math.fsum(
            p for h, row in enumerate(self.grid) for a, p in enumerate(row) if h > 0 and a > 0
        )

    def clean_sheet_probabilities(self) -> tuple[float, float]:
        """``(P(home keeps a clean sheet), P(away keeps a clean sheet))``."""
        home_clean = math.fsum(row[0] for row in self.grid)
        away_clean = math.fsum(self.grid[0])
        return (home_clean, away_clean)

    def expected_goals(self) -> tuple[float, float]:
        """Grid-implied expected goals for each team."""
        home = math.fsum(h * p for h, row in enumerate(self.grid) for p in row)
        away = math.fsum(a * p for row in self.grid for a, p in enumerate(row))
        return (home, away)

    def top_scorelines(self, count: int = 5) -> list[tuple[Score, float]]:
        """The ``count`` most likely scorelines, most likely first."""
        if count < 1:
            raise ValueError("count must be positive")
        entries = [
            (Score(h, a), p) for h, row in enumerate(self.grid) for a, p in enumerate(row) if p > 0
        ]
        entries.sort(key=lambda item: (-item[1], item[0].home, item[0].away))
        return entries[:count]

    def most_likely_score(self) -> tuple[Score, float]:
        return self.top_scorelines(1)[0]

    def outcome_of(self, outcome: Outcome) -> float:
        return self.outcome_probabilities()[outcome]


def _is_quarter(line: float) -> bool:
    """True for lines such as 0.25 or -1.75 that split across two lines."""
    return abs(line * 4.0 - round(line * 4.0)) < 1e-9 and abs(line * 2.0 - round(line * 2.0)) > 1e-9


def _combine_totals(a: TotalsSettlement, b: TotalsSettlement) -> TotalsSettlement:
    return TotalsSettlement(
        over=0.5 * (a.over + b.over),
        push=0.5 * (a.push + b.push),
        under=0.5 * (a.under + b.under),
    )


def _combine_handicaps(a: HandicapSettlement, b: HandicapSettlement) -> HandicapSettlement:
    return HandicapSettlement(
        home=0.5 * (a.home + b.home),
        push=0.5 * (a.push + b.push),
        away=0.5 * (a.away + b.away),
    )


@runtime_checkable
class MatchModel(Protocol):
    """The contract shared by every forecasting model in :mod:`foot`.

    Implementations must be *pure* with respect to the fixture: calling
    :meth:`score_matrix` must never mutate state, so a fitted model can be
    reused across a whole backtest without risk of leakage.
    """

    @property
    def teams(self) -> Sequence[str]:
        """Teams the model can produce forecasts for."""

    def fit(self, matches: MatchLog) -> MatchModel:
        """Estimate parameters from ``matches`` and return the fitted model."""

    def score_matrix(self, fixture: Fixture) -> ScoreMatrix:
        """Joint scoreline distribution for ``fixture``."""

    def predict(self, fixture: Fixture) -> OutcomeProbabilities:
        """1X2 probabilities for ``fixture``."""
