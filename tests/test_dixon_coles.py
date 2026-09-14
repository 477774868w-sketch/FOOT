"""The Dixon-Coles model: gradient, identifiability and parameter recovery.

These are the tests that decide whether the library can be trusted.  A wrong
gradient still "converges", to the wrong place; a mis-specified likelihood
still produces plausible-looking ratings.  The only defences are checking the
gradient against finite differences, checking the fitted parameters against a
known truth, and checking the exact identities maximum likelihood must satisfy.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Sequence

from foot.data.synthetic import synthetic_league
from foot.domain import Fixture, MatchLog
from foot.models.dixon_coles import (
    DixonColesModel,
    DixonColesParameters,
    _Design,
    _objective,
)
from foot.models.poisson import PoissonModel
from foot.numerics.optimize import check_gradient

from support import assert_close, assert_probability_vector, assert_raises


def _design_and_objective(
    model: DixonColesModel, matches: MatchLog
) -> tuple[_Design, list[float]]:
    """Build the internal design matrix so the raw likelihood can be probed."""
    design = model._build_design(matches, matches.teams, matches.end)
    start = model._initial_vector(matches, matches.teams, design)
    return design, start


def test_analytic_gradient_matches_finite_differences() -> None:
    """The single most important test in the library.

    The Dixon-Coles gradient is derived by hand, including the derivatives of
    the low-score correction with respect to both goal rates and to rho.  If
    any term is wrong the fit silently lands somewhere else, so it is checked
    against central differences at several points, not only at the optimum.
    """
    matches, _ = synthetic_league(seed=11, n_teams=8, seasons=2)
    for half_life in (None, 120.0):
        model = DixonColesModel(half_life_days=half_life)
        design, start = _design_and_objective(model, matches)

        def objective(
            vector: Sequence[float], design: _Design = design
        ) -> tuple[float, list[float]]:
            return _objective(vector, design)

        # Probe several points, including one near the optimum: a gradient can
        # be wrong in a direction the starting point happens not to explore.
        fitted = model.fit(matches).parameters
        near_optimum = [
            fitted.home_advantage + 0.02,
            *(a + 0.02 for a in fitted.attack),
            *(d + 0.02 for d in fitted.defence),
        ]
        if design.include_rho:
            near_optimum.append(fitted.rho + 0.02)
        probes = [start, [v + 0.15 for v in start], [v * 0.6 for v in start], near_optimum]
        for probe in probes:
            error = check_gradient(objective, probe)
            assert error < 1e-6, f"half_life={half_life}: relative error {error:.3e}"


def test_analytic_gradient_is_correct_with_rho_disabled() -> None:
    matches, _ = synthetic_league(seed=12, n_teams=6)
    model = PoissonModel()
    design, start = _design_and_objective(model, matches)
    assert not design.include_rho
    assert check_gradient(lambda v: _objective(v, design), start) < 1e-6


def test_analytic_gradient_is_correct_with_a_ridge_penalty() -> None:
    matches, _ = synthetic_league(seed=13, n_teams=6)
    model = DixonColesModel(ridge=0.5)
    design, start = _design_and_objective(model, matches)
    assert check_gradient(lambda v: _objective(v, design), [v + 0.2 for v in start]) < 1e-6


def test_the_fit_converges_and_reports_honestly() -> None:
    matches, _ = synthetic_league(seed=21, seasons=2)
    model = DixonColesModel(half_life_days=200).fit(matches)
    diagnostics = model.parameters.diagnostics
    assert diagnostics.converged, diagnostics.message
    assert diagnostics.matches == len(matches)
    assert diagnostics.parameters == 1 + 2 * len(matches.teams) + 1
    assert diagnostics.gradient_norm < 1e-3
    assert 0.0 < diagnostics.effective_matches < diagnostics.matches  # decay bites
    assert math.isfinite(diagnostics.aic) and math.isfinite(diagnostics.bic)


def test_parameter_recovery_improves_with_more_data() -> None:
    """Fit data generated from known parameters and watch the error shrink.

    With a fixed number of parameters and a growing sample, maximum likelihood
    is consistent: the estimates must converge on the truth.  Anything else
    means the likelihood being maximised is not the one that generated the data.
    """
    errors = []
    for seasons in (2, 8, 30):
        matches, truth = synthetic_league(seed=31, seasons=seasons)
        parameters = DixonColesModel().fit(matches).parameters
        attack_error = math.sqrt(
            math.fsum(
                (parameters.attack_of(team) - truth.attack_of(team)) ** 2
                for team in parameters.teams
            )
            / len(parameters.teams)
        )
        errors.append(attack_error)
    assert errors[0] > errors[1] > errors[2], errors
    assert errors[-1] < 0.05, f"30 seasons should pin the attack ratings: {errors[-1]:.4f}"


def test_home_advantage_and_rho_are_recovered() -> None:
    matches, truth = synthetic_league(seed=41, seasons=30)
    parameters = DixonColesModel().fit(matches).parameters
    assert_close(
        parameters.home_advantage, truth.home_advantage, tolerance=0.03, label="home advantage"
    )
    assert_close(parameters.rho, truth.rho, tolerance=0.03, label="rho")


def test_ratings_are_reported_in_the_canonical_gauge() -> None:
    """Adding a constant to every attack *and* every defence changes nothing.

    The likelihood is exactly flat along that direction, so a fit alone does
    not pin the ratings down; the convention mean(attack) == 0 does.  Without
    it, two fits of the same data are not comparable.
    """
    matches, _ = synthetic_league(seed=51)
    parameters = DixonColesModel().fit(matches).parameters
    assert_close(
        math.fsum(parameters.attack) / len(parameters.attack), 0.0,
        tolerance=1e-12, label="mean attack",
    )


def test_predictions_are_invariant_under_the_gauge_shift() -> None:
    matches, _ = synthetic_league(seed=52)
    original = DixonColesModel().fit(matches)
    parameters = original.parameters
    shift = 0.37
    shifted = DixonColesParameters(
        teams=parameters.teams,
        attack=tuple(a + shift for a in parameters.attack),
        defence=tuple(d + shift for d in parameters.defence),
        home_advantage=parameters.home_advantage,
        rho=parameters.rho,
        reference_date=parameters.reference_date,
        half_life_days=parameters.half_life_days,
        diagnostics=parameters.diagnostics,
    )
    fixture = Fixture(parameters.teams[0], parameters.teams[1], matches.end)
    before = parameters.rates(fixture.home, fixture.away)
    after = shifted.rates(fixture.home, fixture.away)
    assert_close(before[0], after[0], tolerance=1e-12, label="home rate")
    assert_close(before[1], after[1], tolerance=1e-12, label="away rate")


def test_poisson_fit_reproduces_the_observed_goal_totals_exactly() -> None:
    """An exact identity of the Poisson maximum likelihood estimate.

    With a log link and no correction, the score equation for a team's attack
    parameter is ``sum(observed - fitted) == 0`` over that team's matches.  So
    the fitted model must reproduce, to machine precision, how many goals each
    team actually scored and conceded, and how many goals were scored at home.
    Nothing but a correct likelihood and a converged optimiser does that.
    """
    matches, _ = synthetic_league(seed=61, seasons=2)
    parameters = PoissonModel().fit(matches).parameters

    scored: dict[str, float] = dict.fromkeys(parameters.teams, 0.0)
    conceded: dict[str, float] = dict.fromkeys(parameters.teams, 0.0)
    fitted_scored: dict[str, float] = dict.fromkeys(parameters.teams, 0.0)
    fitted_conceded: dict[str, float] = dict.fromkeys(parameters.teams, 0.0)
    observed_home = fitted_home = 0.0

    for match in matches:
        home_rate, away_rate = parameters.rates(match.home, match.away)
        scored[match.home] += match.score.home
        scored[match.away] += match.score.away
        conceded[match.home] += match.score.away
        conceded[match.away] += match.score.home
        fitted_scored[match.home] += home_rate
        fitted_scored[match.away] += away_rate
        fitted_conceded[match.home] += away_rate
        fitted_conceded[match.away] += home_rate
        observed_home += match.score.home
        fitted_home += home_rate

    assert_close(fitted_home, observed_home, tolerance=1e-3, label="total home goals")
    for team in parameters.teams:
        assert_close(
            fitted_scored[team], scored[team], tolerance=1e-3, label=f"{team} goals for"
        )
        assert_close(
            fitted_conceded[team], conceded[team], tolerance=1e-3, label=f"{team} goals against"
        )


def test_the_low_score_correction_cannot_reduce_the_likelihood() -> None:
    """Poisson is Dixon-Coles with rho pinned at zero, so it cannot fit better."""
    matches, _ = synthetic_league(seed=71, seasons=3)
    dixon_coles = DixonColesModel().fit(matches).parameters
    poisson = PoissonModel().fit(matches).parameters
    assert dixon_coles.diagnostics.log_likelihood >= poisson.diagnostics.log_likelihood - 1e-6
    assert poisson.rho == 0.0


def test_time_decay_reduces_the_effective_sample() -> None:
    matches, _ = synthetic_league(seed=81, seasons=3)
    undecayed = DixonColesModel().fit(matches).parameters.diagnostics
    decayed = DixonColesModel(half_life_days=90).fit(matches).parameters.diagnostics
    assert_close(undecayed.effective_matches, float(len(matches)), label="no decay")
    assert decayed.effective_matches < 0.4 * len(matches)
    assert decayed.matches == undecayed.matches  # the data itself is unchanged


def test_a_neutral_venue_removes_the_home_advantage() -> None:
    matches, _ = synthetic_league(seed=91)
    model = DixonColesModel().fit(matches)
    home, away = model.parameters.teams[:2]
    normal = model.rates(Fixture(home, away, matches.end))
    neutral = model.rates(Fixture(home, away, matches.end, neutral=True))
    assert_close(
        math.log(normal[0]) - math.log(neutral[0]),
        model.parameters.home_advantage,
        tolerance=1e-12,
        label="home advantage",
    )
    assert_close(normal[1], neutral[1], tolerance=1e-12, label="away rate unchanged")


def test_predictions_are_valid_distributions() -> None:
    matches, _ = synthetic_league(seed=101)
    model = DixonColesModel(half_life_days=150).fit(matches)
    for home in model.parameters.teams[:4]:
        for away in model.parameters.teams[:4]:
            if home == away:
                continue
            fixture = Fixture(home, away, matches.end)
            assert_probability_vector(model.predict(fixture).as_tuple())
            matrix = model.score_matrix(fixture)
            assert_probability_vector([p for row in matrix.grid for p in row], tolerance=1e-12)


def test_the_model_refuses_to_guess_about_unknown_teams() -> None:
    matches, _ = synthetic_league(seed=111)
    model = DixonColesModel().fit(matches)
    with assert_raises(KeyError, match="unknown team"):
        model.predict(Fixture("Newly Promoted FC", model.parameters.teams[0], matches.end))


def test_fitting_validates_its_input() -> None:
    with assert_raises(ValueError, match="empty"):
        DixonColesModel().fit(MatchLog())
    with assert_raises(RuntimeError, match="not fitted"):
        _ = DixonColesModel().parameters
    with assert_raises(ValueError, match="half_life"):
        DixonColesModel(half_life_days=-5.0)
    with assert_raises(ValueError, match="ridge"):
        DixonColesModel(ridge=-1.0)
    with assert_raises(ValueError, match="baseline"):
        PoissonModel(low_score_correction=True)
    matches, _ = synthetic_league(seed=121, n_teams=2)
    single = MatchLog([matches[0]])
    assert DixonColesModel().fit(single).parameters.diagnostics.matches == 1


def test_fit_is_independent_of_input_ordering() -> None:
    matches, _ = synthetic_league(seed=131)
    shuffled = MatchLog(list(matches)[::-1])
    a = DixonColesModel(half_life_days=200).fit(matches).parameters
    b = DixonColesModel(half_life_days=200).fit(shuffled).parameters
    assert a.teams == b.teams
    for team in a.teams:
        assert_close(a.attack_of(team), b.attack_of(team), tolerance=1e-9, label=team)


def test_parameters_round_trip_through_a_dictionary() -> None:
    matches, _ = synthetic_league(seed=141)
    parameters = DixonColesModel(half_life_days=180).fit(matches).parameters
    payload = parameters.to_dict()
    assert payload["teams"] == list(parameters.teams)
    assert payload["reference_date"] == parameters.reference_date.isoformat()
    rho = payload["rho"]
    assert isinstance(rho, float)
    assert_close(rho, parameters.rho, label="rho")
    assert isinstance(dt.date.fromisoformat(str(payload["reference_date"])), dt.date)


def test_ratings_table_is_ordered_by_strength() -> None:
    matches, _ = synthetic_league(seed=151)
    model = DixonColesModel().fit(matches)
    rows = model.ratings_table()
    strengths = [row.strength for row in rows]
    assert strengths == sorted(strengths, reverse=True)
    assert len(rows) == len(model.parameters.teams)


def test_the_ridge_penalty_shrinks_ratings_without_polluting_the_likelihood() -> None:
    """A penalised fit must still report the *likelihood*, not likelihood minus penalty.

    Otherwise AIC, BIC and every model comparison silently mix a modelling
    choice into a goodness-of-fit number.  Shrinkage must also actually shrink,
    and, being a worse fit by construction, must score no better.
    """
    matches, _ = synthetic_league(seed=161, seasons=2)
    plain = DixonColesModel(half_life_days=200).fit(matches).parameters
    shrunk = DixonColesModel(half_life_days=200, ridge=5.0).fit(matches).parameters

    spread_plain = math.sqrt(math.fsum(a * a for a in plain.attack) / len(plain.attack))
    spread_shrunk = math.sqrt(math.fsum(a * a for a in shrunk.attack) / len(shrunk.attack))
    assert spread_shrunk < spread_plain, "the ridge did not shrink the ratings"

    # The unpenalised fit maximises the likelihood, so it must score at least as
    # well on both the plain and the time-weighted versions of it.
    assert plain.diagnostics.log_likelihood >= shrunk.diagnostics.log_likelihood
    assert (
        plain.diagnostics.weighted_log_likelihood
        >= shrunk.diagnostics.weighted_log_likelihood
    )
    # And the penalty itself never leaks into the reported number.
    assert shrunk.diagnostics.weighted_log_likelihood > -math.inf
    assert abs(
        shrunk.diagnostics.weighted_log_likelihood
        - plain.diagnostics.weighted_log_likelihood
    ) < 0.1 * abs(plain.diagnostics.weighted_log_likelihood)
