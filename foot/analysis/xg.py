"""Le rapport buts / xG, estimé au lieu d'être divisé.

Le rapport brut ``buts / xG`` est inutilisable tel quel : il vaut zéro dès qu'une
équipe n'a pas marqué, l'infini dès que les xG fournis sont nuls, et il est aussi
bruyant sur un match que stable sur vingt.  La version auditée le divisait
directement — un match sans but faisait tomber l'analyse de **tout le lot** par
``ZeroDivisionError``.

Zéro but est une observation normale.  Le traitement retenu ici est l'estimateur
a posteriori d'un modèle Gamma-Poisson, qui est la forme conjuguée naturelle du
problème :

    buts ~ Poisson(r · xG)        r ~ Gamma(k, k)

Le prior ``Gamma(k, k)`` est centré sur ``r = 1`` — « une équipe convertit ses
occasions au taux attendu » — et pèse ``k`` pseudo-buts.  Le postérieur est
``Gamma(k + buts, k + xG)``, de moyenne :

    r̂ = (buts + k) / (xG + k)

Cette moyenne est finie pour toute entrée finie et positive, elle tend vers le
rapport brut quand l'échantillon grandit, et elle tend vers 1 quand il est petit.
Rien n'est « remplacé » : zéro but reste zéro but, il pèse simplement ce qu'il
pèse face au prior.

L'écart-type a posteriori ``√(k + buts) / (k + xG)`` donne l'incertitude, et
c'est elle — pas un seuil arbitraire sur l'écart — qui décide si un écart mérite
un scénario.

``k`` est une **hypothèse**, pas une mesure : sa valeur est déclarée ici et
reportée dans la fiche. Elle n'a jamais été calibrée sur données hors
échantillon, et le rapport le dit.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

__all__ = ["PSEUDO_GOALS", "REVERSION_BOUNDS", "XgBalance"]

PSEUDO_GOALS = 4.0
"""Poids du prior, en buts. Environ trois matchs de rythme moyen.

Hypothèse déclarée : aucune calibration hors échantillon ne la soutient. Une
valeur plus grande rend l'estimateur plus prudent, jamais plus faux.
"""

REVERSION_BOUNDS = (0.6, 1.6)
"""Bornes du facteur de retour au niveau xG. Hypothèse, non mesurée.

Elles bornent l'ampleur d'un scénario prospectif ; elles ne décrivent aucune
régression observée et sont présentées comme telles dans le rapport.
"""

_Z = 1.959963984540054
"""Quantile normal à 95 %, utilisé pour l'intervalle a posteriori."""


@dataclass(frozen=True, slots=True)
class XgBalance:
    """Buts réellement marqués face aux xG fournis, sur les mêmes matchs."""

    team: str
    matches: int
    goals: float
    expected: float
    keys: tuple[str, ...] = ()
    pseudo_goals: float = PSEUDO_GOALS

    def __post_init__(self) -> None:
        for name, value in (("goals", self.goals), ("expected", self.expected)):
            if not math.isfinite(value) or value < 0.0:
                raise ValueError(f"{name} doit être fini et positif : {value!r}")
        if self.matches < 0:
            raise ValueError(f"matches doit être positif : {self.matches!r}")
        if not math.isfinite(self.pseudo_goals) or self.pseudo_goals <= 0.0:
            raise ValueError(f"pseudo_goals doit être > 0 : {self.pseudo_goals!r}")

    @property
    def raw_ratio(self) -> float:
        """Le rapport brut, pour la restitution seulement. Peut être nul ou infini."""
        if self.expected <= 0.0:
            return math.inf if self.goals > 0.0 else math.nan
        return self.goals / self.expected

    @property
    def ratio(self) -> float:
        """Moyenne a posteriori du taux de conversion. Toujours finie et > 0."""
        return (self.goals + self.pseudo_goals) / (self.expected + self.pseudo_goals)

    @property
    def sigma(self) -> float:
        """Écart-type a posteriori du taux."""
        return math.sqrt(self.goals + self.pseudo_goals) / (
            self.expected + self.pseudo_goals
        )

    @property
    def interval(self) -> tuple[float, float]:
        """Intervalle de crédibilité à 95 %, tronqué à zéro par le bas."""
        half = _Z * self.sigma
        return (max(self.ratio - half, 0.0), self.ratio + half)

    @property
    def interval_width(self) -> float:
        low, high = self.interval
        return high - low

    @property
    def material(self) -> bool:
        """Un écart ne compte que si l'incertitude ne l'explique pas à elle seule.

        Un seuil fixe sur l'écart au rapport ferait passer un seul match pour une
        tendance.  Ici, l'intervalle doit exclure « pas d'écart » — un match
        isolé ne le fait presque jamais, vingt matchs cohérents le font.
        """
        if self.matches <= 0:
            return False
        low, high = self.interval
        return low > 1.0 or high < 1.0

    @property
    def reversion_factor(self) -> float:
        """De combien multiplier le rythme si l'équipe revient à son niveau xG.

        C'est l'inverse du taux estimé, borné. Le taux est mesuré ; le retour
        complet vers les xG, lui, est une hypothèse — les deux sont distingués
        dans le texte du scénario.
        """
        low, high = REVERSION_BOUNDS
        return max(min(1.0 / self.ratio, high), low)

    def describe(self) -> str:
        """Phrase publiable, qui sépare ce qui est mesuré de ce qui est supposé."""
        raw = self.raw_ratio
        raw_text = (
            f"rapport brut {raw:.2f}"
            if math.isfinite(raw)
            else ("rapport brut non défini (xG fournis nuls)" if self.goals else
                  "rapport brut nul")
        )
        low, high = self.interval
        return (
            f"{self.goals:.0f} buts pour {self.expected:.2f} xG sur "
            f"{self.matches} match(s) — {raw_text} ; taux estimé {self.ratio:.2f} "
            f"[{low:.2f} ; {high:.2f}] à 95 % (régularisé par {self.pseudo_goals:.0f} "
            f"pseudo-buts, hypothèse non calibrée). MESURÉ : l'écart et son "
            f"incertitude. HYPOTHÈSE : le retour complet au niveau xG, borné à "
            f"×{REVERSION_BOUNDS[0]:.1f}–×{REVERSION_BOUNDS[1]:.1f}, soit "
            f"×{self.reversion_factor:.2f} ici."
        )
