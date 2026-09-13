"""Pricing every offer from one joint distribution of scorelines.

The whole catalogue is settled against the same grid, which is what keeps the
markets mutually consistent: the 1X2, the handicaps, the totals and any
same-match combination are literally different sums over the same numbers.

Three checks the operator asked for are performed here rather than assumed:

* **truncated mass** — how much probability falls outside the grid;
* **Dixon-Coles validity** — that the low-score correction left no negative cell
  for the pair actually being predicted;
* **stale prices** — how old a quote is relative to the analysis instant.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from foot.markets.catalogue import MarketOffer
from foot.markets.settlement import SettlementProfile, unit_return
from foot.models.base import ScoreMatrix
from foot.models.dixon_coles import dixon_coles_tau

__all__ = [
    "GridDiagnostics",
    "PricedOffer",
    "expected_log_growth_at",
    "grid_diagnostics",
    "price_catalogue",
    "price_offer",
]


def price_offer(offer: MarketOffer, matrix: ScoreMatrix) -> SettlementProfile:
    """Settle an offer across every scoreline in the grid.

    Quarter lines are handled by their own unit-result function, which averages
    the two component lines **cell by cell**.  Averaging the two finished
    profiles instead would give the same expected value but the wrong
    distribution: a draw on an Asian ``-0.25`` is one half-loss, not a mixture
    of a push and a full loss, and the two differ in variance — which is what
    log-growth comparisons and stake sizing actually respond to.
    """
    units: dict[float, float] = {}
    for home, row in enumerate(matrix.grid):
        for away, probability in enumerate(row):
            if probability <= 0.0:
                continue
            unit = offer.unit_result(home, away)
            units[unit] = units.get(unit, 0.0) + probability
    return SettlementProfile.from_units(units)


def expected_log_growth_at(
    profile: SettlementProfile, decimal_odds: float, stake: float
) -> float:
    """Expected log wealth multiple for a fixed fractional stake.

    Comparing markets on expected *log* growth at one reference stake is what
    makes a well-paid, moderately likely bet comparable with a near-certain,
    badly-paid one: the logarithm charges for variance, which raw expected value
    does not.  Returns ``-inf`` if the stake could be wiped out.
    """
    if not 0.0 < stake <= 1.0:
        raise ValueError("la mise de référence doit être dans (0, 1]")
    growth = 0.0
    for settlement, probability in profile.probabilities.items():
        if probability <= 0.0:
            continue
        wealth = 1.0 + stake * (unit_return(settlement.unit, decimal_odds) - 1.0)
        if wealth <= 0.0:
            return -math.inf
        growth += probability * math.log(wealth)
    return growth


@dataclass(frozen=True, slots=True)
class GridDiagnostics:
    """Health of the scoreline grid a price was derived from."""

    truncated_mass: float
    max_goals: int
    negative_cells: int
    home_rate: float
    away_rate: float
    rho: float | None = None
    tau_floor: float | None = None

    @property
    def trustworthy(self) -> bool:
        return self.truncated_mass < 1e-6 and self.negative_cells == 0

    def warnings(self) -> tuple[str, ...]:
        issues: list[str] = []
        if self.truncated_mass >= 1e-6:
            issues.append(
                f"masse de probabilité perdue par troncature : "
                f"{self.truncated_mass:.2e} (grille {self.max_goals} buts)"
            )
        if self.negative_cells:
            issues.append(
                f"{self.negative_cells} cases négatives avant renormalisation : "
                f"la correction Dixon-Coles sort de son domaine pour ce couple "
                f"(λ={self.home_rate:.2f}, μ={self.away_rate:.2f}, ρ={self.rho})"
            )
        if self.tau_floor is not None and self.tau_floor <= 0.05:
            issues.append(
                f"correction Dixon-Coles proche de sa borne (τ_min={self.tau_floor:.3f})"
            )
        return tuple(issues)


def grid_diagnostics(
    matrix: ScoreMatrix,
    *,
    home_rate: float,
    away_rate: float,
    rho: float | None = None,
) -> GridDiagnostics:
    """Measure truncation and Dixon-Coles validity for this specific pairing."""
    negative = sum(1 for row in matrix.grid for p in row if p < 0.0)
    tau_floor: float | None = None
    if rho is not None:
        tau_floor = min(
            dixon_coles_tau(h, a, home_rate, away_rate, rho)
            for h in (0, 1)
            for a in (0, 1)
        )
    return GridDiagnostics(
        truncated_mass=matrix.truncated_mass,
        max_goals=matrix.max_goals,
        negative_cells=negative,
        home_rate=home_rate,
        away_rate=away_rate,
        rho=rho,
        tau_floor=tau_floor,
    )


@dataclass(frozen=True, slots=True)
class PricedOffer:
    """An offer, settled against the model, and compared with its price."""

    offer: MarketOffer
    profile: SettlementProfile
    quoted_at: dt.datetime | None = None
    as_of: dt.datetime | None = None

    # -- Model side (always available) ------------------------------------
    @property
    def win_probability(self) -> float:
        return self.profile.win_probability

    @property
    def fair_odds(self) -> float:
        """The price at which this bet breaks even, refunds included."""
        return self.profile.break_even_odds()

    # -- Market side (only with a price) ----------------------------------
    @property
    def odds(self) -> float | None:
        return self.offer.odds

    @property
    def has_price(self) -> bool:
        return self.offer.odds is not None

    @property
    def expected_value(self) -> float | None:
        """Expected profit per unit staked, or ``None`` without a quoted price."""
        if self.offer.odds is None:
            return None
        return self.profile.expected_value(self.offer.odds)

    def log_growth(self, stake: float = 0.02) -> float | None:
        if self.offer.odds is None:
            return None
        return expected_log_growth_at(self.profile, self.offer.odds, stake)

    @property
    def price_age(self) -> dt.timedelta | None:
        if self.quoted_at is None or self.as_of is None:
            return None
        return self.as_of - self.quoted_at

    def is_stale(self, *, limit_hours: float = 12.0) -> bool:
        """A quote older than ``limit_hours`` is treated as unreliable."""
        age = self.price_age
        return age is not None and age.total_seconds() > limit_hours * 3600.0

    def render(self) -> str:
        parts = [f"{self.offer.label:<38}", f"p={self.win_probability * 100:5.1f}%"]
        fair = self.fair_odds
        parts.append(
            f"cote équitable {fair:6.2f}" if math.isfinite(fair) else "cote équitable   —  "
        )
        if self.has_price:
            assert self.offer.odds is not None
            parts.append(f"offerte {self.offer.odds:5.2f}")
            value = self.expected_value
            assert value is not None
            parts.append(f"EV {value * 100:+6.2f}%")
        else:
            parts.append("cote non fournie")
        if self.profile.push_probability > 1e-9:
            parts.append(f"remb. {self.profile.push_probability * 100:.1f}%")
        return "  ".join(parts)


def price_catalogue(
    offers: Iterable[MarketOffer],
    matrix: ScoreMatrix,
    *,
    quotes: Mapping[str, float] | None = None,
    quoted_at: dt.datetime | None = None,
    as_of: dt.datetime | None = None,
    bookmaker: str | None = None,
) -> list[PricedOffer]:
    """Settle every offer and attach any price supplied for it.

    Prices are matched by offer key, so a book that only quotes part of the
    catalogue simply leaves the rest unpriced — reported as such rather than
    dropped.
    """
    quotes = dict(quotes or {})
    priced: list[PricedOffer] = []
    for offer in offers:
        quote = quotes.get(offer.key)
        with_price = offer.with_odds(quote, bookmaker=bookmaker) if quote else offer
        priced.append(
            PricedOffer(
                offer=with_price,
                profile=price_offer(offer, matrix),
                quoted_at=quoted_at if quote else None,
                as_of=as_of,
            )
        )
    return priced


def coherent_market_set(priced: Sequence[PricedOffer]) -> dict[str, list[PricedOffer]]:
    """Group priced offers into coherent sets for de-vigging.

    The margin must be removed from one complete market at one instant — the
    three 1X2 prices together, say.  Picking the best price for each leg across
    several books is a different operation and is never mistaken for this one.
    """
    groups: dict[str, list[PricedOffer]] = {}
    for item in priced:
        if not item.has_price:
            continue
        line = "" if item.offer.line is None else f":{item.offer.line:g}"
        groups.setdefault(f"{item.offer.family.value}{line}", []).append(item)
    return groups


def market_completeness(group: Sequence[PricedOffer]) -> tuple[bool, str]:
    """Whether a group of prices covers its market exhaustively.

    De-vigging an incomplete market silently rescales the wrong total, so the
    caller is told rather than left to find out from a strange number.
    """
    if not group:
        return (False, "aucune cote")
    coverage = math.fsum(item.profile.win_probability for item in group)
    if abs(coverage - 1.0) > 0.02:
        return (
            False,
            f"marché incomplet : les issues cotées couvrent {coverage * 100:.1f}% "
            f"des cas, pas 100%",
        )
    return (True, "marché complet")
