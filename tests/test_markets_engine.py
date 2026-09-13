"""Market arithmetic checked against hand calculations, not against itself."""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Callable
from zoneinfo import ZoneInfo

from foot.domain import Outcome, Score
from foot.market.devig import DevigMethod, remove_margin, shin_insider_fraction
from foot.market.kelly import NotMutuallyExclusiveError, kelly_portfolio
from foot.market.odds import MatchOdds
from foot.markets.catalogue import (
    UnsupportedMarketError,
    asian_handicap,
    both_teams_to_score,
    correct_score_group,
    double_chance,
    draw_no_bet,
    match_result,
    refuse_if_unsupported,
    standard_catalogue,
    team_total,
    total_goals,
)
from foot.markets.portfolio import (
    OverlapError,
    build_ticket,
    plan_stakes,
    verify_exclusive,
)
from foot.markets.pricing import (
    PricedOffer,
    coherent_market_set,
    grid_diagnostics,
    market_completeness,
    price_catalogue,
    price_offer,
)
from foot.markets.selection import DecisionStatus, RankingCriteria, Scenario, select_best
from foot.markets.settlement import Settlement, SettlementProfile, unit_return
from foot.models.base import ScoreMatrix

from support import assert_close, assert_probability_vector, assert_raises

MATRIX = ScoreMatrix.from_rates(1.65, 1.15)
PARIS = ZoneInfo("Europe/Paris")


def _manual(predicate: Callable[[int, int], bool]) -> float:
    """Sum the grid by hand, independently of the pricing code."""
    return math.fsum(
        p
        for home, row in enumerate(MATRIX.grid)
        for away, p in enumerate(row)
        if predicate(home, away)
    )


# --------------------------------------------------------------------------- #
# Settlement arithmetic
# --------------------------------------------------------------------------- #


def test_expected_value_generalises_the_binary_formula() -> None:
    binary = SettlementProfile.binary(0.55)
    assert_close(binary.expected_value(2.0), 0.55 * 2.0 - 1.0, label="cas binaire")

    with_push = SettlementProfile.from_units({1.0: 0.45, 0.0: 0.25, -1.0: 0.30})
    expected = 0.45 * 1.30 + 0.25 * 0.0 - 0.30
    assert_close(with_push.expected_value(2.30), expected, label="avec remboursement")
    # The binary formula is wrong here, and by a wide margin.
    assert abs((0.45 * 2.30 - 1.0) - expected) > 0.2


def test_half_win_and_half_loss_settle_at_half_stake() -> None:
    assert_close(unit_return(0.5, 2.5), 1.75, label="demi-gain")
    assert_close(unit_return(-0.5, 2.5), 0.5, label="demi-perte")
    assert_close(unit_return(0.0, 2.5), 1.0, label="remboursement")
    profile = SettlementProfile.from_units({1.0: 0.5, -0.5: 0.2, -1.0: 0.3})
    assert_close(profile.expected_value(2.1), 0.5 * 1.1 - 0.2 * 0.5 - 0.3, label="mélange")
    with assert_raises(ValueError):
        Settlement.from_unit(0.37)


def test_break_even_odds_zero_the_expected_value() -> None:
    for units in ({1.0: 0.45, 0.0: 0.25, -1.0: 0.30}, {1.0: 0.5, -0.5: 0.2, -1.0: 0.3}):
        profile = SettlementProfile.from_units(units)
        fair = profile.break_even_odds()
        assert_close(profile.expected_value(fair), 0.0, tolerance=1e-12, label="équilibre")


# --------------------------------------------------------------------------- #
# Pricing against independent hand sums
# --------------------------------------------------------------------------- #


def test_every_family_matches_a_hand_computed_grid_sum() -> None:
    checks = (
        (match_result(Outcome.HOME_WIN), lambda h, a: h > a),
        (match_result(Outcome.DRAW), lambda h, a: h == a),
        (double_chance(Outcome.HOME_WIN, Outcome.DRAW), lambda h, a: h >= a),
        (total_goals(2.5, over=True), lambda h, a: h + a > 2.5),
        (total_goals(1.5, over=False), lambda h, a: h + a < 1.5),
        (both_teams_to_score(yes=True), lambda h, a: h > 0 and a > 0),
        (team_total(1.5, home=True, over=True), lambda h, _a: h > 1.5),
        (asian_handicap(-1.5), lambda h, a: h - a > 1.5),
    )
    for offer, predicate in checks:
        profile = price_offer(offer, MATRIX)
        assert_close(
            profile.win_probability, _manual(predicate), tolerance=1e-12, label=offer.key
        )


