"""Leak-free walk-forward backtesting.

The only difficult part of backtesting a football model is not the arithmetic —
it is proving that the model never saw the future.  This module makes that
structural rather than a matter of discipline:

* forecasters are *given* their history as an argument, they never hold the
  full dataset;
* the history handed to them is ``matches.before(kickoff)``, an exclusive cut,
  so same-day fixtures cannot inform each other;
* a match is scored only when *every* forecaster produced a forecast for it, so
  the comparison is always over an identical sample.

That last point is what makes the resulting table meaningful.  A model that
quietly declines the hard fixtures will look excellent on the ones it kept.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from foot.domain import Fixture, MatchLog, Outcome, OutcomeProbabilities
from foot.evaluation.calibration import CalibrationReport, calibration_report
from foot.evaluation.metrics import ScoreCard
from foot.market.devig import DevigMethod, fair_probabilities
from foot.market.odds import MatchOdds
from foot.models.base import MatchModel
from foot.ratings.elo import EloRatingSystem

__all__ = [
    "BacktestResult",
    "BaseRateForecaster",
    "BlendForecaster",
    "EloForecaster",
    "Forecaster",
    "MarketForecaster",
    "ModelForecaster",
    "walk_forward",
]


@runtime_checkable
class Forecaster(Protocol):
    """Anything that turns a fixture plus its past into 1X2 probabilities."""

    @property
    def name(self) -> str:
        """Label used in result tables."""

    def forecast(self, fixture: Fixture, history: MatchLog) -> OutcomeProbabilities | None:
        """Probabilities for ``fixture``, or ``None`` to decline.

        ``history`` contains every match played strictly before the fixture and
        nothing else.  Implementations must not retain information across
        calls that did not come from a ``history`` they were given.
        """


class BaseRateForecaster:
    """The honest floor: the empirical home/draw/away frequencies so far.

    Any model that cannot beat this is not modelling football, and quoting a
    model's RPS without this number next to it is meaningless.
    """

    __slots__ = ("_min_matches", "_name", "_prior", "_prior_weight")

    def __init__(
        self,
        *,
        name: str = "base rate",
        min_matches: int = 20,
        prior: tuple[float, float, float] = (0.45, 0.26, 0.29),
        prior_weight: float = 10.0,
    ) -> None:
        if prior_weight < 0.0:
            raise ValueError("prior_weight must be non-negative")
        self._name = name
        self._min_matches = min_matches
        self._prior = prior
        self._prior_weight = prior_weight

    @property
    def name(self) -> str:
        return self._name

    def forecast(
        self, fixture: Fixture, history: MatchLog  # noqa: ARG002 - set by the protocol
    ) -> OutcomeProbabilities | None:
        if len(history) < self._min_matches:
            return None
        counts = [0.0, 0.0, 0.0]
        for match in history:
            counts[match.outcome.index] += 1.0
        weights = [
            counts[i] + self._prior_weight * self._prior[i] for i in range(3)
        ]
        return OutcomeProbabilities.normalised(*weights)


class ModelForecaster:
    """Wraps a :class:`~foot.models.base.MatchModel` for walk-forward use.

    Refitting on every match date is wasteful and, for a time-decayed
    likelihood, barely changes the answer.  ``refit_interval`` controls how many
    *new matches* must accumulate before the model is re-estimated; the cached
    fit is always strictly older than the fixture being forecast, so this is an
    efficiency choice, never a leak.
    """

    __slots__ = ("_cached", "_factory", "_fitted_at", "_min_matches", "_name", "_refit_interval")

    def __init__(
        self,
        name: str,
        factory: Callable[[], MatchModel],
        *,
        min_matches: int = 60,
        refit_interval: int = 10,
    ) -> None:
        if refit_interval < 1:
            raise ValueError("refit_interval must be at least 1")
        self._name = name
        self._factory = factory
        self._min_matches = min_matches
        self._refit_interval = refit_interval
        self._cached: MatchModel | None = None
        self._fitted_at = -1

    @property
    def name(self) -> str:
        return self._name

    def forecast(self, fixture: Fixture, history: MatchLog) -> OutcomeProbabilities | None:
        if len(history) < self._min_matches:
            return None
        if self._cached is None or len(history) - self._fitted_at >= self._refit_interval:
            self._cached = self._factory().fit(history)
            self._fitted_at = len(history)
        try:
            return self._cached.predict(fixture)
        except KeyError:
            return None  # a team the fitted model has never seen

    def __repr__(self) -> str:
        return f"ModelForecaster({self._name!r}, refit_interval={self._refit_interval})"


class EloForecaster:
    """Elo ratings rebuilt from the supplied history at every forecast date.

    Elo updates are cheap, and rebuilding rather than caching removes any
    possibility of state surviving from a future fixture.
    """

    __slots__ = ("_fit_draw_parameter", "_min_matches", "_name", "_system")

    def __init__(
        self,
        name: str = "elo",
        system: EloRatingSystem | None = None,
        *,
        min_matches: int = 40,
        fit_draw_parameter: bool = False,
    ) -> None:
        self._name = name
        self._system = system if system is not None else EloRatingSystem()
        self._min_matches = min_matches
        self._fit_draw_parameter = fit_draw_parameter

    @property
    def name(self) -> str:
        return self._name

    def forecast(self, fixture: Fixture, history: MatchLog) -> OutcomeProbabilities | None:
        if len(history) < self._min_matches:
            return None
        table = self._system.rate(history)
        if fixture.home not in table or fixture.away not in table:
            return None
        if self._fit_draw_parameter:
            nu = table.fit_draw_parameter(history)
            return table.outcome_probabilities(
                fixture.home, fixture.away, neutral=fixture.neutral, draw_parameter=nu
            )
        return table.predict(fixture)


class MarketForecaster:
    """The betting market, with its margin removed — the benchmark to beat."""

    __slots__ = ("_method", "_name", "_odds")

    def __init__(
        self,
        odds: Mapping[Fixture, MatchOdds],
        *,
        name: str = "market",
        method: DevigMethod = DevigMethod.SHIN,
    ) -> None:
        self._odds = dict(odds)
        self._name = name
        self._method = method

    @property
    def name(self) -> str:
        return self._name

    def forecast(
        self, fixture: Fixture, history: MatchLog  # noqa: ARG002 - set by the protocol
    ) -> OutcomeProbabilities | None:
        quote = self._odds.get(fixture)
        return None if quote is None else fair_probabilities(quote, self._method)


class BlendForecaster:
    """A weighted geometric blend of other forecasters.

    Geometric pooling (averaging in log space, then renormalising) is the
    standard choice for combining probability forecasts: it is externally
    Bayesian, and unlike a linear average it will not turn two confident,
    agreeing forecasts into a timid one.
    """

    __slots__ = ("_components", "_floor", "_name", "_weights")

    def __init__(
        self,
        name: str,
        components: Sequence[Forecaster],
        weights: Sequence[float] | None = None,
        *,
        floor: float = 1e-6,
    ) -> None:
        if not components:
            raise ValueError("a blend needs at least one component")
        if weights is None:
            weights = [1.0 / len(components)] * len(components)
        if len(weights) != len(components):
            raise ValueError("weights and components must have equal length")
        total = math.fsum(weights)
        if total <= 0.0 or any(w < 0.0 for w in weights):
            raise ValueError("weights must be non-negative and not all zero")
        self._name = name
        self._components = tuple(components)
        self._weights = tuple(w / total for w in weights)
        self._floor = floor

    @property
    def name(self) -> str:
        return self._name

    def forecast(self, fixture: Fixture, history: MatchLog) -> OutcomeProbabilities | None:
        accumulated = [0.0, 0.0, 0.0]
        for component, weight in zip(self._components, self._weights, strict=True):
            parts = component.forecast(fixture, history)
            if parts is None:
                return None
            for i, p in enumerate(parts.as_tuple()):
                accumulated[i] += weight * math.log(max(p, self._floor))
        return OutcomeProbabilities.normalised(*(math.exp(v) for v in accumulated))


@dataclass(frozen=True, slots=True)
class BacktestRecord:
    """One scored fixture and every forecaster's view of it."""

    fixture: Fixture
    outcome: Outcome
    forecasts: Mapping[str, OutcomeProbabilities]


