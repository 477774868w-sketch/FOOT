"""Scoring rules for probabilistic football forecasts.

Every rule here is *proper*: a forecaster minimises its expected score only by
reporting its true beliefs.  That is not a technicality — accuracy, the most
commonly quoted metric, is improper, and a model tuned on it will learn to
exaggerate.  Accuracy is provided for reporting, never for selection.

The default rule for 1X2 forecasts is the ranked probability score, which
alone respects the ordering home > draw > away: predicting a home win when the
away side wins should cost more than predicting a draw.

Constantinou, A.C. and Fenton, N.E. (2012), *Solving the problem of inadequate
scoring rules for assessing probabilistic football forecast models*, Journal of
Quantitative Analysis in Sports 8(1).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from foot.domain import Outcome, OutcomeProbabilities

__all__ = [
    "ScoreCard",
    "brier_score",
    "ignorance_score",
    "log_loss",
    "ranked_probability_score",
]

_LOG_FLOOR = 1e-15


def _observed(outcome: Outcome) -> tuple[float, float, float]:
    indicator = [0.0, 0.0, 0.0]
    indicator[outcome.index] = 1.0
    return (indicator[0], indicator[1], indicator[2])


def ranked_probability_score(
    forecast: OutcomeProbabilities, outcome: Outcome
) -> float:
    """Ranked probability score of a 1X2 forecast: lower is better, 0 is perfect.

    For ``r`` ordered categories,

        RPS = 1 / (r - 1) * sum over i < r of (cumulative forecast_i -
              cumulative observation_i) ** 2

    Being distance-sensitive, it penalises a confident home-win forecast more
    when the away team wins than when the match is drawn.

    >>> from foot.domain import Outcome, OutcomeProbabilities
    >>> round(ranked_probability_score(
    ...     OutcomeProbabilities(0.8, 0.1, 0.1), Outcome.HOME_WIN), 6)
    0.025
    """
    predicted = forecast.as_tuple()
    actual = _observed(outcome)
    total = 0.0
    cumulative_predicted = 0.0
    cumulative_actual = 0.0
    for i in range(2):  # the final cumulative pair is 1 == 1 and contributes nothing
        cumulative_predicted += predicted[i]
        cumulative_actual += actual[i]
        difference = cumulative_predicted - cumulative_actual
        total += difference * difference
    return total / 2.0


def brier_score(forecast: OutcomeProbabilities, outcome: Outcome) -> float:
    """Multi-category Brier score: the squared error summed over outcomes.

    Ranges from 0 (perfect) to 2 (confidently wrong).  Unlike the RPS it
    ignores the ordering of the categories.

    >>> from foot.domain import Outcome, OutcomeProbabilities
    >>> round(brier_score(OutcomeProbabilities(0.8, 0.1, 0.1), Outcome.HOME_WIN), 6)
    0.06
    """
    predicted = forecast.as_tuple()
    actual = _observed(outcome)
    return math.fsum((p - a) ** 2 for p, a in zip(predicted, actual, strict=True))


def log_loss(forecast: OutcomeProbabilities, outcome: Outcome) -> float:
    """Negative log-likelihood of the observed outcome, in nats.

    The probability is floored at ``1e-15`` so that a single overconfident
    forecast cannot make an average infinite; a model that needs that floor is
    already telling you something.
    """
    return -math.log(max(forecast[outcome], _LOG_FLOOR))


def ignorance_score(forecast: OutcomeProbabilities, outcome: Outcome) -> float:
    """Log loss in bits — the number of yes/no questions the miss cost you."""
    return log_loss(forecast, outcome) / math.log(2.0)


@dataclass(frozen=True, slots=True)
class ScoreCard:
    """Aggregate performance of a forecaster over a set of matches."""

    name: str
    count: int
    rps: float
    brier: float
    log_loss: float
    accuracy: float

    @classmethod
    def evaluate(
        cls,
        forecasts: Sequence[OutcomeProbabilities],
        outcomes: Sequence[Outcome],
        *,
        name: str = "model",
    ) -> ScoreCard:
        """Score a batch of forecasts against what actually happened."""
        if len(forecasts) != len(outcomes):
            raise ValueError("forecasts and outcomes must have equal length")
        if not forecasts:
            raise ValueError("cannot score an empty set of forecasts")
        n = len(forecasts)
        return cls(
            name=name,
            count=n,
            rps=math.fsum(
                ranked_probability_score(f, o) for f, o in zip(forecasts, outcomes, strict=True)
            ) / n,
            brier=math.fsum(
                brier_score(f, o) for f, o in zip(forecasts, outcomes, strict=True)
            ) / n,
            log_loss=math.fsum(
                log_loss(f, o) for f, o in zip(forecasts, outcomes, strict=True)
            ) / n,
            accuracy=math.fsum(
                1.0 for f, o in zip(forecasts, outcomes, strict=True) if f.most_likely is o
            ) / n,
        )

    def skill_against(self, baseline: ScoreCard) -> float:
        """Skill score on the RPS: the fraction of the baseline's loss removed.

        ``1`` is perfect, ``0`` is no better than the baseline, negative is
        worse.  This is the number worth quoting, because a raw RPS means
        nothing without knowing how hard the sample was.
        """
        if baseline.rps <= 0.0:
            raise ValueError("baseline RPS must be positive to compute a skill score")
        return 1.0 - self.rps / baseline.rps

    def __str__(self) -> str:
        return (
            f"{self.name:<22} n={self.count:<6d} RPS={self.rps:.5f}  "
            f"Brier={self.brier:.5f}  logloss={self.log_loss:.5f}  "
            f"acc={self.accuracy * 100:5.1f}%"
        )