def test_draw_no_bet_refunds_exactly_the_draw() -> None:
    profile = price_offer(draw_no_bet(Outcome.HOME_WIN), MATRIX)
    assert_close(profile.push_probability, _manual(lambda h, a: h == a), label="remboursement")
    assert_close(profile.win_probability, _manual(lambda h, a: h > a), label="gain")
    assert_close(
        profile.probability(Settlement.LOSS), _manual(lambda h, a: h < a), label="perte"
    )


def test_a_whole_asian_line_pushes_and_a_quarter_line_half_settles() -> None:
    whole = price_offer(asian_handicap(-1.0), MATRIX)
    assert_close(whole.push_probability, _manual(lambda h, a: h - a == 1), label="push -1")

    quarter = price_offer(asian_handicap(-0.25), MATRIX)
    assert quarter.probability(Settlement.HALF_LOSS) > 0.0
    assert_close(
        quarter.probability(Settlement.HALF_LOSS),
        _manual(lambda h, a: h == a),
        tolerance=1e-12,
        label="le nul est une demi-perte sur -0.25",
    )
    # A quarter line has the mean expected value of its two neighbours.
    level = price_offer(asian_handicap(0.0), MATRIX).expected_value(2.0)
    half = price_offer(asian_handicap(-0.5), MATRIX).expected_value(2.0)
    assert_close(quarter.expected_value(2.0), 0.5 * (level + half), label="moyenne des voisines")


def test_a_score_group_is_a_union_of_cells() -> None:
    scores = [Score(1, 0), Score(2, 0), Score(2, 1)]
    profile = price_offer(correct_score_group(scores), MATRIX)
    manual = math.fsum(MATRIX.probability(s.home, s.away) for s in scores)
    assert_close(profile.win_probability, manual, tolerance=1e-12, label="union")


def test_a_same_match_combination_is_an_intersection_not_a_product() -> None:
    """The requirement that catches the commonest mispricing in football betting."""
    home = match_result(Outcome.HOME_WIN)
    over = total_goals(2.5, over=True)
    combined = price_offer(home.combined_with(over), MATRIX)

    intersection = _manual(lambda h, a: h > a and h + a > 2.5)
    assert_close(combined.win_probability, intersection, tolerance=1e-12, label="intersection")

    product = (
        price_offer(home, MATRIX).win_probability * price_offer(over, MATRIX).win_probability
    )
    assert abs(combined.win_probability - product) > 0.02, (
        "sur ces taux, l'intersection et le produit doivent différer nettement"
    )


def test_a_losing_leg_beats_a_refunded_one_in_a_combination() -> None:
    """Precedence matters: a loss anywhere sinks the ticket, a refund only voids it."""
    combined = match_result(Outcome.HOME_WIN).combined_with(draw_no_bet(Outcome.HOME_WIN))
    assert combined.unit_result(2, 1) == 1.0    # both legs win
    # At 1-1 the "home win" leg loses outright while "draw no bet" refunds: the
    # loss dominates, because a refunded leg cannot rescue a lost one.
    assert combined.unit_result(1, 1) == -1.0
    assert combined.unit_result(0, 1) == -1.0

    # A refund only voids the ticket when nothing else has lost.
    over = total_goals(2.0, over=True)
    void_combo = match_result(Outcome.HOME_WIN).combined_with(over)
    assert void_combo.unit_result(2, 0) == 0.0  # home wins, total lands exactly on the line
    assert void_combo.unit_result(3, 0) == 1.0
    assert void_combo.unit_result(1, 2) == -1.0
    with assert_raises(UnsupportedMarketError, match="fractionnée"):
        match_result(Outcome.HOME_WIN).combined_with(asian_handicap(-0.25))


def test_markets_needing_their_own_model_are_refused() -> None:
    for name in ("Total corners 9.5", "Premier buteur", "Cartons +4.5"):
        with assert_raises(UnsupportedMarketError, match="ne se déduit pas"):
            refuse_if_unsupported(name)
    refuse_if_unsupported("Plus de 2.5 buts")  # must not raise


# --------------------------------------------------------------------------- #
# Grid health, stale prices, incomplete markets
# --------------------------------------------------------------------------- #