@dataclass(frozen=True, slots=True)
class BacktestResult:
    """Scored forecasts, ready for comparison."""

    records: tuple[BacktestRecord, ...]
    scorecards: Mapping[str, ScoreCard]
    evaluated: int
    skipped: int
    names: tuple[str, ...]

    def forecasts_of(self, name: str) -> list[OutcomeProbabilities]:
        return [record.forecasts[name] for record in self.records]

    def outcomes(self) -> list[Outcome]:
        return [record.outcome for record in self.records]

    def calibration(self, name: str, *, bins: int = 10) -> CalibrationReport:
        return calibration_report(self.forecasts_of(name), self.outcomes(), bins=bins)

    def ranked(self) -> list[ScoreCard]:
        """Scorecards ordered from best to worst by ranked probability score."""
        return sorted(self.scorecards.values(), key=lambda card: card.rps)

    def summary(self, *, baseline: str | None = None) -> str:
        """A comparison table, skill scores relative to ``baseline`` if given."""
        lines = [
            f"walk-forward backtest: {self.evaluated} fixtures scored, "
            f"{self.skipped} skipped (insufficient history or unknown team)"
        ]
        reference = self.scorecards.get(baseline) if baseline else None
        for card in self.ranked():
            line = f"  {card}"
            if reference is not None and card.name != reference.name:
                line += f"  skill={card.skill_against(reference) * 100:+.2f}%"
            lines.append(line)
        return "\n".join(lines)

    def __str__(self) -> str:
        return self.summary()


