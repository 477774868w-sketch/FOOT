"""Chronological validation: nothing is tuned on data it will be scored against.

A backtest that picks its half-life on the whole sample and then reports the
score of that half-life is measuring hindsight.  Every choice here — parameters,
shrinkage, model selection, calibration — is made **inside the training window
of each fold**, and scored only on the fold that follows.

Two cautions the operator insisted on are implemented rather than mentioned:

* **A match date does not prove a fact was known.**  Only results dated strictly
  before the cutoff enter training, and the engine's ``as_of`` mechanism carries
  the same rule into live analysis.  Statistics corrected after the fact, or an
  injury learned later, cannot be re-labelled pre-match by their match date.
* **Selections are dependent, and many variants get tried.**  The uncertainty
  reported for a yield is a moving-block bootstrap, which preserves the serial
  dependence that an ordinary bootstrap would destroy and would otherwise make
  the interval look far tighter than it is.
"""

from __future__ import annotations

import datetime as dt
import math
import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from foot.domain import MatchLog, Outcome, OutcomeProbabilities
from foot.evaluation.metrics import ScoreCard, ranked_probability_score
from foot.models.dixon_coles import DixonColesModel

__all__ = [
    "AblationResult",
    "ChronoFold",
    "ValidationReport",
    "block_bootstrap_interval",
    "chronological_folds",
    "tune_chronologically",
    "validate",
]


@dataclass(frozen=True, slots=True)
class ChronoFold:
    """One expanding-window fold: train strictly before, score strictly after."""

    index: int
    cutoff: dt.date
    train: MatchLog
    test: MatchLog

    def __post_init__(self) -> None:
        late = [m for m in self.train if m.date >= self.cutoff]
        if late:
            raise ValueError(
                f"fuite temporelle dans le pli {self.index} : "
                f"{len(late)} matchs d'entraînement au-delà de {self.cutoff}"
            )


