"""Elo: conservation laws, monotonicity and Davidson's tie model."""

from __future__ import annotations

import datetime as dt
import math
import random

from foot.data.synthetic import synthetic_league
from foot.domain import Fixture, Match, MatchLog, Outcome, Score
from foot.ratings.elo import EloRatingSystem, MarginRule, _davidson, _margin_multiplier

from support import assert_close, assert_probability_vector, assert_raises

DAY = dt.date(2024, 1, 1)


def test_elo_is_zero_sum() -> None:
    """Points are transferred, never created: the total rating is invariant."""
    matches, _ = synthetic_league(seed=5, seasons=2)
    for rule in MarginRule:
        table = EloRatingSystem(margin_rule=rule).rate(matches)
        total = math.fsum(rating for _, rating in table.ranking())
        assert_close(
            total, 1500.0 * len(matches.teams), tolerance=1e-8, label=f"total under {rule.value}"
        )


def test_season_regression_pulls_towards_the_mean_but_conserves_the_total() -> None:
    matches, _ = synthetic_league(seed=6, seasons=3)
    plain = EloRatingSystem().rate(matches)
    regressed = EloRatingSystem(season_regression=1 / 3).rate(matches)
    spread_plain = max(r for _, r in plain.ranking()) - min(r for _, r in plain.ranking())
    spread_regressed = max(r for _, r in regressed.ranking()) - min(
        r for _, r in regressed.ranking()
    )
    assert spread_regressed < spread_plain
    assert_close(
        math.fsum(r for _, r in regressed.ranking()),
        1500.0 * len(matches.teams),
        tolerance=1e-8,
        label="total after regression",
    )


def test_a_win_raises_the_winner_and_lowers_the_loser_symmetrically() -> None:
    system = EloRatingSystem(k_factor=20.0, margin_rule=MarginRule.NONE)
    table = system.rate([Match("A", "B", DAY, Score(1, 0))], track_history=True)
    assert table.rating("A") > 1500.0 > table.rating("B")
    assert_close(
        table.rating("A") - 1500.0, 1500.0 - table.rating("B"), label="symmetry"
    )
    snapshot = table.history[0]
    assert snapshot.change > 0.0
    assert snapshot.actual_home_score == 1.0
    assert 0.0 < snapshot.expected_home_score < 1.0


def test_a_draw_between_equals_moves_nothing() -> None:
    table = EloRatingSystem(home_advantage=0.0).rate([Match("A", "B", DAY, Score(1, 1))])
    assert_close(table.rating("A"), 1500.0, tolerance=1e-9, label="A")
    assert_close(table.rating("B"), 1500.0, tolerance=1e-9, label="B")


def test_home_advantage_makes_a_home_draw_cost_the_favourite() -> None:
    table = EloRatingSystem(home_advantage=60.0).rate([Match("A", "B", DAY, Score(1, 1))])
    assert table.rating("A") < 1500.0 < table.rating("B")


def test_expected_score_is_monotone_and_centred() -> None:
    system = EloRatingSystem(home_advantage=0.0)
    table = system.rate(
        [Match("A", "B", DAY, Score(3, 0)), Match("A", "B", DAY, Score(2, 0))]
    )
    assert_close(
        _elo_expectation(0.0), 0.5, label="parity"
    )
    previous = 0.0
    for difference in (-400.0, -200.0, 0.0, 200.0, 400.0):
        value = _elo_expectation(difference)
        assert value > previous
        previous = value
    assert_close(_elo_expectation(400.0), 10.0 / 11.0, tolerance=1e-12, label="+400 points")
    assert table.expected_score("A", "B") > 0.5


def _elo_expectation(difference: float) -> float:
    return 1.0 / (1.0 + math.pow(10.0, -difference / 400.0))


def test_margin_rules_scale_in_the_documented_way() -> None:
    assert _margin_multiplier(MarginRule.NONE, 5, 0.0) == 1.0
    assert _margin_multiplier(MarginRule.GOAL_DIFFERENCE, 1, 0.0) == 1.0
    assert _margin_multiplier(MarginRule.GOAL_DIFFERENCE, 2, 0.0) == 1.5
    assert_close(_margin_multiplier(MarginRule.GOAL_DIFFERENCE, 3, 0.0), 1.75, label="gd 3")
    assert_close(_margin_multiplier(MarginRule.GOAL_DIFFERENCE, 4, 0.0), 1.875, label="gd 4")
    # The logarithmic rule damps blowouts by strong favourites.
    assert _margin_multiplier(MarginRule.LOGARITHMIC, 4, 600.0) < _margin_multiplier(
        MarginRule.LOGARITHMIC, 4, 0.0
    )
    for rule in MarginRule:
        assert _margin_multiplier(rule, 0, 0.0) == 1.0


