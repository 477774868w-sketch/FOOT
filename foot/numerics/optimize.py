"""A limited-memory BFGS optimiser with a strong-Wolfe line search.

Written from first principles so that :mod:`foot` has *no* runtime dependency
and so that every numerical guarantee it relies on is auditable in one file.

The implementation follows Nocedal & Wright, *Numerical Optimization* (2nd ed.):

* Algorithm 7.4 — two-loop recursion for the L-BFGS direction;
* Algorithm 3.5 — line search satisfying the strong Wolfe conditions;
* Algorithm 3.6 — the ``zoom`` refinement, here with safeguarded quadratic
  interpolation and a bisection fallback.

Two extensions matter in practice and are implemented here:

* **Non-finite objective values are first-class.**  Constrained likelihoods
  (such as the Dixon-Coles low-score correction) are ``+inf`` outside their
  feasible region.  Because ``inf`` compares greater than any finite value, the
  textbook sufficient-decrease test rejects such steps automatically and the
  line search simply backs off — no special-casing, no ``nan`` propagation.
* **Flat directions are harmless.**  Search directions are built exclusively
  from past steps and gradients, so a likelihood that is exactly invariant
  along some direction (as attack/defence ratings are) never causes drift: the
  optimiser provably never moves along it.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

__all__ = [
    "OptimizeResult",
    "check_gradient",
    "minimize",
    "minimize_with_gradient",
    "numerical_gradient",
]

Objective = Callable[[Sequence[float]], float]
Gradient = Callable[[Sequence[float]], Sequence[float]]
ObjectiveWithGradient = Callable[[Sequence[float]], tuple[float, Sequence[float]]]

_CUBE_ROOT_EPS = 6.055454452393343e-06  # sys.float_info.epsilon ** (1 / 3)


@dataclass(frozen=True, slots=True)
class OptimizeResult:
    """Outcome of a minimisation."""

    x: tuple[float, ...]
    fun: float
    gradient: tuple[float, ...] = field(repr=False)
    gradient_norm: float = 0.0
    iterations: int = 0
    evaluations: int = 0
    converged: bool = False
    message: str = ""

    def __str__(self) -> str:
        status = "converged" if self.converged else "STOPPED"
        return (
            f"{status} after {self.iterations} iterations / {self.evaluations} evaluations: "
            f"f = {self.fun:.10g}, |grad|inf = {self.gradient_norm:.3g} ({self.message})"
        )


# --------------------------------------------------------------------------- #
# Small dense-vector helpers.  Explicit loops beat generic machinery at the
# problem sizes here (tens to low hundreds of parameters).
# --------------------------------------------------------------------------- #


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    total = 0.0
    for i in range(len(a)):
        total += a[i] * b[i]
    return total


def _axpy(alpha: float, x: Sequence[float], y: Sequence[float]) -> list[float]:
    """Return ``alpha * x + y``."""
    return [alpha * x[i] + y[i] for i in range(len(x))]


def _max_abs(v: Sequence[float]) -> float:
    return max((abs(component) for component in v), default=0.0)


def _sanitise(value: float) -> float:
    """Map ``nan`` to ``+inf`` so that comparisons order it as 'worst'."""
    return math.inf if math.isnan(value) else value


def numerical_gradient(
    fun: Objective, x: Sequence[float], *, step: float | None = None
) -> list[float]:
    """Central-difference gradient, accurate to ``O(h^2)``.

    The default step ``eps**(1/3) * max(1, |x_i|)`` balances truncation against
    round-off; it recovers roughly ten correct significant digits.
    """
    base = list(x)
    grad = [0.0] * len(base)
    for i in range(len(base)):
        h = step if step is not None else _CUBE_ROOT_EPS * max(1.0, abs(base[i]))
        original = base[i]
        base[i] = original + h
        forward = fun(base)
        base[i] = original - h
        backward = fun(base)
        base[i] = original
        grad[i] = (forward - backward) / (2.0 * h)
    return grad


def check_gradient(
    objective: ObjectiveWithGradient, x: Sequence[float], *, step: float | None = None
) -> float:
    """Largest relative discrepancy between the analytic and numeric gradients.

    A correct hand-derived gradient scores below ``1e-6``.  This is exposed as
    public API because it is the single most valuable test one can write about
    a likelihood.
    """
    _, analytic = objective(x)
    numeric = numerical_gradient(lambda v: objective(v)[0], x, step=step)
    worst = 0.0
    for a, n in zip(analytic, numeric, strict=True):
        scale = max(1.0, abs(a), abs(n))
        worst = max(worst, abs(a - n) / scale)
    return worst


# --------------------------------------------------------------------------- #
# Line search
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class _LineSearchOutcome:
    alpha: float
    fun: float
    x: list[float]
    gradient: list[float]
    evaluations: int
    ok: bool


def _strong_wolfe(
    objective: ObjectiveWithGradient,
    x: Sequence[float],
    direction: Sequence[float],
    *,
    f0: float,
    g0: Sequence[float],
    alpha_init: float,
    c1: float,
    c2: float,
    max_evaluations: int,
) -> _LineSearchOutcome:
    """Find a step satisfying the strong Wolfe conditions along ``direction``."""
    dphi0 = _dot(g0, direction)
    evaluations = 0
    best = _LineSearchOutcome(0.0, f0, list(x), list(g0), 0, False)

    def probe(alpha: float) -> tuple[float, list[float], list[float], float]:
        nonlocal evaluations
        point = _axpy(alpha, direction, x)
        value, grad = objective(point)
        evaluations += 1
        return _sanitise(value), point, list(grad), _dot(grad, direction)

    def accept(alpha: float, f: float, point: list[float], grad: list[float]) -> _LineSearchOutcome:
        return _LineSearchOutcome(alpha, f, point, grad, evaluations, True)

    def zoom(
        lo: float, hi: float, f_lo: float, dphi_lo: float, f_hi: float
    ) -> _LineSearchOutcome:
        best_alpha, best_f, best_x, best_g = lo, f_lo, list(x), list(g0)
        if lo > 0.0:
            best_f, best_x, best_g, _ = probe(lo)
        for _ in range(64):
            if evaluations >= max_evaluations:
                break
            width = hi - lo
            if abs(width) < 1e-16 * max(1.0, abs(hi)):
                break
            # Safeguarded quadratic interpolation of phi through
            # (lo, f_lo, dphi_lo) and (hi, f_hi); bisect when it misbehaves.
            trial = lo + 0.5 * width
            if math.isfinite(f_hi) and math.isfinite(f_lo):
                denominator = 2.0 * (f_hi - f_lo - dphi_lo * width)
                if abs(denominator) > 1e-300:
                    candidate = lo - dphi_lo * width * width / denominator
                    low_edge, high_edge = (lo, hi) if width > 0 else (hi, lo)
                    margin = 0.1 * abs(width)
                    if low_edge + margin <= candidate <= high_edge - margin:
                        trial = candidate
            f_trial, x_trial, g_trial, dphi_trial = probe(trial)
            if f_trial > f0 + c1 * trial * dphi0 or f_trial >= f_lo:
                hi, f_hi = trial, f_trial
            else:
                if abs(dphi_trial) <= -c2 * dphi0:
                    return accept(trial, f_trial, x_trial, g_trial)
                if dphi_trial * (hi - lo) >= 0.0:
                    hi, f_hi = lo, f_lo
                lo, f_lo, dphi_lo = trial, f_trial, dphi_trial
                best_alpha, best_f, best_x, best_g = trial, f_trial, x_trial, g_trial
        if best_alpha > 0.0 and best_f < f0:
            return _LineSearchOutcome(best_alpha, best_f, best_x, best_g, evaluations, True)
        return _LineSearchOutcome(0.0, f0, list(x), list(g0), evaluations, False)

    if dphi0 >= 0.0:  # not a descent direction; the caller must reset
        return best

    alpha_prev, f_prev, dphi_prev = 0.0, f0, dphi0
    alpha = alpha_init
    for iteration in range(1, 65):
        if evaluations >= max_evaluations:
            break
        f_alpha, x_alpha, g_alpha, dphi_alpha = probe(alpha)
        if f_alpha > f0 + c1 * alpha * dphi0 or (iteration > 1 and f_alpha >= f_prev):
            return zoom(alpha_prev, alpha, f_prev, dphi_prev, f_alpha)
        if abs(dphi_alpha) <= -c2 * dphi0:
            return accept(alpha, f_alpha, x_alpha, g_alpha)
        if dphi_alpha >= 0.0:
            return zoom(alpha, alpha_prev, f_alpha, dphi_alpha, f_prev)
        alpha_prev, f_prev, dphi_prev = alpha, f_alpha, dphi_alpha
        alpha = min(alpha * 2.0, 1e10)
    return best


# --------------------------------------------------------------------------- #
# L-BFGS
# --------------------------------------------------------------------------- #


def minimize_with_gradient(
    objective: ObjectiveWithGradient,
    x0: Sequence[float],
    *,
    memory: int = 12,
    max_iterations: int = 1000,
    max_evaluations: int = 20000,
    gtol: float = 1e-8,
    ftol: float = 1e-12,
    c1: float = 1e-4,
    c2: float = 0.9,
) -> OptimizeResult:
    """Minimise ``objective`` (returning ``(value, gradient)``) from ``x0``.

    Args:
        memory: number of curvature pairs retained; 5-20 is the useful range.
        gtol: convergence when the sup-norm of the gradient falls below this.
        ftol: convergence when the relative decrease in the objective does.

    Returns:
        An :class:`OptimizeResult` that always reports honestly whether the
        convergence criteria were met.
    """
    if not 0.0 < c1 < c2 < 1.0:
        raise ValueError(f"require 0 < c1 < c2 < 1, got c1={c1}, c2={c2}")
    if memory < 1:
        raise ValueError("memory must be at least 1")
    if not x0:
        raise ValueError("x0 must not be empty")

    x = [float(v) for v in x0]
    value, grad_seq = objective(x)
    value = _sanitise(value)
    grad = [float(g) for g in grad_seq]
    evaluations = 1

    if not math.isfinite(value):
        return OptimizeResult(
            tuple(x), value, tuple(grad), _max_abs(grad), 0, evaluations, False,
            "objective is not finite at the starting point",
        )

    s_history: list[list[float]] = []
    y_history: list[list[float]] = []
    rho_history: list[float] = []
    message = "maximum iterations reached"
    converged = False
    iteration = 0

    for iteration in range(1, max_iterations + 1):  # noqa: B007 - reported below
        gradient_norm = _max_abs(grad)
        if gradient_norm <= gtol:
            converged, message = True, "gradient tolerance reached"
            break
        if evaluations >= max_evaluations:
            message = "evaluation budget exhausted"
            break

        direction = _two_loop_direction(grad, s_history, y_history, rho_history)
        if _dot(grad, direction) >= 0.0:  # numerically lost descent: restart
            s_history.clear()
            y_history.clear()
            rho_history.clear()
            direction = [-g for g in grad]

        alpha_init = 1.0
        if not s_history:
            scale = math.fsum(abs(g) for g in grad)
            alpha_init = min(1.0, 1.0 / scale) if scale > 0.0 else 1.0

        search = _strong_wolfe(
            objective,
            x,
            direction,
            f0=value,
            g0=grad,
            alpha_init=alpha_init,
            c1=c1,
            c2=c2,
            max_evaluations=max_evaluations - evaluations,
        )
        evaluations += search.evaluations
        if not search.ok:
            if s_history:  # a stale Hessian model is the usual culprit: restart once
                s_history.clear()
                y_history.clear()
                rho_history.clear()
                continue
            message = "line search failed to find a sufficient decrease"
            break

        step = [search.x[i] - x[i] for i in range(len(x))]
        gradient_delta = [search.gradient[i] - grad[i] for i in range(len(x))]
        previous_value = value
        x, value, grad = search.x, search.fun, search.gradient

        if abs(previous_value - value) <= ftol * max(1.0, abs(previous_value)):
            converged, message = True, "objective tolerance reached"
            break

        sy = _dot(step, gradient_delta)
        ss = _dot(step, step)
        yy = _dot(gradient_delta, gradient_delta)
        if sy > 1e-8 * math.sqrt(max(ss, 0.0) * max(yy, 0.0)):  # curvature condition
            s_history.append(step)
            y_history.append(gradient_delta)
            rho_history.append(1.0 / sy)
            if len(s_history) > memory:
                s_history.pop(0)
                y_history.pop(0)
                rho_history.pop(0)
    else:
        gradient_norm = _max_abs(grad)
        if gradient_norm <= gtol:
            converged, message = True, "gradient tolerance reached"

    return OptimizeResult(
        x=tuple(x),
        fun=value,
        gradient=tuple(grad),
        gradient_norm=_max_abs(grad),
        iterations=iteration,
        evaluations=evaluations,
        converged=converged,
        message=message,
    )


def _two_loop_direction(
    grad: Sequence[float],
    s_history: Sequence[Sequence[float]],
    y_history: Sequence[Sequence[float]],
    rho_history: Sequence[float],
) -> list[float]:
    """L-BFGS two-loop recursion (Nocedal & Wright, Algorithm 7.4)."""
    q = list(grad)
    alphas: list[float] = []
    for i in range(len(s_history) - 1, -1, -1):
        alpha = rho_history[i] * _dot(s_history[i], q)
        alphas.append(alpha)
        q = _axpy(-alpha, y_history[i], q)
    alphas.reverse()

    if s_history:
        last_s, last_y = s_history[-1], y_history[-1]
        yy = _dot(last_y, last_y)
        gamma = (_dot(last_s, last_y) / yy) if yy > 0.0 else 1.0
    else:
        gamma = 1.0
    r = [gamma * component for component in q]

    for i in range(len(s_history)):
        beta = rho_history[i] * _dot(y_history[i], r)
        r = _axpy(alphas[i] - beta, s_history[i], r)
    return [-component for component in r]


def minimize(
    fun: Objective,
    x0: Sequence[float],
    *,
    jac: Gradient | None = None,
    **kwargs: float | int,
) -> OptimizeResult:
    """Convenience wrapper around :func:`minimize_with_gradient`.

    When ``jac`` is omitted the gradient is estimated by central differences,
    which costs ``2n`` extra evaluations per step — fine for exploration, but
    every model in :mod:`foot` supplies an analytic gradient instead.
    """
    if jac is None:

        def objective(x: Sequence[float]) -> tuple[float, Sequence[float]]:
            return fun(x), numerical_gradient(fun, x)

    else:

        def objective(x: Sequence[float]) -> tuple[float, Sequence[float]]:
            return fun(x), jac(x)

    return minimize_with_gradient(objective, x0, **kwargs)  # type: ignore[arg-type]
