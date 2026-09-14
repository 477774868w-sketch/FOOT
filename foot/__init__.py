"""foot — a rigorous, dependency-free football modelling engine.

The library is organised around one idea: a football forecast is a joint
distribution over scorelines, and everything else — 1X2 prices, totals,
handicaps, league tables, title odds, stake sizes — is a summation over it.

    >>> from foot import DixonColesModel, synthetic_league
    >>> matches, _ = synthetic_league(seed=1)
    >>> model = DixonColesModel(half_life_days=180).fit(matches)
    >>> forecast = model.predict(matches[0].fixture)
    >>> round(sum(forecast.as_tuple()), 12)
    1.0

Layout
------
``foot.domain``
    Immutable match, fixture and probability types.
``foot.numerics``
    L-BFGS with a strong-Wolfe line search, safeguarded root finding and
    numerically stable primitives.  No third-party code.
``foot.models``
    The independent-Poisson baseline and the Dixon-Coles model, fitted by
    maximum likelihood with hand-derived analytic gradients.
``foot.ratings``
    Elo, with published margin-of-victory rules and Davidson's tie model.
``foot.market``
    Odds arithmetic, four margin-removal methods (including Shin's) and exact
    simultaneous Kelly staking.
``foot.evaluation``
    Proper scoring rules, calibration, leak-free walk-forward backtesting and
    bankroll simulation.
``foot.simulation``
    Monte Carlo projection of a season's remaining fixtures.
``foot.league``
    League tables with configurable tiebreakers, head-to-head included.
``foot.data``
    Reproducible synthetic leagues with known ground truth, and CSV ingestion.
"""

from __future__ import annotations

from foot.data.csv_source import load_matches, load_odds, write_matches
from foot.data.synthetic import LeagueTruth, synthetic_league, synthetic_odds
from foot.domain import Fixture, Match, MatchLog, Outcome, OutcomeProbabilities, Score
from foot.evaluation.backtest import (
    BacktestResult,
    BaseRateForecaster,
    BlendForecaster,
    EloForecaster,
    Forecaster,
    MarketForecaster,
    ModelForecaster,
    walk_forward,
)
from foot.evaluation.betting import BettingResult, simulate_betting
from foot.evaluation.calibration import CalibrationReport, calibration_report
from foot.evaluation.metrics import (
    ScoreCard,
    brier_score,
    log_loss,
    ranked_probability_score,
)
from foot.league.table import LeagueRules, LeagueTable, TableRow, Tiebreaker
from foot.market.devig import DevigMethod, fair_probabilities, remove_margin
from foot.market.kelly import KellyAllocation, kelly_fraction, kelly_portfolio
from foot.market.odds import MatchOdds
from foot.models.base import ScoreMatrix
from foot.models.dixon_coles import DixonColesModel, DixonColesParameters
from foot.models.poisson import PoissonModel
from foot.ratings.elo import EloRatingSystem, EloTable, MarginRule
from foot.simulation.season import SeasonProjection, SeasonSimulator

__version__ = "1.0.0"

__all__ = [
    "BacktestResult",
    "BaseRateForecaster",
    "BettingResult",
    "BlendForecaster",
    "CalibrationReport",
    "DevigMethod",
    "DixonColesModel",
    "DixonColesParameters",
    "EloForecaster",
    "EloRatingSystem",
    "EloTable",
    "Fixture",
    "Forecaster",
    "KellyAllocation",
    "LeagueRules",
    "LeagueTable",
    "LeagueTruth",
    "MarginRule",
    "MarketForecaster",
    "Match",
    "MatchLog",
    "MatchOdds",
    "ModelForecaster",
    "Outcome",
    "OutcomeProbabilities",
    "PoissonModel",
    "Score",
    "ScoreCard",
    "ScoreMatrix",
    "SeasonProjection",
    "SeasonSimulator",
    "TableRow",
    "Tiebreaker",
    "__version__",
    "brier_score",
    "calibration_report",
    "fair_probabilities",
    "kelly_fraction",
    "kelly_portfolio",
    "load_matches",
    "load_odds",
    "log_loss",
    "ranked_probability_score",
    "remove_margin",
    "simulate_betting",
    "synthetic_league",
    "synthetic_odds",
    "walk_forward",
    "write_matches",
]
