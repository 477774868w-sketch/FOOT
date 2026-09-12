"""Monte Carlo season projection: reproducibility and analytic agreement."""

from __future__ import annotations

import datetime as dt
import math

from foot.data.synthetic import synthetic_league
from foot.domain import Fixture, MatchLog
from foot.league.table import LeagueRules, LeagueTable
from foot.models.dixon_coles import DixonColesModel
from foot.simulation.season import SeasonSimulator

from support import assert_close, assert_probability_vector, assert_raises


def _fitted_model(
    seed: int = 5,
) -> tuple[DixonColesModel, MatchLog, list[Fixture]]:
    matches, _ = synthetic_league(seed=seed, seasons=2)
    cut = matches.dates()[len(matches.dates()) // 2]
    played = matches.before(cut)
    remaining = [m.fixture for m in matches.after(cut)]
    return DixonColesModel(half_life_days=250).fit(played), played, remaining


def test_projection_probabilities_are_distributions() -> None:
    model, played, remaining = _fitted_model()
    projection = SeasonSimulator(model, seed=1).run(played, remaining, iterations=2000)
    assert projection.remaining_fixtures == len(remaining)
    assert projection.iterations == 2000
    for row in projection.rows:
        assert_probability_vector(row.position_probabilities, tolerance=1e-12)
        assert 1.0 <= row.expected_position <= len(projection.rows)
        assert row.top(1) == row.title_probability
        assert_close(
            row.top(len(projection.rows)), 1.0, tolerance=1e-12, label="top-everything"
        )
    # Exactly one team finishes in each position, in every simulation.
    for place in range(len(projection.rows)):
        total = math.fsum(row.position_probabilities[place] for row in projection.rows)
        assert_close(total, 1.0, tolerance=1e-12, label=f"position {place + 1}")


def test_projection_is_reproducible_and_seed_sensitive() -> None:
    model, played, remaining = _fitted_model()
    first = SeasonSimulator(model, seed=42).run(played, remaining, iterations=500)
    again = SeasonSimulator(model, seed=42).run(played, remaining, iterations=500)
    different = SeasonSimulator(model, seed=43).run(played, remaining, iterations=500)
    assert first.rows == again.rows
    assert first.rows != different.rows


def test_expected_points_match_the_analytic_value_for_one_fixture() -> None:
    """A single remaining match, where the answer can be written down exactly.

    With one fixture left, the home team's expected points are
    ``3 P(win) + 1 P(draw)``, computed straight from the score matrix.  The
    simulator has to reproduce that to within Monte Carlo error, which pins
    down the sampler, the points arithmetic and the bookkeeping at once.
    """
    matches, _ = synthetic_league(seed=15, n_teams=4, seasons=2)
    model = DixonColesModel().fit(matches)
    home, away = model.parameters.teams[0], model.parameters.teams[1]
    fixture = Fixture(home, away, matches.end + dt.timedelta(days=7))
    probabilities = model.score_matrix(fixture).outcome_probabilities()

    iterations = 40000
    projection = SeasonSimulator(model, seed=3).run(
        MatchLog(), [fixture], iterations=iterations, teams=[home, away]
    )
    expected = 3.0 * probabilities.home + probabilities.draw
    # Three standard errors of the Monte Carlo mean, plus a little slack.
    tolerance = 3.0 * 1.5 / math.sqrt(iterations) + 0.01
    assert_close(
        projection.team(home).expected_points, expected, tolerance=tolerance, label="xPts"
    )
    # A draw leaves the two level on points, goal difference and goals scored,
    # so the final, deterministic fallback decides: the alphabetically first
    # name. `teams` is sorted, so `home` always wins that tiebreak.
    assert home < away
    assert_close(
        projection.team(home).title_probability,
        probabilities.home + probabilities.draw,
        tolerance=0.02,
        label="finishing first (a draw is resolved by the name fallback)",
    )


def test_expected_goal_difference_matches_the_model() -> None:
    matches, _ = synthetic_league(seed=16, n_teams=4, seasons=2)
    model = DixonColesModel().fit(matches)
    home, away = model.parameters.teams[0], model.parameters.teams[1]
    fixture = Fixture(home, away, matches.end + dt.timedelta(days=7))
    home_rate, away_rate = model.rates(fixture)
    projection = SeasonSimulator(model, seed=4).run(
        MatchLog(), [fixture], iterations=40000, teams=[home, away]
    )
    assert_close(
        projection.team(home).expected_goal_difference,
        home_rate - away_rate,
        tolerance=0.05,
        label="expected goal difference",
    )


def test_already_played_matches_are_carried_into_the_projection() -> None:
    model, played, remaining = _fitted_model()
    projection = SeasonSimulator(model, seed=6).run(played, remaining, iterations=300)
    table = LeagueTable.from_matches(played)
    for row in projection.rows:
        assert row.current_points == table.row(row.team).points
        assert row.expected_points >= row.current_points


def test_exact_tiebreakers_agree_with_the_fast_path_on_ordinary_rules() -> None:
    """The fast ranking and the full LeagueTable must not disagree materially."""
    model, played, remaining = _fitted_model(seed=8)
    fast = SeasonSimulator(model, seed=11).run(played, remaining[:20], iterations=400)
    exact = SeasonSimulator(model, seed=11, exact_tiebreakers=True).run(
        played, remaining[:20], iterations=400
    )
    for left, right in zip(
        sorted(fast.rows, key=lambda r: r.team),
        sorted(exact.rows, key=lambda r: r.team),
        strict=True,
    ):
        assert left.team == right.team
        assert_close(
            left.expected_points, right.expected_points, tolerance=1e-9, label=left.team
        )
        assert abs(left.title_probability - right.title_probability) < 0.02


def test_simulator_validates_its_input() -> None:
    model, played, remaining = _fitted_model()
    simulator = SeasonSimulator(model, seed=1, rules=LeagueRules.english())
    with assert_raises(ValueError, match="iterations"):
        simulator.run(played, remaining, iterations=0)
    with assert_raises(ValueError, match="no teams"):
        simulator.run(MatchLog(), [], teams=[])
    projection = simulator.run(played, remaining, iterations=50)
    with assert_raises(KeyError):
        projection.team("Nowhere United")
    with assert_raises(ValueError):
        projection.rows[0].top(0)
    with assert_raises(ValueError):
        projection.rows[0].bottom(999)


def test_rendering_includes_every_team() -> None:
    model, played, remaining = _fitted_model()
    projection = SeasonSimulator(model, seed=2).run(played, remaining, iterations=200)
    rendered = projection.render(champions_league=4, relegation=3)
    for row in projection.rows:
        assert row.team in rendered
    assert "Title" in rendered
    assert "seed 2" in rendered
