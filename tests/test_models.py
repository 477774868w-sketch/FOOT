"""Score matrices and the markets derived from them."""

from __future__ import annotations

import math

from foot.domain import Outcome, Score
from foot.models.base import ScoreMatrix
from foot.models.dixon_coles import dixon_coles_tau
from foot.numerics.stable import poisson_pmf

from support import assert_close, assert_probability_vector, assert_raises

RATES = ((1.6, 1.1), (0.7, 2.4), (3.0, 0.4), (1.35, 1.35), (0.05, 0.05))


def test_score_matrix_is_a_distribution_and_recovers_its_rates() -> None:
    for home_rate, away_rate in RATES:
        matrix = ScoreMatrix.from_rates(home_rate, away_rate)
        assert_probability_vector([p for row in matrix.grid for p in row], tolerance=1e-12)
        expected_home, expected_away = matrix.expected_goals()
        assert_close(expected_home, home_rate, tolerance=1e-9, label="home xG")
        assert_close(expected_away, away_rate, tolerance=1e-9, label="away xG")
        assert matrix.truncated_mass < 1e-11


def test_score_matrix_marginals_are_poisson() -> None:
    matrix = ScoreMatrix.from_rates(1.8, 1.2)
    for k in range(6):
        marginal = math.fsum(matrix.grid[k])
        assert_close(marginal, poisson_pmf(k, 1.8), tolerance=1e-12, label=f"P(home={k})")


def test_dixon_coles_tau_conserves_total_probability_exactly() -> None:
    """The defining property of the correction: it moves mass, never creates it.

    The four adjusted cells cancel algebraically against their Poisson weights,
    so a corrected grid still sums to one before any renormalisation.
    """
    for home_rate, away_rate in RATES:
        for rho in (-0.2, -0.05, 0.0, 0.08):
            perturbation = math.fsum(
                poisson_pmf(h, home_rate)
                * poisson_pmf(a, away_rate)
                * (dixon_coles_tau(h, a, home_rate, away_rate, rho) - 1.0)
                for h in range(2)
                for a in range(2)
            )
            assert_close(perturbation, 0.0, tolerance=1e-15, label="tau perturbation")


def test_dixon_coles_tau_shifts_mass_in_the_documented_direction() -> None:
    rho = -0.1
    assert dixon_coles_tau(0, 0, 1.5, 1.2, rho) > 1.0  # 0-0 inflated
    assert dixon_coles_tau(1, 1, 1.5, 1.2, rho) > 1.0  # 1-1 inflated
    assert dixon_coles_tau(1, 0, 1.5, 1.2, rho) < 1.0  # 1-0 deflated
    assert dixon_coles_tau(0, 1, 1.5, 1.2, rho) < 1.0  # 0-1 deflated
    assert dixon_coles_tau(2, 1, 1.5, 1.2, rho) == 1.0  # everything else untouched
    assert dixon_coles_tau(0, 0, 1.5, 1.2, 0.0) == 1.0


def test_outcome_probabilities_agree_with_the_margin_distribution() -> None:
    matrix = ScoreMatrix.from_rates(1.7, 1.3)
    margins = matrix.margin_distribution()
    outcome = matrix.outcome_probabilities()
    assert_close(
        math.fsum(p for m, p in margins.items() if m > 0), outcome.home, label="home"
    )
    assert_close(margins[0], outcome.draw, label="draw")
    assert_close(
        math.fsum(p for m, p in margins.items() if m < 0), outcome.away, label="away"
    )
    assert_probability_vector(list(margins.values()))


def test_totals_settlements_are_complete() -> None:
    matrix = ScoreMatrix.from_rates(1.4, 1.6)
    for line in (0.5, 1.5, 2.0, 2.5, 3.0, 4.5):
        settlement = matrix.totals(line)
        assert_close(
            settlement.over + settlement.push + settlement.under, 1.0, label=f"totals {line}"
        )
    assert matrix.totals(2.5).push == 0.0  # half lines cannot push
    assert matrix.totals(3.0).push > 0.0  # whole lines can


