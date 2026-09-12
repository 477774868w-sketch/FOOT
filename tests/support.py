"""Test helpers with no third-party dependencies.

The suite deliberately avoids importing pytest so that it can also be run by
``python3 tests/run_tests.py`` on a machine with nothing installed.
"""

from __future__ import annotations

import contextlib
import math
from collections.abc import Iterator


@contextlib.contextmanager
def assert_raises(exception: type[BaseException], *, match: str | None = None) -> Iterator[None]:
    """Assert that the block raises ``exception`` (optionally containing text)."""
    try:
        yield
    except exception as error:
        if match is not None and match.lower() not in str(error).lower():
            raise AssertionError(
                f"expected {exception.__name__} mentioning {match!r}, got {error!r}"
            ) from None
        return
    raise AssertionError(f"expected {exception.__name__}, nothing was raised")


def assert_close(
    actual: float, expected: float, *, tolerance: float = 1e-9, label: str = "value"
) -> None:
    """Assert approximate equality with an informative message."""
    if not math.isfinite(actual) or abs(actual - expected) > tolerance:
        raise AssertionError(
            f"{label}: expected {expected!r} +/- {tolerance:g}, got {actual!r} "
            f"(difference {actual - expected:+.3e})"
        )


def assert_probability_vector(values: object, *, tolerance: float = 1e-9) -> None:
    """Assert that ``values`` is a genuine probability distribution."""
    items = list(values)  # type: ignore[call-overload]
    if not items:
        raise AssertionError("probability vector is empty")
    for p in items:
        if not math.isfinite(p) or p < -tolerance or p > 1.0 + tolerance:
            raise AssertionError(f"{p!r} is not a probability in [0, 1]")
    total = math.fsum(items)
    if abs(total - 1.0) > tolerance:
        raise AssertionError(f"probabilities sum to {total!r}, not 1")
