"""Odds arithmetic, margin removal and Kelly staking."""

from __future__ import annotations

import math
import random
from collections.abc import Sequence

from foot.domain import Outcome, OutcomeProbabilities
from foot.market.devig import (
    DevigMethod,
    _shin_probabilities,
    fair_probabilities,
    remove_margin,
    shin_insider_fraction,
)
from foot.market.kelly import (
    expected_log_growth,
    expected_value,
    kelly_fraction,
    kelly_portfolio,
)
from foot.market.odds import (
    MatchOdds,
    american_to_decimal,
    decimal_to_american,
    decimal_to_fractional,
    fractional_to_decimal,
    implied_probability,
)
from foot.numerics.optimize import minimize

from support import assert_close, assert_probability_vector, assert_raises


def test_odds_conversions_round_trip() -> None:
    for decimal in (1.01, 1.5, 2.0, 3.5, 11.0, 101.0):
        assert_close(
            american_to_decimal(decimal_to_american(decimal)),
            decimal,
            tolerance=1e-12,
            label=f"american round trip at {decimal}",
        )
        assert_close(
            fractional_to_decimal(decimal_to_fractional(decimal, max_denominator=10**6)),
            decimal,
            tolerance=1e-9,
            label=f"fractional round trip at {decimal}",
        )
    assert_close(american_to_decimal(150.0), 2.5, label="+150")
    assert_close(american_to_decimal(-200.0), 1.5, label="-200")
    assert_close(decimal_to_american(2.0), 100.0, label="evens")
    assert str(decimal_to_fractional(3.5)) == "5/2"
    assert_close(implied_probability(4.0), 0.25, label="implied")


def test_odds_validation() -> None:
    for bad in (1.0, 0.5, -2.0, math.inf, math.nan):
        with assert_raises(ValueError):
            MatchOdds(bad, 3.0, 3.0)
    with assert_raises(ValueError):
        american_to_decimal(0.0)
    with assert_raises(ValueError):
        fractional_to_decimal("-1/2")


def test_book_arithmetic() -> None:
    odds = MatchOdds(2.10, 3.40, 3.60)
    assert_close(odds.booksum, 1.0 / 2.10 + 1.0 / 3.40 + 1.0 / 3.60, label="booksum")
    assert_close(odds.overround, odds.booksum - 1.0, label="overround")
    assert not odds.is_arbitrage
    assert MatchOdds(3.2, 4.0, 4.2).is_arbitrage
    assert odds[Outcome.HOME_WIN] == 2.10
    assert_close(odds.payout(Outcome.DRAW, 10.0), 34.0, label="payout")
    assert_close(odds.profit(Outcome.DRAW, Outcome.DRAW, 10.0), 24.0, label="winning profit")
    assert_close(odds.profit(Outcome.HOME_WIN, Outcome.DRAW, 10.0), -10.0, label="losing")


def test_quoting_probabilities_back_as_odds_reproduces_the_margin() -> None:
    probabilities = OutcomeProbabilities(0.45, 0.28, 0.27)
    for margin in (0.0, 0.03, 0.10):
        odds = MatchOdds.from_probabilities(probabilities, margin=margin)
        assert_close(odds.overround, margin, tolerance=1e-12, label=f"margin {margin}")
        recovered = fair_probabilities(odds, DevigMethod.MULTIPLICATIVE)
        for got, want in zip(recovered.as_tuple(), probabilities.as_tuple(), strict=True):
            assert_close(got, want, tolerance=1e-12, label="round trip")


def test_every_devig_method_returns_a_distribution() -> None:
    books = [
        MatchOdds(2.10, 3.40, 3.60),
        MatchOdds(1.12, 9.50, 26.0),
        MatchOdds(1.01, 41.0, 81.0),
        MatchOdds(4.5, 3.7, 1.85),
        MatchOdds(2.9, 3.1, 2.9),
    ]
    for odds in books:
        for method in DevigMethod:
            probabilities = fair_probabilities(odds, method)
            assert_probability_vector(probabilities.as_tuple(), tolerance=1e-12)
            assert probabilities.home > 0.0


def test_devig_is_a_no_op_on_a_margin_free_book() -> None:
    exact = [0.5, 0.25, 0.25]
    for method in DevigMethod:
        for got, want in zip(remove_margin(exact, method), exact, strict=True):
            assert_close(got, want, tolerance=1e-9, label=f"{method.value} passthrough")