def walk_forward(
    matches: MatchLog,
    forecasters: Sequence[Forecaster],
    *,
    start: int = 0,
    progress: Callable[[int, int], None] | None = None,
) -> BacktestResult:
    """Run every forecaster over ``matches`` in strict chronological order.

    Args:
        matches: the full match log; it is walked date by date.
        forecasters: the contenders.  Names must be unique.
        start: skip the first ``start`` matches entirely, giving every model a
            burn-in period that is identical across forecasters.
        progress: optional ``(done, total)`` callback.

    Returns:
        A :class:`BacktestResult` in which every scored fixture carries a
        forecast from every forecaster.
    """
    if not forecasters:
        raise ValueError("need at least one forecaster")
    names = [f.name for f in forecasters]
    if len(set(names)) != len(names):
        raise ValueError(f"forecaster names must be unique, got {names}")

    records: list[BacktestRecord] = []
    skipped = 0
    processed = 0
    total = len(matches)

    for date in matches.dates():
        history = matches.before(date)
        for match in matches.between(date, date):
            processed += 1
            if processed <= start:
                skipped += 1
                continue
            views: dict[str, OutcomeProbabilities] = {}
            for forecaster in forecasters:
                view = forecaster.forecast(match.fixture, history)
                if view is None:
                    break
                views[forecaster.name] = view
            if len(views) != len(forecasters):
                skipped += 1
                continue
            records.append(
                BacktestRecord(fixture=match.fixture, outcome=match.outcome, forecasts=views)
            )
        if progress is not None:
            progress(processed, total)

    if not records:
        raise ValueError(
            "no fixture could be scored by every forecaster; "
            "lower min_matches or supply more history"
        )

    outcomes = [record.outcome for record in records]
    scorecards = {
        name: ScoreCard.evaluate(
            [record.forecasts[name] for record in records], outcomes, name=name
        )
        for name in names
    }
    return BacktestResult(
        records=tuple(records),
        scorecards=scorecards,
        evaluated=len(records),
        skipped=skipped,
        names=tuple(names),
    )
