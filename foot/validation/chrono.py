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
    "paired_difference_interval",
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
        following = position + step
        end = usable[following] if index < folds - 1 and following < len(usable) else None
        train = matches.before(cutoff)
        # Half-open window [cutoff, end): `between` is inclusive at both ends, so
        # using it made consecutive folds share their boundary date and score the
        # same matches twice — 180 distinct fixtures produced 186 evaluations.
        test = matches.after(cutoff).before(end) if end else matches.after(cutoff)
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
    """What one component contributes, measured by removing **it alone**.

    Two disciplines make the number mean something.

    First, :attr:`varied` must name exactly one dimension.  Comparing a tuned
    Dixon-Coles against a fixed Poisson changes the correction, the half-life
    *and* the shrinkage at once, so whatever the difference shows, it does not
    show what the correction is worth.

    Second, the verdict rests on the **uncertainty of the difference**, not on
    its sign.  Scores are paired match by match and bootstrapped in blocks, so
    a gain whose interval straddles zero is reported as unconfirmed however
    positive its point estimate.
    """

    component: str
    varied: tuple[str, ...]
    rps_with: float
    rps_without: float
    difference_interval: tuple[float, float] | None = None
    paired_differences: tuple[float, ...] = field(default=(), repr=False)

    def __post_init__(self) -> None:
        if not self.varied:
            raise ValueError(f"{self.component} : l'ablation ne déclare pas ce qui varie")
        if len(self.varied) != 1:
            raise ValueError(
                f"{self.component} : {len(self.varied)} dimensions varient "
                f"({', '.join(self.varied)}) — ce n'est pas une ablation"
            )

    @property
    def gain(self) -> float:
        """Positive means the component helps (lower RPS with it than without)."""
        return self.rps_without - self.rps_with

    @property
    def relative_gain(self) -> float:
        return self.gain / self.rps_without if self.rps_without else 0.0

    @property
    def confirmed(self) -> bool:
        """True only when the whole interval of the difference sits above zero."""
        if self.difference_interval is None:
            return False
        low, _high = self.difference_interval
        return self.gain > 0.0 and low > 0.0

    def render(self) -> str:
        verdict = "apport confirmé" if self.confirmed else "APPORT NON CONFIRMÉ"
        interval = (
            f"IC95 [{self.difference_interval[0]:+.5f}, {self.difference_interval[1]:+.5f}]"
            if self.difference_interval
            else "IC non calculé"
        )
        return (
            f"{self.component:<38} varie : {self.varied[0]:<22} "
            f"avec {self.rps_with:.5f}  sans {self.rps_without:.5f}  "
            f"gain {self.gain:+.5f}  {interval}  → {verdict}"
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


def paired_difference_interval(
    left: Sequence[float],
    right: Sequence[float],
    *,
    block: int = 10,
    resamples: int = 2000,
    level: float = 0.95,
    seed: int = 0,
) -> tuple[float, float]:
    """Confidence interval for the mean *paired* difference ``right - left``.

    Pairing match by match removes the fixture-to-fixture difficulty that both
    models face equally, which is most of the variance.  Comparing two
    independent intervals instead would be far too conservative — and comparing
    point estimates alone, far too generous.
    """
    if len(left) != len(right):
        raise ValueError("les séries appariées doivent avoir la même longueur")
    differences = [b - a for a, b in zip(left, right, strict=True)]
    return block_bootstrap_interval(
        differences, block=block, resamples=resamples, level=level, seed=seed
    )


@dataclass(frozen=True, slots=True)
class _Variant:
    """One fitted configuration, and the single knob that distinguishes it."""

    name: str
    half_life: float | None
    ridge: float
    correction: bool


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
    """Run an expanding-window validation, tuning inside each training window.

    Ablations vary **one** dimension at a time against a common reference, and
    each verdict comes with the bootstrap interval of the paired difference.
    """
    built = chronological_folds(matches, folds=folds, min_train=min_train)
    reference = _Variant("référence", 240.0, 0.3, True)
    variants = [
        reference,
        _Variant("sans correction bas scores", 240.0, 0.3, False),
        _Variant("sans décroissance temporelle", None, 0.3, True),
        _Variant("sans régularisation", 240.0, 0.0, True),
    ]

    tuned: list[tuple[dt.date, float, float]] = []
    forecasts: dict[str, list[OutcomeProbabilities]] = {v.name: [] for v in variants}
    forecasts["réglé (demi-vie)"] = []
    forecasts["réglé (régularisation)"] = []
    forecasts["taux de base"] = []
    outcomes: list[Outcome] = []

    for position, fold in enumerate(built):
        half_life, ridge = (
            tune_chronologically(fold.train) if tune else (240.0, 0.3)
        )
        tuned.append((fold.cutoff, half_life, ridge))
        # Two extra variants, each differing from the reference in exactly one
        # knob, so "what did tuning buy?" can be answered per knob.
        run_variants = [
            *variants,
            _Variant("réglé (demi-vie)", half_life, reference.ridge, True),
            _Variant("réglé (régularisation)", reference.half_life, ridge, True),
        ]

        fitted: dict[str, DixonColesModel | None] = {}
        for variant in run_variants:
            try:
                fitted[variant.name] = DixonColesModel(
                    half_life_days=variant.half_life,
                    ridge=variant.ridge,
                    low_score_correction=variant.correction,
                ).fit(fold.train, reference_date=fold.cutoff)
            except (ValueError, RuntimeError):
                fitted[variant.name] = None

        counts = [0.0, 0.0, 0.0]
        for match in fold.train:
            counts[match.outcome.index] += 1.0
        base = OutcomeProbabilities.normalised(*(c + 1.0 for c in counts))

        for match in fold.test:
            outcomes.append(match.outcome)
            forecasts["taux de base"].append(base)
            for name, model in fitted.items():
                if model is None:
                    forecasts[name].append(base)
                    continue
                try:
                    forecasts[name].append(model.predict(match.fixture))
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

    def per_match(name: str) -> list[float]:
        return [
            ranked_probability_score(f, o)
            for f, o in zip(forecasts[name], outcomes, strict=True)
        ]

    reference_scores = per_match(reference.name)
    interval = block_bootstrap_interval(reference_scores) if reference_scores else None

    ablations: tuple[AblationResult, ...] = ()
    if ablate:
        by_name = {c.name: c for c in cards}
        # Each pair names the run that *has* the component and the run that
        # lacks it.  For the two tuning pairs the reference is the one *without*
        # tuning, so the names are the other way round — getting that backwards
        # once made "tuning helps" out of a result that said the opposite.
        pairs = (
            ("correction bas scores (rho)", reference.name, "sans correction bas scores",
             "low_score_correction"),
            ("décroissance temporelle", reference.name, "sans décroissance temporelle",
             "half_life_days"),
            ("régularisation (ridge)", reference.name, "sans régularisation", "ridge"),
            ("réglage chronologique de la demi-vie", "réglé (demi-vie)", reference.name,
             "half_life_days (réglé vs fixe)"),
            ("réglage chronologique du ridge", "réglé (régularisation)", reference.name,
             "ridge (réglé vs fixe)"),
            ("modèle d'équipes", reference.name, "taux de base", "modèle complet"),
        )
        results: list[AblationResult] = []
        for label, with_name, without_name, knob in pairs:
            if with_name not in by_name or without_name not in by_name:
                continue
            with_scores = per_match(with_name)
            without_scores = per_match(without_name)
            results.append(
                AblationResult(
                    component=label,
                    varied=(knob,),
                    rps_with=by_name[with_name].rps,
                    rps_without=by_name[without_name].rps,
                    difference_interval=paired_difference_interval(
                        with_scores, without_scores
                    ),
                    paired_differences=tuple(
                        b - a for a, b in zip(with_scores, without_scores, strict=True)
                    ),
                )
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
