"""Turning forecasts into stakes, and stakes into a bankroll curve.

A better RPS is not money.  This module closes the loop: it takes backtest
records, prices them against a book, sizes bets by the Kelly criterion and
reports what would have happened — including the parts people leave out, such
as the worst drawdown along the way.

Two disciplines are enforced rather than offered:

* **Bets are only placed where the edge survives the margin.**  The comparison
  is against the *de-vigged* market price, so a 2 percent "edge" that is really
  the bookmaker's cut cannot be counted as one.
* **Stakes compound on the actual bankroll.**  Reporting a flat-stake profit
  while claiming Kelly sizing is the commonest way backtests flatter
  themselves.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from foot.domain import Fixture, Outcome
from foot.evaluation.backtest import BacktestRecord
from foot.market.devig import DevigMethod, fair_probabilities
from foot.market.kelly import kelly_portfolio
from foot.market.odds import MatchOdds

__all__ = ["Bet", "BettingResult", "simulate_betting"]


@dataclass(frozen=True, slots=True)
class Bet:
    """A single settled wager."""

    fixture: Fixture
    outcome: Outcome
    decimal_odds: float
    stake: float
    model_probability: float
    market_probability: float
    won: bool
    profit: float
    bankroll_after: float

    @property
    def edge(self) -> float:
        """Expected profit per unit staked at the model's probability."""
        return self.model_probability * self.decimal_odds - 1.0

    @property
    def market_edge(self) -> float:
        """How far the model disagrees with the de-vigged market price."""
        return self.model_probability - self.market_probability

    def __str__(self) -> str:
        return (
            f"{self.fixture.date.isoformat()} {self.fixture.home} v {self.fixture.away} "
            f"[{self.outcome.value}] @{self.decimal_odds:.2f} stake={self.stake:.4f} "
            f"edge={self.edge:+.3f} pnl={self.profit:+.4f}"
        )


@dataclass(frozen=True, slots=True)
class BettingResult:
    """Outcome of a staking simulation."""

    bets: tuple[Bet, ...]
    bankroll_curve: tuple[float, ...]
    starting_bankroll: float
    final_bankroll: float
    turnover: float
    kelly_fraction: float
    minimum_edge: float

    @property
    def profit(self) -> float:
        return self.final_bankroll - self.starting_bankroll

    @property
    def roi(self) -> float:
        """Return on the starting bankroll."""
        return self.profit / self.starting_bankroll

    @property
    def yield_on_turnover(self) -> float:
        """Profit per unit staked — the number the betting world quotes."""
        return self.profit / self.turnover if self.turnover > 0.0 else 0.0

    @property
    def hit_rate(self) -> float:
        if not self.bets:
            return 0.0
        return sum(1 for bet in self.bets if bet.won) / len(self.bets)

    @property
    def mean_edge(self) -> float:
        if not self.bets:
            return 0.0
        return math.fsum(bet.edge for bet in self.bets) / len(self.bets)

    @property
    def max_drawdown(self) -> float:
        """Largest peak-to-trough fall of the bankroll, as a fraction."""
        peak = self.starting_bankroll
        worst = 0.0
        for value in self.bankroll_curve:
            peak = max(peak, value)
            if peak > 0.0:
                worst = max(worst, (peak - value) / peak)
        return worst

    @property
    def growth_rate(self) -> float:
        """Mean log growth per bet — the quantity Kelly staking maximises."""
        if not self.bets or self.final_bankroll <= 0.0:
            return -math.inf
        return math.log(self.final_bankroll / self.starting_bankroll) / len(self.bets)

    def summary(self) -> str:
        if not self.bets:
            return "no bets placed: no price cleared the minimum edge"
        return (
            f"{len(self.bets)} bets | staked {self.turnover:.2f} | "
            f"bankroll {self.starting_bankroll:.2f} -> {self.final_bankroll:.2f} | "
            f"ROI {self.roi * 100:+.1f}% | yield {self.yield_on_turnover * 100:+.2f}% | "
            f"hit {self.hit_rate * 100:.1f}% | max drawdown {self.max_drawdown * 100:.1f}% | "
            f"mean edge {self.mean_edge * 100:+.2f}%"
        )

    def __str__(self) -> str:
        return self.summary()