def test_truncation_and_dixon_coles_validity_are_reported() -> None:
    healthy = grid_diagnostics(MATRIX, home_rate=1.65, away_rate=1.15, rho=-0.08)
    assert healthy.trustworthy
    assert healthy.warnings() == ()

    truncated = ScoreMatrix.from_rates(3.0, 3.0, max_goals=2)
    poor = grid_diagnostics(truncated, home_rate=3.0, away_rate=3.0, rho=-0.5)
    warnings = poor.warnings()
    assert any("troncature" in w for w in warnings)
    assert any("Dixon-Coles" in w for w in warnings)


def test_a_stale_quote_is_detected_and_rejected() -> None:
    as_of = dt.datetime(2026, 9, 13, 12, 0, tzinfo=PARIS)
    offer = match_result(Outcome.HOME_WIN).with_odds(2.60)
    fresh = PricedOffer(
        offer=offer, profile=price_offer(offer, MATRIX),
        quoted_at=as_of - dt.timedelta(hours=1), as_of=as_of,
    )
    stale = PricedOffer(
        offer=offer, profile=price_offer(offer, MATRIX),
        quoted_at=as_of - dt.timedelta(hours=30), as_of=as_of,
    )
    assert not fresh.is_stale()
    assert stale.is_stale()

    decision = select_best([stale], criteria=RankingCriteria(min_expected_value=-1.0))
    assert decision.status is DecisionStatus.NO_BET
    assert "périmée" in decision.reason


def test_an_incomplete_market_is_named_as_such() -> None:
    complete = [
        PricedOffer(
            offer=match_result(o).with_odds(2.5), profile=price_offer(match_result(o), MATRIX)
        )
        for o in Outcome
    ]
    ok, message = market_completeness(complete)
    assert ok and "complet" in message

    partial = complete[:2]
    ok, message = market_completeness(partial)
    assert not ok and "incomplet" in message
    assert market_completeness([])[0] is False


def test_devigging_needs_a_coherent_market() -> None:
    priced = price_catalogue(
        standard_catalogue(), MATRIX,
        quotes={"1X2:H": 2.05, "1X2:D": 3.50, "1X2:A": 3.80, "OU:2.5:over": 1.95},
    )
    groups = coherent_market_set(priced)
    assert any(len(items) == 3 for items in groups.values()), "le 1X2 doit former un groupe"
    assert all(item.has_price for items in groups.values() for item in items)


# --------------------------------------------------------------------------- #
# Shin's domain, as corrected in the README
# --------------------------------------------------------------------------- #


def test_shin_has_no_root_on_an_arbitrage_book() -> None:
    """3.50 / 3.50 / 3.50 sums to 0.857 < 1, so no Shin root exists.

    The implementation short-circuits to ``z = 0``, reducing Shin to plain
    normalisation.  An earlier edition of the README claimed a root existed for
    all decimal odds; that claim was too broad, and this test pins the real
    behaviour so it cannot drift.
    """
    odds = MatchOdds(3.50, 3.50, 3.50)
    assert odds.booksum < 1.0
    assert odds.is_arbitrage
    assert shin_insider_fraction(odds.raw_probabilities()) == 0.0
    for method in DevigMethod:
        probabilities = remove_margin(odds.raw_probabilities(), method)
        assert_probability_vector(probabilities)
        assert_close(probabilities[0], 1 / 3, tolerance=1e-9, label=method.value)

    # With a margin, a genuine root is found.
    assert shin_insider_fraction(MatchOdds(2.10, 3.40, 3.60).raw_probabilities()) > 0.0


# --------------------------------------------------------------------------- #
# Kelly, exclusivity and tickets
# --------------------------------------------------------------------------- #


def test_overlapping_selections_are_rejected_on_the_grid() -> None:
    """The check the probability-only API cannot perform."""
    verify_exclusive([match_result(o) for o in Outcome], MATRIX)  # no raise
    with assert_raises(OverlapError, match="gagnent tous deux"):
        verify_exclusive(
            [match_result(Outcome.HOME_WIN), total_goals(2.5, over=True)], MATRIX
        )
    with assert_raises(OverlapError):
        verify_exclusive(
            [match_result(Outcome.HOME_WIN), double_chance(Outcome.HOME_WIN, Outcome.DRAW)],
            MATRIX,
        )


