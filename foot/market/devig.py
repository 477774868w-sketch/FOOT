"""Removing the bookmaker's margin from quoted odds.

Quoted prices imply probabilities that sum to more than one.  Recovering the
bookmaker's *true* beliefs means deciding how the excess is distributed across
outcomes, and that decision matters: the methods disagree most exactly where
value is usually claimed, on longshots.

Four methods are implemented, in increasing order of sophistication:

``MULTIPLICATIVE``
    Divide through by the book sum.  Assumes the margin is proportional to
    probability.  It is the standard choice, and it is known to over-price
    longshots because it preserves the favourite-longshot bias intact.

``ADDITIVE``
    Subtract an equal share of the margin from each outcome.  The opposite
    assumption, and it can drive extreme longshots negative.

``POWER``
    Find the exponent ``k`` with ``sum(pi_i ** k) == 1``.  A one-parameter
    family that interpolates between the two extremes.

``SHIN``
    Shin, H.S. (1993), *Measuring the Incidence of Insider Trading in a Market
    for State-Contingent Claims*, Economic Journal 103, 1141-1153.  Derives the
    margin from a bookmaker optimally protecting themselves against a fraction
    ``z`` of insider money.  It consistently wins the empirical comparisons —
    see Strumbelj, E. (2014), *On determining probability forecasts from
    betting odds*, International Journal of Forecasting 30(4), 934-943.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from enum import Enum

from foot.domain import OutcomeProbabilities
from foot.market.odds import MatchOdds
from foot.numerics.roots import bisect_root, expand_bracket

__all__ = ["DevigMethod", "fair_probabilities", "remove_margin", "shin_insider_fraction"]


class DevigMethod(Enum):
    """Strategies for redistributing a bookmaker's margin."""

    MULTIPLICATIVE = "multiplicative"
    ADDITIVE = "additive"
    POWER = "power"
    SHIN = "shin"


def _validated_raw(raw: Sequence[float]) -> list[float]:
    if len(raw) < 2:
        raise ValueError("a market needs at least two outcomes")
    values = []
    for p in raw:
        if not math.isfinite(p) or p <= 0.0:
            raise ValueError(f"implied probabilities must be positive and finite, got {p!r}")
        if p >= 1.0:
            raise ValueError(f"implied probability {p!r} implies decimal odds of at most 1.0")
        values.append(float(p))
    return values


def shin_insider_fraction(raw: Sequence[float]) -> float:
    """Estimate Shin's insider-trading fraction ``z`` from quoted prices.

    ``z`` is the root in ``[0, 1)`` of ``sum(p_i(z)) == 1``.  That root always
    exists for genuine decimal odds: at ``z = 0`` the implied total is
    ``sqrt(B) >= 1``, while as ``z`` approaches one it tends to
    ``sum(pi_i ** 2) / B``, which is strictly below one because every
    ``pi_i < 1`` forces ``sum(pi_i ** 2) < sum(pi_i) = B``.  The total is
    continuous in between, so bisection cannot fail.
    """
    values = _validated_raw(raw)
    booksum = math.fsum(values)
    if booksum <= 1.0:
        return 0.0

    def excess(z: float) -> float:
        return math.fsum(_shin_probabilities(values, booksum, z)) - 1.0

    return bisect_root(excess, 0.0, 1.0 - 1e-12, xtol=1e-15, ftol=1e-15)


def _shin_probabilities(raw: Sequence[float], booksum: float, z: float) -> list[float]:
    """Shin's inversion, written so that no catastrophic cancellation occurs.

    The textbook form is

        p_i = (sqrt(z^2 + 4 (1 - z) pi_i^2 / B) - z) / (2 (1 - z)),

    in which both the numerator and the denominator vanish as ``z`` approaches
    one.  Multiplying through by the conjugate gives the algebraically identical

        p_i = 2 pi_i^2 / (B (sqrt(z^2 + 4 (1 - z) pi_i^2 / B) + z)),

    which is a ratio of well-scaled quantities over the whole range of ``z``.
    """
    result = []
    for pi in raw:
        squared = pi * pi / booksum
        discriminant = math.sqrt(z * z + 4.0 * (1.0 - z) * squared)
        result.append(2.0 * squared / (discriminant + z) if discriminant + z > 0.0 else 0.0)
    return result


def _power_exponent(raw: Sequence[float]) -> float:
    """Find ``k >= 1`` with ``sum(pi_i ** k) == 1``."""
    values = _validated_raw(raw)
    if math.fsum(values) <= 1.0:
        return 1.0

    def excess(k: float) -> float:
        return math.fsum(pi**k for pi in values) - 1.0

    low, high = expand_bracket(excess, 1.0, 2.0, upper_limit=1e6)
    return bisect_root(excess, low, high, xtol=1e-15, ftol=1e-15)


def remove_margin(
    raw: Sequence[float], method: DevigMethod = DevigMethod.SHIN
) -> list[float]:
    """Convert raw implied probabilities into a proper probability vector.

    Args:
        raw: the ``1 / odds`` values of a complete, mutually exclusive market.
        method: which margin model to apply.

    Returns:
        Probabilities that are non-negative and sum to exactly one.
    """
    values = _validated_raw(raw)
    booksum = math.fsum(values)

    if method is DevigMethod.MULTIPLICATIVE:
        result = [pi / booksum for pi in values]
    elif method is DevigMethod.ADDITIVE:
        share = (booksum - 1.0) / len(values)
        # Longshots can be pushed below zero; clamping keeps the output a valid
        # distribution, and the renormalisation below restores the unit sum.
        result = [max(pi - share, 1e-12) for pi in values]
    elif method is DevigMethod.POWER:
        exponent = _power_exponent(values)
        result = [pi**exponent for pi in values]
    elif method is DevigMethod.SHIN:
        z = shin_insider_fraction(values)
        result = _shin_probabilities(values, booksum, z)
    else:  # pragma: no cover - exhaustive over the enum
        raise ValueError(f"unsupported de-vig method: {method!r}")

    total = math.fsum(result)
    if total <= 0.0:  # pragma: no cover - unreachable for validated input
        raise ValueError("de-vigging produced no probability mass")
    return [p / total for p in result]


def fair_probabilities(
    odds: MatchOdds, method: DevigMethod = DevigMethod.SHIN
) -> OutcomeProbabilities:
    """The market's implied 1X2 probabilities with the margin removed."""
    home, draw, away = remove_margin(odds.raw_probabilities(), method)
    return OutcomeProbabilities(home, draw, away)
