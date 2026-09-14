"""Elo ratings adapted to football.

Elo is not a probability model — it is a recursive point estimate of strength,
and its virtue is that it needs no fitting, updates in constant time and starts
producing sensible numbers after a handful of matches.  Two football-specific
questions have to be answered before it is usable, and this module answers both
explicitly rather than by folklore:

**How should the margin of victory count?**  A 5-0 win says more than a 1-0, but
naively scaling by the margin inflates strong teams beating weak ones.  Two
published rules are offered — see :class:`MarginRule`.

**Where do draws come from?**  Elo yields a single expected score in ``[0, 1]``,
not a three-way split.  The classical answer is Davidson's (1970) extension of
the Bradley-Terry model, which introduces one tie parameter ``nu`` while
preserving the win/loss odds ratio exactly.  It is a proper distribution, has a
single interpretable parameter, and that parameter can be estimated by maximum
likelihood from the same match log (:meth:`EloTable.fit_draw_parameter`).
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum

from foot.domain import Fixture, Match, MatchLog, Outcome, OutcomeProbabilities
from foot.numerics.optimize import minimize

__all__ = ["EloRatingSystem", "EloSnapshot", "EloTable", "MarginRule"]


class MarginRule(Enum):
    """How the goal margin scales the rating update."""

    NONE = "none"
    """Ignore the margin: a 1-0 and a 5-0 move the ratings identically."""

    GOAL_DIFFERENCE = "goal_difference"
    """The World Football Elo Ratings index: ``1``, ``1.5``, then ``(11+d)/8``."""

    LOGARITHMIC = "logarithmic"
    """FiveThirtyEight's rule: ``ln(d+1)`` damped by the favourite's edge, which
    removes the autocorrelation that otherwise inflates dominant teams."""


def _margin_multiplier(rule: MarginRule, margin: int, rating_edge: float) -> float:
    """Scale factor applied to ``K`` for a result won by ``margin`` goals.

    Args:
        margin: absolute goal difference.
        rating_edge: the winner's pre-match rating advantage, including home
            advantage.  Only the logarithmic rule uses it.
    """
    size = abs(margin)
    if rule is MarginRule.NONE or size <= 1:
        return 1.0
    if rule is MarginRule.GOAL_DIFFERENCE:
        return 1.5 if size == 2 else (11.0 + size) / 8.0
    return math.log(size + 1.0) * (2.2 / (0.001 * max(rating_edge, 0.0) + 2.2))


@dataclass(frozen=True, slots=True)
class EloSnapshot:
    """One rating update, retained so that a rating path can be audited."""

    date: dt.date
    home: str
    away: str
    home_before: float
    away_before: float
    home_after: float
    away_after: float
    expected_home_score: float
    actual_home_score: float

    @property
    def change(self) -> float:
        """Points transferred from away to home (negative if the away side gained)."""
        return self.home_after - self.home_before


class EloTable:
    """Ratings produced by an :class:`EloRatingSystem`, plus their projection."""

    __slots__ = ("_history", "_played", "_ratings", "_system")

    def __init__(
        self,
        system: EloRatingSystem,
        ratings: Mapping[str, float],
        played: Mapping[str, int],
        history: Sequence[EloSnapshot] = (),
    ) -> None:
        self._system = system
        self._ratings = dict(ratings)
        self._played = dict(played)
        self._history = tuple(history)

    # -- Access ------------------------------------------------------------
    @property
    def system(self) -> EloRatingSystem:
        return self._system

    @property
    def teams(self) -> tuple[str, ...]:
        return tuple(sorted(self._ratings))

    @property
    def history(self) -> tuple[EloSnapshot, ...]:
        return self._history

    def rating(self, team: str) -> float:
        """Current rating, falling back to the configured initial rating."""
        return self._ratings.get(team, self._system.initial_rating)

    def matches_played(self, team: str) -> int:
        return self._played.get(team, 0)

    def ranking(self) -> list[tuple[str, float]]:
        return sorted(self._ratings.items(), key=lambda kv: (-kv[1], kv[0]))

    def __contains__(self, team: str) -> bool:
        return team in self._ratings

    def __repr__(self) -> str:
        return f"EloTable({len(self._ratings)} teams, {len(self._history)} updates)"

    # -- Projection --------------------------------------------------------
    def rating_difference(self, home: str, away: str, *, neutral: bool = False) -> float:
        """Home rating advantage in Elo points, including home advantage."""
        edge = 0.0 if neutral else self._system.home_advantage
        return self.rating(home) + edge - self.rating(away)

    def expected_score(self, home: str, away: str, *, neutral: bool = False) -> float:
        """Elo's expected score for the home team, a draw counting as a half."""
        return _logistic(self.rating_difference(home, away, neutral=neutral))

    def predict(self, fixture: Fixture) -> OutcomeProbabilities:
        """Three-way probabilities via Davidson's tie model."""
        return self.outcome_probabilities(
            fixture.home, fixture.away, neutral=fixture.neutral
        )

    def outcome_probabilities(
        self, home: str, away: str, *, neutral: bool = False, draw_parameter: float | None = None
    ) -> OutcomeProbabilities:
        """Davidson (1970) three-way split of an Elo rating difference.

        With ``x = 10 ** (rating difference / 400)`` and tie parameter ``nu``::

            P(home) = x / (x + 1 + nu * sqrt(x))
            P(draw) = nu * sqrt(x) / (x + 1 + nu * sqrt(x))
            P(away) = 1 / (x + 1 + nu * sqrt(x))

        The win/loss odds ratio stays exactly ``x``, so the draw parameter
        redistributes probability without disturbing Elo's core claim.
        """
        nu = self._system.draw_parameter if draw_parameter is None else draw_parameter
        if nu < 0.0:
            raise ValueError(f"draw parameter must be non-negative, got {nu!r}")
        difference = self.rating_difference(home, away, neutral=neutral)
        return _davidson(difference, nu)

    def fit_draw_parameter(self, matches: MatchLog) -> float:
        """Maximum-likelihood tie parameter for these ratings on ``matches``.

        Optimised over ``log(nu)`` so that positivity is automatic.
        """
        if len(matches) == 0:
            raise ValueError("cannot fit the draw parameter on an empty MatchLog")
        differences = [
            self.rating_difference(m.home, m.away, neutral=m.neutral) for m in matches
        ]
        outcomes = [m.outcome for m in matches]

        def negative_log_likelihood(vector: Sequence[float]) -> float:
            nu = math.exp(vector[0])
            total = 0.0
            for difference, outcome in zip(differences, outcomes, strict=True):
                probabilities = _davidson(difference, nu)
                total -= math.log(max(probabilities[outcome], 1e-300))
            return total

        result = minimize(negative_log_likelihood, [math.log(0.75)], gtol=1e-8)
        return math.exp(result.x[0])


