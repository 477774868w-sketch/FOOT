"""Match models: the Poisson baseline and the Dixon-Coles refinement."""

from foot.models.base import HandicapSettlement, MatchModel, ScoreMatrix, TotalsSettlement
from foot.models.dixon_coles import (
    DixonColesModel,
    DixonColesParameters,
    FitDiagnostics,
    TeamRating,
    dixon_coles_tau,
)
from foot.models.poisson import PoissonModel

__all__ = [
    "DixonColesModel",
    "DixonColesParameters",
    "FitDiagnostics",
    "HandicapSettlement",
    "MatchModel",
    "PoissonModel",
    "ScoreMatrix",
    "TeamRating",
    "TotalsSettlement",
    "dixon_coles_tau",
]
