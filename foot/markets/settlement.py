"""Exact settlement arithmetic, including pushes and half-stakes.

``p × cote − 1`` is the expected value of a *binary* bet with no refund.  Asian
handicaps and whole-number totals are not binary: they push, and quarter lines
split a stake across two adjacent lines so that half of it can win while the
other half is returned.  Using the binary formula on those markets misprices
them, sometimes badly, so this module never uses it.

Every outcome is reduced to a **unit result** ``u``:

======  ==================  =====================================
``u``   meaning             return on a unit stake at odds ``o``
======  ==================  =====================================
``+1``  win                 ``o``
``+.5`` half win            ``(o + 1) / 2``
``0``   push                ``1``
``-.5`` half loss           ``0.5``
``-1``  loss                ``0``
======  ==================  =====================================

which makes a quarter line exactly the average of its two neighbours: a draw on
an Asian ``-0.25`` averages a push (``0``) and a loss (``-1``) into a half loss
(``-0.5``).  Expected value is then a sum over the joint distribution of
scorelines, never a product of marginals.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum

__all__ = ["Settlement", "SettlementProfile", "unit_return"]


class Settlement(Enum):
    """The five ways a football bet can be settled."""

    WIN = 1.0
    HALF_WIN = 0.5
    PUSH = 0.0
    HALF_LOSS = -0.5
    LOSS = -1.0

    @property
    def unit(self) -> float:
        return float(self.value)

    @property
    def label(self) -> str:
        return {
            Settlement.WIN: "gagné",
            Settlement.HALF_WIN: "demi-gain",
            Settlement.PUSH: "remboursé",
            Settlement.HALF_LOSS: "demi-perte",
            Settlement.LOSS: "perdu",
        }[self]

    @classmethod
    def from_unit(cls, unit: float) -> Settlement:
        """Snap a numeric unit result onto the nearest settlement class."""
        best = min(cls, key=lambda s: abs(s.unit - unit))
        if abs(best.unit - unit) > 1e-9:
            raise ValueError(f"résultat unitaire non standard : {unit!r}")
        return best


def unit_return(unit: float, decimal_odds: float) -> float:
    """Return on a unit stake, including the stake itself.

    >>> unit_return(1.0, 2.5)      # full win
    2.5
    >>> unit_return(0.5, 2.5)      # half win, half returned
    1.75
    >>> unit_return(-0.5, 2.5)     # half loss
    0.5
    """
    if decimal_odds <= 1.0:
        raise ValueError(f"cote décimale invalide : {decimal_odds!r}")
    if unit > 0.0:
        return 1.0 + unit * (decimal_odds - 1.0)
    return 1.0 + unit


@dataclass(frozen=True, slots=True)
class SettlementProfile:
    """The distribution of settlements for one offer, from the joint scoreline law."""

    probabilities: Mapping[Settlement, float]

    def __post_init__(self) -> None:
        total = math.fsum(self.probabilities.values())
        if abs(total - 1.0) > 1e-8:
            raise ValueError(f"le profil de règlement doit sommer à 1, obtenu {total!r}")
        for settlement, p in self.probabilities.items():
            if not math.isfinite(p) or p < -1e-12:
                raise ValueError(f"probabilité invalide pour {settlement}: {p!r}")

    def probability(self, settlement: Settlement) -> float:
        return self.probabilities.get(settlement, 0.0)

    @property
    def win_probability(self) -> float:
        """Probability the bet returns more than the stake (full or half win)."""
        return self.probability(Settlement.WIN) + self.probability(Settlement.HALF_WIN)

    @property
    def push_probability(self) -> float:
        return self.probability(Settlement.PUSH)

    @property
    def loss_probability(self) -> float:
        return self.probability(Settlement.LOSS) + self.probability(Settlement.HALF_LOSS)

    @property
    def full_win_probability(self) -> float:
        return self.probability(Settlement.WIN)

    @property
    def expected_unit(self) -> float:
        """Mean unit result — the fraction of the stake at genuine risk."""
        return math.fsum(s.unit * p for s, p in self.probabilities.items())

    def expected_return(self, decimal_odds: float) -> float:
        """Expected return per unit staked, stake included."""
        return math.fsum(
            p * unit_return(s.unit, decimal_odds) for s, p in self.probabilities.items()
        )

    def expected_value(self, decimal_odds: float) -> float:
        """Expected **profit** per unit staked.

        Reduces to ``p × cote − 1`` for a binary market and stays correct for
        one that can push or half-settle, which the binary formula does not.
        """
        return self.expected_return(decimal_odds) - 1.0

    def break_even_odds(self) -> float:
        """The price at which expected value turns positive.

        ``inf`` when the bet cannot win, since no price makes it worth taking.
        """
        win = self.probability(Settlement.WIN)
        half_win = self.probability(Settlement.HALF_WIN)
        loss = self.probability(Settlement.LOSS)
        half_loss = self.probability(Settlement.HALF_LOSS)
        slope = win + 0.5 * half_win  # d(return)/d(odds)
        if slope <= 0.0:
            return math.inf
        # return = 1 + slope*(o-1) - loss - 0.5*half_loss  ... solve return == 1
        deficit = loss + 0.5 * half_loss
        return 1.0 + deficit / slope

    def risked_fraction(self) -> float:
        """Share of the stake genuinely exposed, after refunds."""
        return (
            self.probability(Settlement.LOSS)
            + 0.5 * self.probability(Settlement.HALF_LOSS)
            + self.probability(Settlement.WIN)
            + 0.5 * self.probability(Settlement.HALF_WIN)
        )

    def render(self) -> str:
        parts = [
            f"{s.label} {self.probability(s) * 100:.1f}%"
            for s in Settlement
            if self.probability(s) > 1e-9
        ]
        return " / ".join(parts)

    @classmethod
    def from_units(cls, weights: Mapping[float, float]) -> SettlementProfile:
        """Build from ``{unit result: probability}``."""
        collected: dict[Settlement, float] = {}
        for unit, p in weights.items():
            if p <= 0.0:
                continue
            settlement = Settlement.from_unit(unit)
            collected[settlement] = collected.get(settlement, 0.0) + p
        if not collected:
            raise ValueError("profil de règlement vide")
        total = math.fsum(collected.values())
        return cls({s: p / total for s, p in collected.items()})

    @classmethod
    def binary(cls, win_probability: float) -> SettlementProfile:
        """A plain win/lose market — the only case ``p × cote − 1`` fits."""
        if not 0.0 <= win_probability <= 1.0:
            raise ValueError(f"probabilité hors [0, 1] : {win_probability!r}")
        return cls({Settlement.WIN: win_probability, Settlement.LOSS: 1.0 - win_probability})

    def blended_with(self, other: SettlementProfile, weight: float = 0.5) -> SettlementProfile:
        """Average two profiles — how a quarter line splits across two lines."""
        if not 0.0 <= weight <= 1.0:
            raise ValueError("le poids doit être dans [0, 1]")
        units: dict[float, float] = {}
        for settlement, p in self.probabilities.items():
            units[settlement.unit] = units.get(settlement.unit, 0.0) + (1.0 - weight) * p
        for settlement, p in other.probabilities.items():
            units[settlement.unit] = units.get(settlement.unit, 0.0) + weight * p
        return SettlementProfile.from_units(units)