def test_davidson_is_a_distribution_that_preserves_the_odds_ratio() -> None:
    for difference in (-500.0, -120.0, 0.0, 75.0, 380.0):
        for nu in (0.0, 0.4, 0.75, 1.5):
            probabilities = _davidson(difference, nu)
            assert_probability_vector(probabilities.as_tuple(), tolerance=1e-12)
            if probabilities.away > 0.0:
                assert_close(
                    probabilities.home / probabilities.away,
                    10.0 ** (difference / 400.0),
                    tolerance=1e-9,
                    label="win/loss odds ratio",
                )


def test_davidson_draw_parameter_controls_the_draw_rate() -> None:
    assert _davidson(0.0, 0.0).draw == 0.0
    assert_close(_davidson(0.0, 1.0).draw, 1.0 / 3.0, tolerance=1e-12, label="nu=1 at parity")
    previous = -1.0
    for nu in (0.0, 0.25, 0.75, 2.0, 8.0):
        draw = _davidson(0.0, nu).draw
        assert draw > previous
        previous = draw
    assert _davidson(0.0, 8.0).draw < 1.0


def test_draw_parameter_is_recovered_by_maximum_likelihood() -> None:
    """Plant a known tie rate in synthetic results and fit it back."""
    rng = random.Random(7)
    planted = 0.9
    teams = [f"T{i}" for i in range(12)]
    strengths = {team: rng.gauss(0.0, 120.0) for team in teams}
    matches = []
    day = DAY
    for _ in range(60):
        for i, home in enumerate(teams):
            for away in teams[i + 1 :]:
                difference = strengths[home] - strengths[away] + 60.0
                probabilities = _davidson(difference, planted)
                draw = rng.random()
                cumulative = probabilities.home
                if draw < cumulative:
                    score = Score(1, 0)
                elif draw < cumulative + probabilities.draw:
                    score = Score(1, 1)
                else:
                    score = Score(0, 1)
                matches.append(Match(home, away, day, score))
        day += dt.timedelta(days=7)

    log = MatchLog(matches)
    table = EloRatingSystem(k_factor=0.0001).rate(log)  # keep ratings essentially fixed
    # Give the table the true strengths so only nu is being estimated.
    for team, value in strengths.items():
        table._ratings[team] = 1500.0 + value
    assert_close(table.fit_draw_parameter(log), planted, tolerance=0.05, label="fitted nu")


def test_elo_table_predicts_fixtures_and_reports_unknowns() -> None:
    matches, _ = synthetic_league(seed=8)
    table = EloRatingSystem().rate(matches)
    fixture = Fixture(matches.teams[0], matches.teams[1], matches.end)
    assert_probability_vector(table.predict(fixture).as_tuple(), tolerance=1e-12)
    assert matches.teams[0] in table
    assert "Nowhere United" not in table
    assert table.rating("Nowhere United") == 1500.0  # the configured default
    assert table.matches_played(matches.teams[0]) == 2 * (len(matches.teams) - 1)
    neutral = Fixture(matches.teams[0], matches.teams[1], matches.end, neutral=True)
    assert table.predict(neutral).home < table.predict(fixture).home


def test_elo_ordering_is_independent_of_input_ordering() -> None:
    """Elo is path dependent, so the log must be sorted before it is applied."""
    matches, _ = synthetic_league(seed=9)
    forwards = EloRatingSystem().rate(matches)
    backwards = EloRatingSystem().rate(list(matches)[::-1])
    for team, rating in forwards.ranking():
        assert_close(backwards.rating(team), rating, tolerance=1e-9, label=team)


def test_configuration_is_validated() -> None:
    with assert_raises(ValueError, match="k_factor"):
        EloRatingSystem(k_factor=0.0)
    with assert_raises(ValueError, match="season_regression"):
        EloRatingSystem(season_regression=1.5)
    with assert_raises(ValueError, match="draw_parameter"):
        EloRatingSystem(draw_parameter=-0.1)
    with assert_raises(ValueError, match="season_gap_days"):
        EloRatingSystem(season_gap_days=0)
    table = EloRatingSystem().rate(MatchLog())
    with assert_raises(ValueError, match="empty"):
        table.fit_draw_parameter(MatchLog())
    with assert_raises(ValueError, match="draw parameter"):
        EloRatingSystem().rate(MatchLog()).outcome_probabilities("A", "B", draw_parameter=-1.0)


def test_outcomes_map_to_the_expected_scores() -> None:
    system = EloRatingSystem(home_advantage=0.0, margin_rule=MarginRule.NONE)
    for score, outcome in ((Score(2, 0), Outcome.HOME_WIN), (Score(0, 2), Outcome.AWAY_WIN)):
        table = system.rate([Match("A", "B", DAY, score)], track_history=True)
        snapshot = table.history[0]
        assert snapshot.actual_home_score == (1.0 if outcome is Outcome.HOME_WIN else 0.0)
