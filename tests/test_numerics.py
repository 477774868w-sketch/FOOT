"""The in-house numerics: optimiser, root finder and stable primitives."""

from __future__ import annotations

import math
from collections.abc import Sequence

from foot.numerics.optimize import (
    check_gradient,
    minimize,
    minimize_with_gradient,
    numerical_gradient,
)
from foot.numerics.roots import bisect_root, expand_bracket
from foot.numerics.stable import (
    clamp,
    log_factorial,
    logsumexp,
    normalise,
    poisson_logpmf,
    poisson_pmf,
    softmax,
)

from support import assert_close, assert_probability_vector, assert_raises


def rosenbrock(x: Sequence[float]) -> tuple[float, list[float]]:
    """The classic ill-conditioned banana valley, with its exact gradient."""
    value = math.fsum(
        100.0 * (x[i + 1] - x[i] ** 2) ** 2 + (1.0 - x[i]) ** 2 for i in range(len(x) - 1)
    )
    gradient = [0.0] * len(x)
    for i in range(len(x) - 1):
        gradient[i] += -400.0 * x[i] * (x[i + 1] - x[i] ** 2) - 2.0 * (1.0 - x[i])
        gradient[i + 1] += 200.0 * (x[i + 1] - x[i] ** 2)
    return value, gradient


def test_optimizer_solves_rosenbrock_in_many_dimensions() -> None:
    for n in (2, 5, 20, 100):
        start = [-1.2 if i % 2 == 0 else 1.0 for i in range(n)]
        result = minimize_with_gradient(rosenbrock, start, max_iterations=5000)
        assert result.converged, f"n={n}: {result.message}"
        assert result.fun < 1e-10, f"n={n}: f={result.fun}"
        assert max(abs(v - 1.0) for v in result.x) < 1e-4, f"n={n}"


def test_optimizer_solves_other_standard_test_functions() -> None:
    def beale(x: Sequence[float]) -> float:
        a, b = x
        return (
            (1.5 - a + a * b) ** 2
            + (2.25 - a + a * b * b) ** 2
            + (2.625 - a + a * b**3) ** 2
        )

    def booth(x: Sequence[float]) -> float:
        a, b = x
        return (a + 2 * b - 7) ** 2 + (2 * a + b - 5) ** 2

    beale_result = minimize(beale, [1.0, 0.5], max_iterations=5000)
    assert_close(beale_result.x[0], 3.0, tolerance=1e-4, label="beale x")
    assert_close(beale_result.x[1], 0.5, tolerance=1e-4, label="beale y")

    booth_result = minimize(booth, [0.0, 0.0])
    assert_close(booth_result.x[0], 1.0, tolerance=1e-6, label="booth x")
    assert_close(booth_result.x[1], 3.0, tolerance=1e-6, label="booth y")


def test_optimizer_backs_off_from_an_infeasible_region() -> None:
    """A barrier of +inf must be handled by the line search, not by a crash."""

    def barrier(x: Sequence[float]) -> tuple[float, list[float]]:
        if x[0] >= 1.0:  # infeasible half-space
            return math.inf, [0.0] * len(x)
        return (x[0] - 5.0) ** 2, [2.0 * (x[0] - 5.0)]

    result = minimize_with_gradient(barrier, [0.0])
    assert math.isfinite(result.fun)
    assert result.x[0] < 1.0, "the optimiser walked into the infeasible region"
    assert result.x[0] > 0.9, "the optimiser failed to approach the barrier"


def test_optimizer_reports_an_infinite_starting_point_honestly() -> None:
    result = minimize_with_gradient(lambda _x: (math.inf, [0.0]), [0.0])
    assert not result.converged
    assert "not finite" in result.message


def test_optimizer_validates_its_arguments() -> None:
    with assert_raises(ValueError, match="c1"):
        minimize_with_gradient(rosenbrock, [0.0, 0.0], c1=0.9, c2=0.1)
    with assert_raises(ValueError, match="memory"):
        minimize_with_gradient(rosenbrock, [0.0, 0.0], memory=0)
    with assert_raises(ValueError, match="x0"):
        minimize_with_gradient(rosenbrock, [])


def test_analytic_and_numerical_gradients_agree() -> None:
    for point in ([0.3, -0.7], [-2.0, 3.5, 0.1, 0.9], [1.7, -0.4], [-1.2, 1.0]):
        assert check_gradient(rosenbrock, point) < 1e-9, point