def test_simultaneous_kelly_states_its_precondition() -> None:
    with assert_raises(NotMutuallyExclusiveError, match="mutually exclusive"):
        kelly_portfolio([0.48, 0.55], [2.2, 1.9])
    assert kelly_portfolio([0.5, 0.28, 0.22], [2.3, 3.6, 4.4]).total_staked > 0.0


def test_staking_respects_the_stated_budget_and_caps() -> None:
    criteria = RankingCriteria(min_expected_value=0.02)
    offers = price_catalogue(
        standard_catalogue(), MATRIX, quotes={"1X2:H": 2.60, "1X2:D": 3.60, "1X2:A": 4.20}
    )
    decision = select_best(offers, criteria=criteria)
    plan = plan_stakes([("Match test", decision)], budget=200.0, max_single=0.03)
    assert plan.budget == 200.0
    for stake in plan.stakes:
        assert stake.fraction_of_budget <= 0.03 + 1e-9
        assert 0.0 < stake.amount <= 200.0 * 0.03 + 1e-9
    assert plan.committed <= 200.0
    assert "ne place aucun pari" in plan.render()
    with assert_raises(ValueError, match="budget"):
        plan_stakes([], budget=0.0)


def test_a_ticket_stays_as_short_as_it_is_justified() -> None:
    criteria = RankingCriteria(min_expected_value=0.02)
    decisions = []
    for index, quotes in enumerate(
        ({"1X2:H": 2.60, "1X2:D": 3.60, "1X2:A": 4.20},
         {"1X2:H": 2.80, "1X2:D": 3.40, "1X2:A": 4.00}),
    ):
        offers = price_catalogue(standard_catalogue(), MATRIX, quotes=quotes)
        decisions.append((f"Match {index}", select_best(offers, criteria=criteria)))

    ticket = build_ticket(decisions, max_legs=5)
    assert 1 <= len(ticket.legs) <= 2, "max_legs est un plafond, pas un quota à remplir"
    assert ticket.independence_assumed == (len(ticket.legs) > 1)
    if len(ticket.legs) > 1:
        assert any("indépendance" in w for w in ticket.warnings)
        assert sum(1 for leg in ticket.legs if leg.drop_first) == 1
    empty = build_ticket([])
    assert empty.legs == ()
    assert "aucune sélection" in empty.warnings[0]


# --------------------------------------------------------------------------- #
# Selection: criteria declared before evaluation
# --------------------------------------------------------------------------- #


def test_criteria_are_declared_before_evaluation_and_reported() -> None:
    criteria = RankingCriteria(min_expected_value=0.05, version="test-v1")
    offers = price_catalogue(
        standard_catalogue(), MATRIX, quotes={"1X2:H": 2.60, "1X2:D": 3.60, "1X2:A": 4.20}
    )
    decision = select_best(offers, criteria=criteria)
    assert decision.criteria is criteria
    described = criteria.describe()
    assert "avant évaluation" in described and "test-v1" in described


def test_robustness_uses_the_worst_credible_scenario() -> None:
    criteria = RankingCriteria(min_expected_value=0.01, min_worst_case_value=0.0)
    offers = price_catalogue(
        standard_catalogue(), MATRIX, quotes={"1X2:H": 2.10, "1X2:D": 3.60, "1X2:A": 4.20}
    )
    scenarios = [
        Scenario("attaque domicile effondrée", ScoreMatrix.from_rates(0.9, 1.15)),
        Scenario("match ouvert", ScoreMatrix.from_rates(2.1, 1.5)),
    ]
    decision = select_best(offers, criteria=criteria, scenarios=scenarios)
    if decision.main is not None:
        assert decision.main.worst_case_value is not None
        assert decision.main.worst_case_value <= (decision.main.expected_value or 0.0) + 1e-12
        assert decision.main.worst_case_value >= criteria.min_worst_case_value


def test_without_a_price_the_selector_gives_an_angle_and_a_condition() -> None:
    offers = price_catalogue(standard_catalogue(), MATRIX)
    decision = select_best(
        offers, criteria=RankingCriteria(), sport_angle="buts attendus 1.65 – 1.15"
    )
    assert decision.status is DecisionStatus.PRICE_CONDITION
    assert "aucune cote" in decision.reason
    assert "NON vérifiée" in decision.price_condition
    assert decision.rationale == "buts attendus 1.65 – 1.15"
