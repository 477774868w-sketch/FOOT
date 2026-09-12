"""Self-contained numerical building blocks: optimisation, roots, stable maths."""

from foot.numerics.optimize import (
    OptimizeResult,
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

__all__ = [
    "OptimizeResult",
    "bisect_root",
    "check_gradient",
    "clamp",
    "expand_bracket",
    "log_factorial",
    "logsumexp",
    "minimize",
    "minimize_with_gradient",
    "normalise",
    "numerical_gradient",
    "poisson_logpmf",
    "poisson_pmf",
    "softmax",
]
