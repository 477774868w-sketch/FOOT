"""Numerically stable scalar primitives.

Football likelihoods are evaluated hundreds of thousands of times inside an
optimiser, on data where a single ``log(0.0)`` turns a fit into ``nan``.  The
helpers here are the defensive layer that keeps the models honest.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

__all__ = [
    "clamp",
    "log_factorial",
    "logsumexp",
    "normalise",
    "poisson_logpmf",
    "poisson_pmf",
    "softmax",
]

_LOG_FACTORIAL_CACHE: list[float] = [0.0, 0.0]
_MAX_CACHED_FACTORIAL = 512


def clamp(value: float, low: float, high: float) -> float:
    """Constrain ``value`` to ``[low, high]``.

    >>> clamp(1.5, 0.0, 1.0)
    1.0
    """
    if low > high:
        raise ValueError(f"empty interval [{low}, {high}]")
    return low if value < low else (high if value > high else value)


def log_factorial(n: int) -> float:
    """``log(n!)`` via a memoised table backed by :func:`math.lgamma`."""
    if n < 0:
        raise ValueError(f"log_factorial is undefined for n = {n}")
    if n < len(_LOG_FACTORIAL_CACHE):
        return _LOG_FACTORIAL_CACHE[n]
    if n > _MAX_CACHED_FACTORIAL:
        return math.lgamma(n + 1)
    while len(_LOG_FACTORIAL_CACHE) <= n:
        k = len(_LOG_FACTORIAL_CACHE)
        _LOG_FACTORIAL_CACHE.append(_LOG_FACTORIAL_CACHE[k - 1] + math.log(k))
    return _LOG_FACTORIAL_CACHE[n]


def poisson_logpmf(k: int, rate: float) -> float:
    """``log P(X = k)`` for ``X ~ Poisson(rate)``, valid at ``rate = 0``."""
    if k < 0:
        raise ValueError(f"k must be non-negative, got {k}")
    if rate < 0.0:
        raise ValueError(f"rate must be non-negative, got {rate}")
    if rate == 0.0:
        return 0.0 if k == 0 else -math.inf
    return k * math.log(rate) - rate - log_factorial(k)


def poisson_pmf(k: int, rate: float) -> float:
    """``P(X = k)`` for ``X ~ Poisson(rate)``, computed in log space."""
    return math.exp(poisson_logpmf(k, rate))


def logsumexp(values: Iterable[float]) -> float:
    """``log(sum(exp(v)))`` without overflow.

    >>> round(logsumexp([0.0, 0.0]), 12)
    0.69314718056
    """
    items = list(values)
    if not items:
        return -math.inf
    peak = max(items)
    if peak == -math.inf:
        return -math.inf
    if peak == math.inf:
        return math.inf
    total = math.fsum(math.exp(v - peak) for v in items)
    return peak + math.log(total)


def softmax(values: Sequence[float]) -> list[float]:
    """Numerically stable softmax over a finite sequence."""
    if not values:
        raise ValueError("softmax requires a non-empty sequence")
    peak = max(values)
    exps = [math.exp(v - peak) for v in values]
    total = math.fsum(exps)
    return [e / total for e in exps]


def normalise(weights: Sequence[float]) -> list[float]:
    """Rescale non-negative weights so that they sum to one."""
    if not weights:
        raise ValueError("cannot normalise an empty sequence")
    for w in weights:
        if not math.isfinite(w) or w < 0.0:
            raise ValueError(f"weights must be finite and non-negative, got {w!r}")
    total = math.fsum(weights)
    if total <= 0.0:
        raise ValueError("weights must not all be zero")
    return [w / total for w in weights]
