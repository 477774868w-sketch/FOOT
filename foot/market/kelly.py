"""Optimal stake sizing under the Kelly criterion.

Kelly, J.L. (1956), *A New Interpretation of Information Rate*, Bell System
Technical Journal 35(4), 917-926.

Kelly staking maximises the expected logarithm of wealth, which is the unique
strategy that maximises long-run growth rate.  For a single bet the answer is
the familiar ``(b p - q) / b``.  For a set of *mutually exclusive* outcomes —
the situation in every football match — the bets interact, because money staked
on the draw is money no longer available if the home side wins, and the
one-bet-at-a-time formula over-stakes badly.

The simultaneous problem

    maximise  sum_i p_i log(1 - S + f_i o_i),    S = sum_i f_i

is concave and has a closed-form solution.  Writing the first-order conditions
and eliminating the multiplier shows that the reserve fraction ``b = 1 - S``
satisfies

    b = (1 - sum_{i in S} p_i) / (1 - sum_{i in S} 1 / o_i),
    f_i = p_i - b / o_i   for i in S,

where ``S`` is the set of outcomes with ``p_i o_i > b`` — found by walking down
the outcomes in decreasing order of ``p_i o_i``.  See Smoczynski, P. and
Tomkins, D. (2010), *An explicit solution to the problem of optimizing the
allocations of a bettor's wealth when wagering on horse races*, Mathematical
Scientist 35, 10-17.

The test suite checks this closed form against a direct numerical maximisation
of the expected log growth, which is the honest way to be sure.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from foot.domain import Outcome, OutcomeProbabilities
from foot.market.odds import MatchOdds

__all__ = [
    "KellyAllocation",
    "NotMutuallyExclusiveError",
    "expected_log_growth",
    "expected_value",
    "kelly_fraction",
    "kelly_portfolio",
]


class NotMutuallyExclusiveError(ValueError):
    """The outcomes passed to a simultaneous-Kelly routine can co-occur.

    Raised instead of a generic message because the mistake it catches — sizing
    overlapping bets as if only one could win — is silent, plausible-looking and
    expensive.
    """


def expected_value(probability: float, decimal_odds: float) -> float:
    """Expected profit per unit staked: ``p * o - 1``.

    Positive means the price is beatable *if* the probability is right.
    """
    if not 0.0 <= probability <= 1.0:
        raise ValueError(f"probability must lie in [0, 1], got {probability!r}")
    if decimal_odds <= 1.0:
        raise ValueError(f"decimal odds must exceed 1, got {decimal_odds!r}")
    return probability * decimal_odds - 1.0


def kelly_fraction(
    probability: float, decimal_odds: float, *, fraction: float = 1.0
) -> float:
    """Optimal stake for a single isolated bet, as a fraction of bankroll.

    ``f* = (p o - 1) / (o - 1)``, floored at zero: a negative Kelly stake means
    "lay the bet", which is a different market and is not silently assumed here.

    Args:
        fraction: Kelly multiplier.  ``0.5`` is the common half-Kelly, which
            gives up a quarter of the growth rate for half the volatility.
    """
    if not 0.0 <= fraction <= 1.0:
        raise ValueError(f"fraction must lie in [0, 1], got {fraction!r}")
    edge = expected_value(probability, decimal_odds)
    if edge <= 0.0:
        return 0.0
    return fraction * edge / (decimal_odds - 1.0)


@dataclass(frozen=True, slots=True)
class KellyAllocation:
    """The optimal simultaneous stakes across a mutually exclusive market."""

    stakes: tuple[float, ...]
    reserve: float
    growth_rate: float
    fraction: float = 1.0

    @property
    def total_staked(self) -> float:
        return math.fsum(self.stakes)

    @property
    def backed(self) -> tuple[int, ...]:
        """Indices actually staked."""
        return tuple(i for i, f in enumerate(self.stakes) if f > 0.0)

    def stake_on(self, outcome: Outcome) -> float:
        """Stake on a 1X2 outcome; only meaningful for three-outcome markets."""
        if len(self.stakes) != 3:
            raise ValueError("stake_on is only defined for a three-way market")
        return self.stakes[outcome.index]

    def __str__(self) -> str:
        parts = ", ".join(f"{f:.4f}" for f in self.stakes)
        return (
            f"stakes=({parts}) total={self.total_staked:.4f} "
            f"growth={self.growth_rate:.6f}/bet"
        )


def expected_log_growth(
    stakes: Sequence[float], probabilities: Sequence[float], odds: Sequence[float]
) -> float:
    """Expected log wealth multiple for a set of simultaneous stakes.

    Returns ``-inf`` if any outcome would bankrupt the bettor.
    """
    if not (len(stakes) == len(probabilities) == len(odds)):
        raise ValueError("stakes, probabilities and odds must have equal length")
    total = math.fsum(stakes)
    growth = 0.0
    for stake, p, o in zip(stakes, probabilities, odds, strict=True):
        wealth = 1.0 - total + stake * o
        if wealth <= 0.0:
            return -math.inf
        if p > 0.0:
            growth += p * math.log(wealth)
    return growth


def kelly_portfolio(
    probabilities: Sequence[float] | OutcomeProbabilities,
    odds: Sequence[float] | MatchOdds,
    *,
    fraction: float = 1.0,
) -> KellyAllocation:
    """Exact simultaneous Kelly stakes for a mutually exclusive market.

    The outcomes must be **mutually exclusive and collectively exhaustive** —
    the three results of one match, not a basket of bets that can win together.
    Passing overlapping selections raises
    :class:`NotMutuallyExclusiveError` rather than silently over-staking.

    Args:
        probabilities: your probabilities; must sum to one.
        odds: decimal odds for the same outcomes, in the same order.
        fraction: Kelly multiplier applied to every stake.

    Returns:
        A :class:`KellyAllocation` whose ``growth_rate`` is the expected log
        growth *actually achieved* by the returned (possibly fractional) stakes,
        not by the full-Kelly optimum.
    """
    if not 0.0 <= fraction <= 1.0:
        raise ValueError(f"fraction must lie in [0, 1], got {fraction!r}")
    probs = list(
        probabilities.as_tuple()
        if isinstance(probabilities, OutcomeProbabilities)
        else probabilities
    )
    prices = list(odds.as_tuple() if isinstance(odds, MatchOdds) else odds)
    if len(probs) != len(prices):
        raise ValueError("probabilities and odds must have equal length")
    if len(probs) < 2:
        raise ValueError("a market needs at least two outcomes")
    for p in probs:
        if not math.isfinite(p) or p < 0.0:
            raise ValueError(f"probabilities must be finite and non-negative, got {p!r}")
    for o in prices:
        if not math.isfinite(o) or o <= 1.0:
            raise ValueError(f"decimal odds must exceed 1, got {o!r}")
    # Mutual exclusivity is a *precondition of the formula*, not a formatting
    # rule.  The closed form below assumes exactly one outcome can occur, so
    # applying it to overlapping bets — "home win" and "over 2.5", say — would
    # over-stake badly.  Summing to one is the strongest check available from
    # the arguments alone, and it is stated as such rather than left implicit:
    # a caller whose probabilities happen to sum to one by coincidence is told
    # here what the function actually requires.
    total = math.fsum(probs)
    if abs(total - 1.0) > 1e-6:
        raise NotMutuallyExclusiveError(
            f"kelly_portfolio requires mutually exclusive, collectively exhaustive "
            f"outcomes whose probabilities sum to 1; got {total!r}. Overlapping bets "
            f"(such as a match result and a totals line on the same game) are not "
            f"a portfolio of exclusive outcomes and must not be sized with this "
            f"function — see foot.markets for same-match combinations."
        )

    # Walk down the outcomes in decreasing order of p*o, adding an outcome only
    # if it beats the reserve implied by the set accepted *so far*.  Testing
    # against the reserve of the enlarged set would be circular and admits
    # outcomes that the first-order conditions reject.
    order = sorted(range(len(probs)), key=lambda i: (-probs[i] * prices[i], i))
    reserve = 1.0
    chosen: list[int] = []
    cumulative_p = 0.0
    cumulative_inverse = 0.0
    for index in order:
        if probs[index] * prices[index] <= reserve:
            break
        chosen.append(index)
        cumulative_p += probs[index]
        cumulative_inverse += 1.0 / prices[index]
        if cumulative_inverse >= 1.0:
            # The accepted prices alone already guarantee a profit, so the
            # growth-optimal play stakes the entire bankroll and holds nothing
            # back.  No further outcome can improve on that.
            reserve = 0.0
            break
        reserve = max(0.0, (1.0 - cumulative_p) / (1.0 - cumulative_inverse))

    stakes = [0.0] * len(probs)
    for index in chosen:
        stakes[index] = max(0.0, fraction * (probs[index] - reserve / prices[index]))
    return KellyAllocation(
        stakes=tuple(stakes),
        reserve=1.0 - math.fsum(stakes),
        growth_rate=expected_log_growth(stakes, probs, prices),
        fraction=fraction,
    )