def chronological_folds(
    matches: MatchLog, *, folds: int = 5, min_train: int = 200
) -> list[ChronoFold]:
    """Expanding-window folds over the calendar, never shuffled."""
    if folds < 1:
        raise ValueError("au moins un pli est requis")
    dates = matches.dates()
    if len(matches) <= min_train:
        raise ValueError(
            f"historique trop court : {len(matches)} matchs pour un minimum "
            f"d'entraînement de {min_train}"
        )
    usable = [d for d in dates if len(matches.before(d)) >= min_train]
    if not usable:
        raise ValueError("aucune date ne laisse assez d'historique d'entraînement")
    step = max(1, len(usable) // folds)
    built: list[ChronoFold] = []
    for index in range(folds):
        position = min(index * step, len(usable) - 1)
        cutoff = usable[position]
        end = usable[min(position + step, len(usable) - 1)] if index < folds - 1 else None
        train = matches.before(cutoff)
        test = matches.between(cutoff, end) if end else matches.after(cutoff)
        if len(test) == 0:
            continue
        built.append(ChronoFold(index=index, cutoff=cutoff, train=train, test=test))
    if not built:
        raise ValueError("aucun pli exploitable")
    return built


def _forecast(model: DixonColesModel, matches: MatchLog) -> list[OutcomeProbabilities]:
    forecasts: list[OutcomeProbabilities] = []
    for match in matches:
        try:
            forecasts.append(model.predict(match.fixture))
        except KeyError:
            forecasts.append(OutcomeProbabilities(0.45, 0.26, 0.29))  # league base rate
    return forecasts


def tune_chronologically(
    train: MatchLog,
    *,
    half_lives: Sequence[float] = (120.0, 180.0, 240.0, 365.0),
    ridges: Sequence[float] = (0.0, 0.3, 1.0),
    inner_folds: int = 3,
) -> tuple[float, float]:
    """Choose ``(half_life, ridge)`` using only data inside ``train``.

    The search is itself chronological: an inner expanding-window split, scored
    on held-out *later* matches from the training window.  Tuning on the whole
    training set and scoring on the test set would still leak, more subtly.
    """
    try:
        inner = chronological_folds(train, folds=inner_folds, min_train=max(100, len(train) // 3))
    except ValueError:
        return (half_lives[len(half_lives) // 2], ridges[0])

    best: tuple[float, float] | None = None
    best_score = math.inf
    for half_life in half_lives:
        for ridge in ridges:
            losses: list[float] = []
            for fold in inner:
                try:
                    model = DixonColesModel(half_life_days=half_life, ridge=ridge).fit(
                        fold.train, reference_date=fold.cutoff
                    )
                except (ValueError, RuntimeError):
                    continue
                forecasts = _forecast(model, fold.test)
                outcomes = [m.outcome for m in fold.test]
                if forecasts:
                    losses.append(ScoreCard.evaluate(forecasts, outcomes).rps)
            if losses:
                mean = math.fsum(losses) / len(losses)
                if mean < best_score:
                    best_score, best = mean, (half_life, ridge)
    return best or (half_lives[len(half_lives) // 2], ridges[0])


@dataclass(frozen=True, slots=True)
class AblationResult:
    """What one component contributes, measured by removing it."""

    component: str
    rps_with: float
    rps_without: float

    @property
    def gain(self) -> float:
        """Positive means the component helps."""
        return self.rps_without - self.rps_with

    @property
    def relative_gain(self) -> float:
        return self.gain / self.rps_without if self.rps_without else 0.0

    def render(self) -> str:
        verdict = "apport confirmé" if self.gain > 0 else "APPORT NON CONFIRMÉ"
        return (
            f"{self.component:<34} avec {self.rps_with:.5f}  sans {self.rps_without:.5f}  "
            f"gain {self.gain:+.5f} ({self.relative_gain * 100:+.2f}%)  → {verdict}"
        )


def block_bootstrap_interval(
    values: Sequence[float],
    *,
    block: int = 10,
    resamples: int = 2000,
    level: float = 0.95,
    seed: int = 0,
) -> tuple[float, float]:
    """Confidence interval for a mean under serial dependence.

    Football results arrive in correlated waves — form runs, congested weeks,
    a whole matchday played in the same weather — so an i.i.d. bootstrap would
    understate the spread.  Resampling contiguous blocks keeps that structure.
    """
    if not values:
        raise ValueError("aucune valeur à ré-échantillonner")
    if block < 1:
        raise ValueError("la taille de bloc doit être positive")
    rng = random.Random(seed)
    n = len(values)
    span = min(block, n)
    means: list[float] = []
    for _ in range(resamples):
        drawn: list[float] = []
        while len(drawn) < n:
            start = rng.randrange(0, max(1, n - span + 1))
            drawn.extend(values[start : start + span])
        means.append(math.fsum(drawn[:n]) / n)
    means.sort()
    tail = (1.0 - level) / 2.0
    low = means[max(0, int(tail * resamples) - 1)]
    high = means[min(resamples - 1, int((1.0 - tail) * resamples))]
    return (low, high)


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """Chronological scores, per fold and pooled, with their uncertainty."""

    competition: str
    folds: tuple[ChronoFold, ...] = field(repr=False, default=())
    cards: tuple[ScoreCard, ...] = ()
    baseline: ScoreCard | None = None
    tuned: tuple[tuple[dt.date, float, float], ...] = ()
    ablations: tuple[AblationResult, ...] = ()
    rps_interval: tuple[float, float] | None = None
    scored: int = 0

    def render(self) -> str:
        lines = [
            f"VALIDATION CHRONOLOGIQUE — {self.competition}",
            f"  {len(self.folds)} plis, {self.scored} rencontres évaluées hors échantillon",
            "─" * 84,
        ]
        for card in sorted(self.cards, key=lambda c: c.rps):
            line = f"  {card}"
            if self.baseline and card.name != self.baseline.name:
                line += f"  skill {card.skill_against(self.baseline) * 100:+.2f}%"
            lines.append(line)
        if self.rps_interval:
            low, high = self.rps_interval
            lines.append(
                f"  RPS du modèle, IC 95% par bootstrap par blocs : "
                f"[{low:.5f}, {high:.5f}] — la dépendance des observations est prise en compte"
            )
        if self.tuned:
            lines.append("─" * 84)
            lines.append("  réglages choisis DANS chaque fenêtre d'entraînement :")
            for cutoff, half_life, ridge in self.tuned:
                lines.append(
                    f"    au {cutoff.isoformat()} : demi-vie {half_life:g} j, ridge {ridge:g}"
                )
        if self.ablations:
            lines.append("─" * 84)
            lines.append("  apport de chaque composant (retiré un par un) :")
            lines.extend(f"    {a.render()}" for a in self.ablations)
        lines.append("─" * 84)
        lines.append(
            "  Aucun avantage de rentabilité n'est revendiqué ici : sans cotes "
            "disponibles au moment de la décision, seule la qualité probabiliste "
            "est mesurée."
        )
        return "\n".join(lines)


def validate(
    matches: MatchLog,
    *,
    competition: str = "",
    folds: int = 5,
    min_train: int = 300,
    tune: bool = True,
    ablate: bool = True,
    progress: Callable[[int, int], None] | None = None,
) -> ValidationReport:
    """Run an expanding-window validation, tuning inside each training window."""
    built = chronological_folds(matches, folds=folds, min_train=min_train)
    tuned: list[tuple[dt.date, float, float]] = []
    forecasts: dict[str, list[OutcomeProbabilities]] = {
        "dixon-coles réglé": [], "dixon-coles fixe": [], "poisson": [], "taux de base": [],
    }
    outcomes: list[Outcome] = []
    per_match_rps: list[float] = []

    for position, fold in enumerate(built):
        half_life, ridge = (
            tune_chronologically(fold.train) if tune else (240.0, 0.3)
        )
        tuned.append((fold.cutoff, half_life, ridge))

        variants = {
            "dixon-coles réglé": DixonColesModel(half_life_days=half_life, ridge=ridge),
            "dixon-coles fixe": DixonColesModel(half_life_days=240.0),
            "poisson": DixonColesModel(half_life_days=240.0, low_score_correction=False),
        }
        fitted: dict[str, DixonColesModel | None] = {}
        for name, model in variants.items():
            try:
                fitted[name] = model.fit(fold.train, reference_date=fold.cutoff)
            except (ValueError, RuntimeError):
                fitted[name] = None

        counts = [0.0, 0.0, 0.0]
        for match in fold.train:
            counts[match.outcome.index] += 1.0
        base = OutcomeProbabilities.normalised(*(c + 1.0 for c in counts))

        for match in fold.test:
            outcomes.append(match.outcome)
            forecasts["taux de base"].append(base)
            for name, candidate in fitted.items():
                if candidate is None:
                    forecasts[name].append(base)
                    continue
                try:
                    forecasts[name].append(candidate.predict(match.fixture))
                except KeyError:
                    forecasts[name].append(base)
        if progress:
            progress(position + 1, len(built))

    cards = tuple(
        ScoreCard.evaluate(values, outcomes, name=name)
        for name, values in forecasts.items()
        if values
    )
    baseline = next((c for c in cards if c.name == "taux de base"), None)
    per_match_rps = [
        ranked_probability_score(f, o)
        for f, o in zip(forecasts["dixon-coles réglé"], outcomes, strict=True)
    ]
    interval = block_bootstrap_interval(per_match_rps) if per_match_rps else None

    ablations: tuple[AblationResult, ...] = ()
    if ablate and baseline is not None:
        by_name = {c.name: c for c in cards}
        tuned_card = by_name.get("dixon-coles réglé")
        results: list[AblationResult] = []
        if tuned_card and "poisson" in by_name:
            results.append(
                AblationResult(
                    "correction bas scores (Dixon-Coles)",
                    tuned_card.rps,
                    by_name["poisson"].rps,
                )
            )
        if tuned_card and "dixon-coles fixe" in by_name:
            results.append(
                AblationResult(
                    "réglage chronologique des paramètres",
                    tuned_card.rps,
                    by_name["dixon-coles fixe"].rps,
                )
            )
        if tuned_card:
            results.append(
                AblationResult("modèle d'équipes (vs taux de base)", tuned_card.rps, baseline.rps)
            )
        ablations = tuple(results)

    return ValidationReport(
        competition=competition or "compétition",
        folds=tuple(built),
        cards=cards,
        baseline=baseline,
        tuned=tuple(tuned),
        ablations=ablations,
        rps_interval=interval,
        scored=len(outcomes),
    )
