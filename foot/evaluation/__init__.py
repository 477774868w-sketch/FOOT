"""Scoring, calibration and leak-free backtesting."""

from foot.evaluation.backtest import (
    BacktestRecord,
    BacktestResult,
    BaseRateForecaster,
    BlendForecaster,
    EloForecaster,
    Forecaster,
    MarketForecaster,
    ModelForecaster,
    walk_forward,
)
from foot.evaluation.betting import Bet, BettingResult, simulate_betting
from foot.evaluation.calibration import CalibrationBin, CalibrationReport, calibration_report
from foot.evaluation.metrics import (
    ScoreCard,
    brier_score,
    ignorance_score,
    log_loss,
    ranked_probability_score,
)

__all__ = [
    "BacktestRecord",
    "BacktestResult",
    "BaseRateForecaster",
    "Bet",
    "BettingResult",
    "BlendForecaster",
    "CalibrationBin",
    "CalibrationReport",
    "EloForecaster",
    "Forecaster",
    "MarketForecaster",
    "ModelForecaster",
    "ScoreCard",
    "brier_score",
    "calibration_report",
    "ignorance_score",
    "log_loss",
    "ranked_probability_score",
    "simulate_betting",
    "walk_forward",
]