def test_gradient_check_degrades_gracefully_at_a_stationary_point() -> None:
    """At the minimum the true gradient is zero, so `check_gradient`'s relative
    scale collapses to an absolute one and the central-difference truncation
    error (order h^2) becomes visible.  That floor is a property of finite
    differences, not of the gradient, so the tolerance here is deliberately
    looser than the one used at generic points.
    """
    assert check_gradient(rosenbrock, [1.0, 1.0]) < 1e-7


def test_numerical_gradient_is_second_order_accurate() -> None:
    def f(x: Sequence[float]) -> float:
        return math.sin(x[0]) * math.exp(x[1])

    point = [0.7, -0.3]
    exact = [math.cos(0.7) * math.exp(-0.3), math.sin(0.7) * math.exp(-0.3)]
    estimate = numerical_gradient(f, point)
    for got, want in zip(estimate, exact, strict=True):
        assert_close(got, want, tolerance=1e-9, label="numerical gradient")


def test_bisection_finds_roots_and_refuses_bad_brackets() -> None:
    assert_close(bisect_root(lambda x: x * x - 2.0, 0.0, 5.0), math.sqrt(2.0), tolerance=1e-12)
    assert_close(bisect_root(math.cos, 0.0, 3.0), math.pi / 2, tolerance=1e-12)
    assert bisect_root(lambda x: x, -1.0, 1.0) == 0.0
    with assert_raises(ValueError, match="bracket"):
        bisect_root(lambda x: x * x + 1.0, -1.0, 1.0)


def test_bracket_expansion() -> None:
    low, high = expand_bracket(lambda x: 10.0 - x, 0.0, 1.0)
    assert low <= 10.0 <= high
    with assert_raises(ValueError, match="bracket"):
        expand_bracket(lambda _x: 1.0, 0.0, 1.0)


def test_poisson_pmf_is_a_distribution() -> None:
    for rate in (0.0, 0.25, 1.5, 4.0, 12.0):
        mass = math.fsum(poisson_pmf(k, rate) for k in range(200))
        assert_close(mass, 1.0, tolerance=1e-12, label=f"poisson({rate}) mass")
        mean = math.fsum(k * poisson_pmf(k, rate) for k in range(200))
        assert_close(mean, rate, tolerance=1e-9, label=f"poisson({rate}) mean")
    assert poisson_pmf(0, 0.0) == 1.0
    assert poisson_logpmf(3, 0.0) == -math.inf
    with assert_raises(ValueError):
        poisson_logpmf(-1, 1.0)
    with assert_raises(ValueError):
        poisson_logpmf(1, -1.0)


def test_log_factorial_matches_lgamma() -> None:
    for n in (0, 1, 2, 7, 40, 511, 512, 513, 5000):
        assert_close(log_factorial(n), math.lgamma(n + 1), tolerance=1e-9, label=f"log {n}!")
    with assert_raises(ValueError):
        log_factorial(-1)


def test_logsumexp_is_overflow_safe() -> None:
    assert_close(logsumexp([0.0, 0.0]), math.log(2.0), label="logsumexp")
    assert logsumexp([]) == -math.inf
    assert logsumexp([-math.inf, -math.inf]) == -math.inf
    huge = [1000.0, 1000.0]
    assert_close(logsumexp(huge), 1000.0 + math.log(2.0), tolerance=1e-9, label="huge")


def test_softmax_and_normalise() -> None:
    assert_probability_vector(softmax([1.0, 2.0, 3.0]))
    assert_probability_vector(softmax([900.0, 900.0]))
    assert_probability_vector(normalise([2.0, 1.0, 1.0]))
    assert_close(normalise([2.0, 1.0, 1.0])[0], 0.5, label="normalise")
    with assert_raises(ValueError):
        normalise([0.0, 0.0])
    with assert_raises(ValueError):
        normalise([-1.0, 2.0])
    with assert_raises(ValueError):
        softmax([])


def test_clamp() -> None:
    assert clamp(1.5, 0.0, 1.0) == 1.0
    assert clamp(-1.5, 0.0, 1.0) == 0.0
    assert clamp(0.5, 0.0, 1.0) == 0.5
    with assert_raises(ValueError):
        clamp(0.5, 1.0, 0.0)