def test_multiplicative_devig_preserves_price_ratios() -> None:
    odds = MatchOdds(2.10, 3.40, 3.60)
    raw = odds.raw_probabilities()
    fair = fair_probabilities(odds, DevigMethod.MULTIPLICATIVE).as_tuple()
    assert_close(fair[0] / fair[1], raw[0] / raw[1], tolerance=1e-12, label="ratio")


def test_devig_methods_disagree_about_longshots_in_the_documented_order() -> None:
    """Multiplicative keeps the favourite-longshot bias; additive over-corrects."""
    odds = MatchOdds(1.12, 9.50, 26.0)
    longshot = {
        method: fair_probabilities(odds, method).away for method in DevigMethod
    }
    assert longshot[DevigMethod.MULTIPLICATIVE] > longshot[DevigMethod.SHIN]
    assert longshot[DevigMethod.SHIN] > longshot[DevigMethod.ADDITIVE]


def test_shin_insider_fraction_grows_with_the_margin() -> None:
    probabilities = OutcomeProbabilities(0.45, 0.28, 0.27)
    previous = -1.0
    for margin in (0.0, 0.01, 0.05, 0.12, 0.25):
        odds = MatchOdds.from_probabilities(probabilities, margin=margin)
        z = shin_insider_fraction(odds.raw_probabilities())
        assert 0.0 <= z < 1.0
        assert z > previous
        previous = z


def test_shin_inversion_is_stable_for_extreme_insider_fractions() -> None:
    """The conjugate form must not lose precision as z approaches one."""
    raw = [0.55, 0.30, 0.20]
    booksum = math.fsum(raw)
    for z in (0.0, 0.5, 0.9, 1.0 - 1e-9, 1.0 - 1e-14):
        values = _shin_probabilities(raw, booksum, z)
        assert all(math.isfinite(v) and v > 0.0 for v in values), z
    limit = _shin_probabilities(raw, booksum, 1.0 - 1e-14)
    for got, want in zip(limit, [p * p / booksum for p in raw], strict=True):
        assert_close(got, want, tolerance=1e-6, label="limit as z -> 1")


def test_devig_validates_its_input() -> None:
    with assert_raises(ValueError, match="at least two"):
        remove_margin([0.5])
    with assert_raises(ValueError, match="positive"):
        remove_margin([0.5, -0.1])
    with assert_raises(ValueError, match="decimal odds"):
        remove_margin([1.2, 0.3])


def test_single_bet_kelly_matches_the_textbook_formula() -> None:
    assert_close(kelly_fraction(0.6, 2.0), 0.2, label="even money, 60 percent")
    assert_close(kelly_fraction(0.5, 3.0), 0.25, label="3.0 at evens odds")
    assert kelly_fraction(0.4, 2.0) == 0.0  # negative edge: no bet, never a lay
    assert_close(kelly_fraction(0.6, 2.0, fraction=0.5), 0.1, label="half Kelly")
    assert_close(expected_value(0.6, 2.0), 0.2, label="expected value")
    with assert_raises(ValueError):
        kelly_fraction(0.6, 2.0, fraction=1.5)
    with assert_raises(ValueError):
        expected_value(1.5, 2.0)


def test_kelly_portfolio_matches_direct_numerical_optimisation() -> None:
    """The closed form is checked against maximising expected log growth.

    Stakes are parameterised as a scaled simplex so that the bettor can never
    stake more than the bankroll, which is the constraint the closed form
    respects.  Agreement to machine precision over hundreds of random markets
    is what justifies using the formula instead of an optimiser.
    """

    def numerical_growth(probabilities: list[float], odds: list[float]) -> float:
        n = len(probabilities)

        def negative(vector: Sequence[float]) -> float:
            exponentials = [math.exp(min(v, 20.0)) for v in vector]
            total = math.fsum(exponentials)
            stakes = [e / total for e in exponentials[:n]]
            growth = expected_log_growth(stakes, probabilities, odds)
            return math.inf if growth == -math.inf else -growth

        best = math.inf
        for start in (-7.0, -4.0, -2.0, -0.7):
            result = minimize(
                negative, [start] * n + [0.0], gtol=1e-12, ftol=1e-16, max_iterations=6000
            )
            best = min(best, result.fun)
        return -best

    rng = random.Random(3)
    worst = 0.0
    for _ in range(120):
        n = rng.choice([2, 3, 3, 4])
        weights = [rng.random() + 0.02 for _ in range(n)]
        total = math.fsum(weights)
        probabilities = [w / total for w in weights]
        odds = [1.0 + rng.uniform(0.05, 25.0) for _ in range(n)]
        allocation = kelly_portfolio(probabilities, odds)
        assert allocation.total_staked <= 1.0 + 1e-12
        assert all(stake >= 0.0 for stake in allocation.stakes)
        worst = max(worst, numerical_growth(probabilities, odds) - allocation.growth_rate)
    assert worst < 1e-9, f"closed form fell short of the numerical optimum by {worst:.3e}"