def test_quarter_lines_are_the_mean_of_their_neighbours() -> None:
    matrix = ScoreMatrix.from_rates(1.9, 1.1)
    quarter = matrix.totals(2.25)
    low, high = matrix.totals(2.0), matrix.totals(2.5)
    assert_close(quarter.over, 0.5 * (low.over + high.over), label="quarter over")
    assert_close(quarter.push, 0.5 * (low.push + high.push), label="quarter push")

    handicap = matrix.asian_handicap(-0.25)
    level, half = matrix.asian_handicap(0.0), matrix.asian_handicap(-0.5)
    assert_close(handicap.home, 0.5 * (level.home + half.home), label="quarter handicap")


def test_asian_handicaps_are_complete_and_anchored() -> None:
    matrix = ScoreMatrix.from_rates(1.5, 1.2)
    outcome = matrix.outcome_probabilities()
    for line in (-2.0, -1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 2.0):
        settlement = matrix.asian_handicap(line)
        assert_close(
            settlement.home + settlement.push + settlement.away, 1.0, label=f"handicap {line}"
        )
    assert_close(matrix.asian_handicap(-0.5).home, outcome.home, label="-0.5 == home win")
    assert_close(matrix.asian_handicap(0.0).push, outcome.draw, label="0.0 push == draw")
    assert_close(
        matrix.asian_handicap(0.5).home, outcome.home + outcome.draw, label="+0.5 == double chance"
    )


def test_both_teams_to_score_and_clean_sheets_are_consistent() -> None:
    matrix = ScoreMatrix.from_rates(1.5, 1.2)
    home_clean, away_clean = matrix.clean_sheet_probabilities()
    # P(both score) = 1 - P(home fails) - P(away fails) + P(neither scores)
    nil_nil = matrix.probability(0, 0)
    assert_close(
        matrix.both_teams_to_score(),
        1.0 - away_clean - home_clean + nil_nil,
        label="btts identity",
    )


def test_stronger_attack_raises_the_home_win_probability() -> None:
    previous = 0.0
    for rate in (0.8, 1.2, 1.6, 2.2, 3.0):
        probability = ScoreMatrix.from_rates(rate, 1.2).outcome_probabilities().home
        assert probability > previous
        previous = probability


def test_score_matrix_indexing_and_top_scorelines() -> None:
    matrix = ScoreMatrix.from_rates(1.6, 1.1)
    assert matrix[Score(1, 1)] == matrix.probability(1, 1)
    assert matrix[(1, 1)] == matrix.probability(1, 1)
    assert matrix.probability(-1, 0) == 0.0
    assert matrix.probability(999, 0) == 0.0
    top = matrix.top_scorelines(5)
    assert len(top) == 5
    assert [p for _, p in top] == sorted((p for _, p in top), reverse=True)
    assert matrix.most_likely_score()[0] == top[0][0]
    assert matrix.outcome_of(Outcome.HOME_WIN) == matrix.outcome_probabilities().home


def test_score_matrix_validates_inputs() -> None:
    with assert_raises(ValueError, match="non-negative"):
        ScoreMatrix.from_rates(-1.0, 1.0)
    with assert_raises(ValueError, match="max_goals"):
        ScoreMatrix.from_rates(1.0, 1.0, max_goals=0)
    with assert_raises(ValueError, match="negative"):
        ScoreMatrix.from_rates(
            1.0, 1.0, max_goals=3, correction=lambda _h, _a, _x, _y: -1.0
        )
    with assert_raises(ValueError, match="sum to 1"):
        ScoreMatrix(grid=((0.5, 0.2), (0.1, 0.1)))
    with assert_raises(ValueError, match="rectangular"):
        ScoreMatrix(grid=((1.0,), (0.0, 0.0)))
    with assert_raises(ValueError):
        ScoreMatrix.from_rates(1.0, 1.0).top_scorelines(0)


def test_truncation_is_reported_when_the_grid_is_too_small() -> None:
    matrix = ScoreMatrix.from_rates(3.0, 3.0, max_goals=2)
    assert matrix.truncated_mass > 0.3
    assert_probability_vector([p for row in matrix.grid for p in row], tolerance=1e-12)