def _logistic(rating_difference: float) -> float:
    return 1.0 / (1.0 + math.pow(10.0, -rating_difference / 400.0))


def _davidson(rating_difference: float, nu: float) -> OutcomeProbabilities:
    # Work with the half-difference to get sqrt(x) without a second exponential,
    # and cap the exponent so that extreme mismatches cannot overflow.
    half = max(-150.0, min(150.0, rating_difference / 800.0))
    root = math.pow(10.0, half)
    x = root * root
    denominator = x + 1.0 + nu * root
    return OutcomeProbabilities(x / denominator, nu * root / denominator, 1.0 / denominator)


@dataclass(frozen=True, slots=True)
class EloRatingSystem:
    """Configuration of an Elo rating scheme.

    Args:
        k_factor: how much a single result moves the ratings.  20 is a common
            choice for club football; higher reacts faster and is noisier.
        home_advantage: home edge expressed in rating points.  Roughly 60
            points corresponds to the long-run home win rate of European
            league football.
        initial_rating: the rating given to a team on first appearance.
        margin_rule: how the goal margin scales the update.
        draw_parameter: Davidson's ``nu``; ``0.75`` puts the draw rate at
            parity near 27 percent.
        season_regression: fraction of the gap to the league mean given back
            whenever a break longer than ``season_gap_days`` occurs.  538 uses
            one third between seasons.
        season_gap_days: how long a break has to be to count as a new season.
    """

    k_factor: float = 20.0
    home_advantage: float = 60.0
    initial_rating: float = 1500.0
    margin_rule: MarginRule = MarginRule.GOAL_DIFFERENCE
    draw_parameter: float = 0.75
    season_regression: float = 0.0
    season_gap_days: int = 45

    def __post_init__(self) -> None:
        if self.k_factor <= 0.0:
            raise ValueError(f"k_factor must be positive, got {self.k_factor!r}")
        if not 0.0 <= self.season_regression <= 1.0:
            raise ValueError(
                f"season_regression must lie in [0, 1], got {self.season_regression!r}"
            )
        if self.draw_parameter < 0.0:
            raise ValueError(f"draw_parameter must be non-negative, got {self.draw_parameter!r}")
        if self.season_gap_days < 1:
            raise ValueError("season_gap_days must be positive")

    def rate(self, matches: Iterable[Match], *, track_history: bool = False) -> EloTable:
        """Run the ratings forward through ``matches`` in chronological order.

        The input is sorted defensively: Elo is path-dependent, so feeding it
        out-of-order matches silently produces different — and wrong — numbers.
        """
        ordered = matches if isinstance(matches, MatchLog) else MatchLog(matches)
        ratings: dict[str, float] = {}
        played: dict[str, int] = {}
        history: list[EloSnapshot] = []
        previous_date: dt.date | None = None

        for match in ordered:
            if (
                self.season_regression > 0.0
                and previous_date is not None
                and (match.date - previous_date).days >= self.season_gap_days
            ):
                _regress_to_mean(ratings, self.season_regression, self.initial_rating)
            previous_date = match.date

            home_before = ratings.setdefault(match.home, self.initial_rating)
            away_before = ratings.setdefault(match.away, self.initial_rating)
            difference = home_before + (0.0 if match.neutral else self.home_advantage) - away_before
            expected = _logistic(difference)
            actual = _actual_score(match.outcome)

            winner_edge = difference if actual > 0.5 else (-difference if actual < 0.5 else 0.0)
            multiplier = _margin_multiplier(self.margin_rule, match.score.margin, winner_edge)
            delta = self.k_factor * multiplier * (actual - expected)

            ratings[match.home] = home_before + delta
            ratings[match.away] = away_before - delta
            played[match.home] = played.get(match.home, 0) + 1
            played[match.away] = played.get(match.away, 0) + 1

            if track_history:
                history.append(
                    EloSnapshot(
                        date=match.date,
                        home=match.home,
                        away=match.away,
                        home_before=home_before,
                        away_before=away_before,
                        home_after=ratings[match.home],
                        away_after=ratings[match.away],
                        expected_home_score=expected,
                        actual_home_score=actual,
                    )
                )

        return EloTable(self, ratings, played, history)


def _actual_score(outcome: Outcome) -> float:
    if outcome is Outcome.HOME_WIN:
        return 1.0
    if outcome is Outcome.DRAW:
        return 0.5
    return 0.0


def _regress_to_mean(ratings: dict[str, float], fraction: float, fallback: float) -> None:
    """Pull every rating a fraction of the way back to the current mean."""
    if not ratings:
        return
    mean = math.fsum(ratings.values()) / len(ratings) if ratings else fallback
    for team, rating in ratings.items():
        ratings[team] = rating + fraction * (mean - rating)