def test_kelly_declines_a_market_with_no_edge() -> None:
    allocation = kelly_portfolio([0.4, 0.3, 0.3], [2.0, 3.0, 3.0])
    assert allocation.total_staked == 0.0
    assert allocation.growth_rate == 0.0
    assert allocation.reserve == 1.0
    assert allocation.backed == ()


def test_kelly_backs_only_the_outcomes_that_beat_the_reserve() -> None:
    """A worked case where a naive rule would wrongly back a third outcome."""
    probabilities = [0.7, 0.25, 0.05]
    odds = [1.5, 1.8, 20.0]
    allocation = kelly_portfolio(probabilities, odds)
    assert allocation.backed == (0, 2)
    assert allocation.stakes[1] == 0.0
    assert_close(allocation.reserve, 1.0 - allocation.total_staked, label="reserve")
    for index in allocation.backed:
        assert_close(
            allocation.stakes[index],
            probabilities[index] - allocation.reserve / odds[index],
            tolerance=1e-12,
            label=f"stake {index}",
        )


def test_kelly_stakes_everything_on_an_arbitrage() -> None:
    allocation = kelly_portfolio([0.6, 0.4], [2.0, 3.0])
    assert_close(allocation.total_staked, 1.0, tolerance=1e-12, label="total")
    assert allocation.growth_rate > 0.0
    assert_close(allocation.growth_rate, math.log(1.2), tolerance=1e-12, label="locked-in growth")


def test_fractional_kelly_scales_the_stakes_linearly() -> None:
    probabilities = [0.5, 0.28, 0.22]
    odds = [2.5, 3.6, 5.0]
    full = kelly_portfolio(probabilities, odds)
    half = kelly_portfolio(probabilities, odds, fraction=0.5)
    for a, b in zip(full.stakes, half.stakes, strict=True):
        assert_close(b, 0.5 * a, tolerance=1e-12, label="half Kelly stake")
    assert half.growth_rate <= full.growth_rate


def test_kelly_accepts_domain_objects_directly() -> None:
    probabilities = OutcomeProbabilities(0.5, 0.28, 0.22)
    odds = MatchOdds(2.5, 3.6, 5.0)
    allocation = kelly_portfolio(probabilities, odds)
    assert_close(
        allocation.stake_on(Outcome.HOME_WIN), allocation.stakes[0], label="stake_on"
    )
    with assert_raises(ValueError):
        kelly_portfolio([0.5, 0.5], [2.0, 2.0]).stake_on(Outcome.DRAW)


def test_kelly_validates_its_input() -> None:
    with assert_raises(ValueError, match="sum to 1"):
        kelly_portfolio([0.5, 0.2], [2.0, 3.0])
    with assert_raises(ValueError, match="equal length"):
        kelly_portfolio([0.5, 0.5], [2.0])
    with assert_raises(ValueError, match="at least two"):
        kelly_portfolio([1.0], [2.0])
    with assert_raises(ValueError, match="exceed 1"):
        kelly_portfolio([0.5, 0.5], [1.0, 3.0])
    with assert_raises(ValueError, match="fraction"):
        kelly_portfolio([0.5, 0.5], [2.5, 2.5], fraction=1.5)
    # A zero multiplier is legal everywhere and simply stakes nothing.
    assert kelly_portfolio([0.5, 0.5], [2.5, 2.5], fraction=0.0).total_staked == 0.0
    # Staking the whole bankroll on a price that returns nothing is ruin.
    assert expected_log_growth([1.0], [1.0], [0.0]) == -math.inf
    with assert_raises(ValueError, match="equal length"):
        expected_log_growth([1.0], [1.0], [2.0, 3.0])
