"""Calibration: are forecasts of 30 percent right 30 percent of the time?

A model can be sharp and still useless if its probabilities are systematically
too confident.  Calibration is measured by binning forecasts by their claimed
probability and comparing each bin's mean claim against the observed frequency.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from foot.domain import Outcome, OutcomeProbabilities

__all__ = ["CalibrationBin", "CalibrationReport", "calibration_report"]


@dataclass(frozen=True, slots=True)
class CalibrationBin:
    """One bucket of forecasts sharing a similar claimed probability."""

    lower: float
    upper: float
    count: int
    mean_forecast: float
    observed_frequency: float

    @property
    def bias(self) -> float:
        """Positive means the model claims more than it delivers."""
        return self.mean_forecast - self.observed_frequency

    def __str__(self) -> str:
        return (
            f"[{self.lower:.2f}, {self.upper:.2f})  n={self.count:<6d} "
            f"claimed={self.mean_forecast:.3f}  observed={self.observed_frequency:.3f}  "
            f"bias={self.bias:+.3f}"
        )


@dataclass(frozen=True, slots=True)
class CalibrationReport:
    """Binned calibration with its summary statistics."""

    bins: tuple[CalibrationBin, ...]
    expected_calibration_error: float
    maximum_calibration_error: float
    count: int

    def __str__(self) -> str:
        lines = [
            f"calibration over {self.count} forecast-outcome pairs "
            f"(ECE = {self.expected_calibration_error:.4f}, "
            f"MCE = {self.maximum_calibration_error:.4f})"
        ]
        lines.extend(f"  {b}" for b in self.bins)
        return "\n".join(lines)


def calibration_report(
    forecasts: Sequence[OutcomeProbabilities],
    outcomes: Sequence[Outcome],
    *,
    bins: int = 10,
) -> CalibrationReport:
    """Bin every (outcome, probability) pair and compare claim with frequency.

    All three outcomes of each match contribute, so ``n`` forecasts produce
    ``3n`` pairs.  That is the right unit: a model can be well calibrated on
    home wins and badly calibrated on draws, and pooling exposes it.

    Args:
        bins: number of equal-width buckets spanning ``[0, 1]``.
    """
    if len(forecasts) != len(outcomes):
        raise ValueError("forecasts and outcomes must have equal length")
    if not forecasts:
        raise ValueError("cannot build a calibration report from no forecasts")
    if bins < 1:
        raise ValueError("bins must be positive")

    totals = [0.0] * bins
    hits = [0.0] * bins
    counts = [0] * bins
    pairs = 0
    for forecast, outcome in zip(forecasts, outcomes, strict=True):
        for candidate in Outcome:
            p = forecast[candidate]
            index = min(int(p * bins), bins - 1)
            totals[index] += p
            hits[index] += 1.0 if candidate is outcome else 0.0
            counts[index] += 1
            pairs += 1

    report_bins: list[CalibrationBin] = []
    weighted_error = 0.0
    worst = 0.0
    for i in range(bins):
        if counts[i] == 0:
            continue
        mean_forecast = totals[i] / counts[i]
        observed = hits[i] / counts[i]
        report_bins.append(
            CalibrationBin(
                lower=i / bins,
                upper=(i + 1) / bins,
                count=counts[i],
                mean_forecast=mean_forecast,
                observed_frequency=observed,
            )
        )
        gap = abs(mean_forecast - observed)
        weighted_error += counts[i] * gap
        worst = max(worst, gap)

    return CalibrationReport(
        bins=tuple(report_bins),
        expected_calibration_error=weighted_error / pairs if pairs else math.nan,
        maximum_calibration_error=worst,
        count=len(forecasts),
    )
