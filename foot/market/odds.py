"""Odds representations and conversions.

The betting market is the strongest publicly available football forecast, so a
modelling library has to be able to read it precisely.  This module handles the
three quoting conventions and the arithmetic of the bookmaker's margin.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from fractions import Fraction

from foot.domain import Outcome, OutcomeProbabilities

__all__ = [
    "MatchOdds",
    "american_to_decimal",
    "decimal_to_american",
    "decimal_to_fractional",
    "fractional_to_decimal",
    "implied_probability",
]


def _check_decimal(odds: float, *, name: str = "odds") -> float:
    if not math.isfinite(odds):
        raise ValueError(f"{name} must be finite, got {odds!r}")
    if odds <= 1.0:
        raise ValueError(f"decimal {name} must exceed 1.0, got {odds!r}")
    return float(odds)


def implied_probability(decimal_odds: float) -> float:
    """The bookmaker's raw implied probability, ``1 / odds``.

    Across a market these do not sum to one: the excess is the margin.
    """
    return 1.0 / _check_decimal(decimal_odds)


def fractional_to_decimal(fractional: str | Fraction) -> float:
    """``"5/2"`` or ``Fraction(5, 2)`` becomes ``3.5``."""
    value = Fraction(fractional) if isinstance(fractional, str) else fractional
    if value <= 0:
        raise ValueError(f"fractional odds must be positive, got {fractional!r}")
    return float(value) + 1.0


def decimal_to_fractional(decimal_odds: float, *, max_denominator: int = 100) -> Fraction:
    """``3.5`` becomes ``Fraction(5, 2)``, rounded to a tradeable denominator."""
    return Fraction(_check_decimal(decimal_odds) - 1.0).limit_denominator(max_denominator)


def american_to_decimal(american: float) -> float:
    """``+150`` becomes ``2.5``; ``-200`` becomes ``1.5``."""
    if not math.isfinite(american) or american == 0.0:
        raise ValueError(f"american odds must be a non-zero finite number, got {american!r}")
    if american > 0.0:
        return 1.0 + american / 100.0
    return 1.0 + 100.0 / abs(american)


def decimal_to_american(decimal_odds: float) -> float:
    """Inverse of :func:`american_to_decimal`."""
    odds = _check_decimal(decimal_odds)
    if odds >= 2.0:
        return (odds - 1.0) * 100.0
    return -100.0 / (odds - 1.0)


@dataclass(frozen=True, slots=True)
class MatchOdds:
    """A three-way (1X2) market quoted in decimal odds."""

    home: float
    draw: float
    away: float
    bookmaker: str | None = None

    def __post_init__(self) -> None:
        for name in ("home", "draw", "away"):
            _check_decimal(getattr(self, name), name=f"{name} odds")

    # -- Construction ------------------------------------------------------
    @classmethod
    def from_american(
        cls, home: float, draw: float, away: float, *, bookmaker: str | None = None
    ) -> MatchOdds:
        return cls(
            american_to_decimal(home),
            american_to_decimal(draw),
            american_to_decimal(away),
            bookmaker,
        )

    @classmethod
    def from_probabilities(
        cls,
        probabilities: OutcomeProbabilities,
        *,
        margin: float = 0.0,
        bookmaker: str | None = None,
    ) -> MatchOdds:
        """Quote a probability vector as odds, optionally adding a margin.

        The margin is applied proportionally, which is the inverse of the
        multiplicative de-vigging in :mod:`foot.market.devig`.
        """
        if margin < 0.0:
            raise ValueError(f"margin must be non-negative, got {margin!r}")
        scale = 1.0 + margin
        values = []
        for p in probabilities.as_tuple():
            if p <= 0.0:
                raise ValueError("cannot quote odds on a zero-probability outcome")
            values.append(1.0 / (p * scale))
        return cls(values[0], values[1], values[2], bookmaker)

    # -- Market arithmetic -------------------------------------------------
    def as_tuple(self) -> tuple[float, float, float]:
        return (self.home, self.draw, self.away)

    def __getitem__(self, outcome: Outcome) -> float:
        return self.as_tuple()[outcome.index]

    def raw_probabilities(self) -> tuple[float, float, float]:
        """The three ``1 / odds`` values; their sum exceeds one by the margin."""
        return (1.0 / self.home, 1.0 / self.draw, 1.0 / self.away)

    @property
    def booksum(self) -> float:
        """Sum of the raw implied probabilities, also called the book percentage."""
        return math.fsum(self.raw_probabilities())

    @property
    def overround(self) -> float:
        """The bookmaker's margin: ``booksum - 1``.

        A typical European 1X2 market runs between 0.02 and 0.08.
        """
        return self.booksum - 1.0

    @property
    def is_arbitrage(self) -> bool:
        """True when the quoted prices allow a risk-free profit."""
        return self.booksum < 1.0

    def payout(self, outcome: Outcome, stake: float = 1.0) -> float:
        """Gross return from a winning stake on ``outcome``."""
        if stake < 0.0:
            raise ValueError("stake must be non-negative")
        return stake * self[outcome]

    def profit(self, outcome: Outcome, bet_on: Outcome, stake: float = 1.0) -> float:
        """Net profit of a stake on ``bet_on`` when ``outcome`` happens."""
        return self.payout(outcome, stake) - stake if outcome is bet_on else -stake

    def __str__(self) -> str:
        book = f" [{self.bookmaker}]" if self.bookmaker else ""
        return (
            f"H {self.home:.2f} / D {self.draw:.2f} / A {self.away:.2f} "
            f"(margin {self.overround * 100:.2f}%){book}"
        )