def simulate_betting(
    records: Sequence[BacktestRecord],
    odds: Mapping[Fixture, MatchOdds],
    forecaster: str,
    *,
    starting_bankroll: float = 1.0,
    kelly_fraction: float = 0.25,
    minimum_edge: float = 0.02,
    maximum_stake: float = 0.05,
    devig_method: DevigMethod = DevigMethod.SHIN,
) -> BettingResult:
    """Stake a forecaster's disagreements with the book and settle them.

    Args:
        records: backtest records, in chronological order.
        odds: quoted prices keyed by fixture.
        forecaster: which forecaster's probabilities to bet.
        starting_bankroll: initial capital.
        kelly_fraction: Kelly multiplier.  Quarter-Kelly is the usual
            compromise between growth and the drawdowns full Kelly produces;
            zero places no bets at all, which is a useful control run.
        minimum_edge: skip bets whose expected profit per unit is below this.
            The single most important guard against betting on model noise.
        maximum_stake: cap on any single stake as a fraction of bankroll.
        devig_method: how to strip the margin before comparing prices.

    Returns:
        A :class:`BettingResult` with the settled bets and the bankroll path.
    """
    if starting_bankroll <= 0.0:
        raise ValueError("starting bankroll must be positive")
    if not 0.0 <= kelly_fraction <= 1.0:
        raise ValueError("kelly_fraction must lie in [0, 1]")
    if minimum_edge < 0.0:
        raise ValueError("minimum_edge must be non-negative")
    if not 0.0 < maximum_stake <= 1.0:
        raise ValueError("maximum_stake must lie in (0, 1]")

    bankroll = starting_bankroll
    curve: list[float] = [bankroll]
    bets: list[Bet] = []
    turnover = 0.0

    for record in records:
        quote = odds.get(record.fixture)
        if quote is None:
            continue
        probabilities = record.forecasts.get(forecaster)
        if probabilities is None:
            raise KeyError(f"no forecasts recorded for {forecaster!r}")

        market = fair_probabilities(quote, devig_method).as_tuple()
        allocation = kelly_portfolio(probabilities, quote, fraction=kelly_fraction)
        prices = quote.as_tuple()
        model = probabilities.as_tuple()

        for outcome in Outcome:
            i = outcome.index
            stake_fraction = allocation.stakes[i]
            if stake_fraction <= 0.0:
                continue
            if model[i] * prices[i] - 1.0 < minimum_edge:
                continue
            stake = min(stake_fraction, maximum_stake) * bankroll
            if stake <= 0.0:
                continue
            won = record.outcome is outcome
            profit = stake * (prices[i] - 1.0) if won else -stake
            bankroll += profit
            turnover += stake
            bets.append(
                Bet(
                    fixture=record.fixture,
                    outcome=outcome,
                    decimal_odds=prices[i],
                    stake=stake,
                    model_probability=model[i],
                    market_probability=market[i],
                    won=won,
                    profit=profit,
                    bankroll_after=bankroll,
                )
            )
            curve.append(bankroll)
            if bankroll <= 0.0:  # ruin: stop rather than simulate negative stakes
                return _finish(bets, curve, starting_bankroll, bankroll, turnover,
                               kelly_fraction, minimum_edge)

    return _finish(
        bets, curve, starting_bankroll, bankroll, turnover, kelly_fraction, minimum_edge
    )


def _finish(
    bets: Sequence[Bet],
    curve: Sequence[float],
    starting_bankroll: float,
    bankroll: float,
    turnover: float,
    kelly_fraction: float,
    minimum_edge: float,
) -> BettingResult:
    return BettingResult(
        bets=tuple(bets),
        bankroll_curve=tuple(curve),
        starting_bankroll=starting_bankroll,
        final_bankroll=bankroll,
        turnover=turnover,
        kelly_fraction=kelly_fraction,
        minimum_edge=minimum_edge,
    )
