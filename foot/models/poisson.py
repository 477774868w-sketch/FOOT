"""The independent-Poisson baseline.

Maher, M.J. (1982), *Modelling association football scores*, Statistica
Neerlandica 36(3), 109-118.

This is Dixon-Coles without the low-score correction, and it exists for a
reason: every claim that a more elaborate model is better has to be measured
against a baseline that is honest, well-fitted and scored on the same data.
"""

from __future__ import annotations

from foot.models.dixon_coles import DixonColesModel, DixonColesParameters

__all__ = ["PoissonModel"]


class PoissonModel(DixonColesModel):
    """Independent Poisson goals with attack, defence and home-advantage terms."""

    __slots__ = ()

    def __init__(
        self,
        *,
        half_life_days: float | None = None,
        ridge: float = 0.0,
        max_goals: int | None = None,
        parameters: DixonColesParameters | None = None,
        low_score_correction: bool = False,
    ) -> None:
        if low_score_correction:
            raise ValueError(
                "PoissonModel is the uncorrected baseline; "
                "use DixonColesModel for the low-score correction"
            )
        super().__init__(
            half_life_days=half_life_days,
            low_score_correction=False,
            ridge=ridge,
            max_goals=max_goals,
            parameters=parameters,
        )

    def __repr__(self) -> str:
        state = (
            f"fitted on {self.parameters.diagnostics.matches} matches"
            if self.is_fitted
            else "unfitted"
        )
        return f"PoissonModel(half_life_days={self.half_life_days!r}, {state})"
