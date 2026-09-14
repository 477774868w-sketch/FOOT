"""Safeguarded one-dimensional root finding.

Used by the market de-vigging routines, where the unknown (Shin's insider
fraction, or the power-method exponent) is defined implicitly by a monotone
equation with no closed-form solution.
"""

from __future__ import annotations

import math
from collections.abc import Callable

__all__ = ["bisect_root", "expand_bracket"]


def bisect_root(
    func: Callable[[float], float],
    low: float,
    high: float,
    *,
    xtol: float = 1e-14,
    ftol: float = 1e-14,
    max_iter: int = 200,
) -> float:
    """Find ``x`` in ``[low, high]`` with ``func(x) == 0`` by bisection.

    Bisection is chosen deliberately over faster hybrids: the callers here have
    cheap, guaranteed-bracketed, monotone functions, and unconditional linear
    convergence beats the pathological cases of secant-based methods.

    Raises:
        ValueError: if the interval does not bracket a sign change.
    """
    if low > high:
        low, high = high, low
    f_low, f_high = func(low), func(high)
    if not (math.isfinite(f_low) and math.isfinite(f_high)):
        raise ValueError("function is not finite at the bracket endpoints")
    if abs(f_low) <= ftol:
        return low
    if abs(f_high) <= ftol:
        return high
    if f_low * f_high > 0.0:
        raise ValueError(
            f"interval [{low!r}, {high!r}] does not bracket a root "
            f"(f(low) = {f_low!r}, f(high) = {f_high!r})"
        )
    for _ in range(max_iter):
        mid = 0.5 * (low + high)
        f_mid = func(mid)
        if abs(f_mid) <= ftol or (high - low) <= xtol * max(1.0, abs(mid)):
            return mid
        if f_low * f_mid <= 0.0:
            high, f_high = mid, f_mid
        else:
            low, f_low = mid, f_mid
    return 0.5 * (low + high)


def expand_bracket(
    func: Callable[[float], float],
    low: float,
    high: float,
    *,
    growth: float = 2.0,
    max_expansions: int = 60,
    upper_limit: float = 1e12,
) -> tuple[float, float]:
    """Grow ``[low, high]`` upwards until it brackets a sign change."""
    if growth <= 1.0:
        raise ValueError("growth must exceed 1")
    f_low = func(low)
    for _ in range(max_expansions):
        f_high = func(high)
        if math.isfinite(f_high) and f_low * f_high <= 0.0:
            return (low, high)
        high = min(high * growth, upper_limit)
        if high >= upper_limit:
            break
    raise ValueError("failed to bracket a root within the allowed range")
