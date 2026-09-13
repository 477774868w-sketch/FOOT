"""Choosing the main bet: criteria first, evaluation second.

The ranking rules are declared in a :class:`RankingCriteria` *before* any offer
is scored, they are carried into the report, and they are never edited after the
results are seen.  That ordering is the whole point: rules rewritten downstream
of the outcome make any backtest of the selection meaningless.

A market wins on three things together, not on probability alone:

* **price** — expected value, computed over the real settlement profile;
* **risk** — expected log growth at a reference stake, which charges for
  variance where raw expected value does not;
* **robustness** — the worst expected value across credible sporting scenarios,
  so a pick that only survives the central estimate cannot be recommended.

When no price is supplied the selector does not pretend to find value.  It
returns the sporting angle and a **price condition** — the odds at which the bet
would become worth taking — clearly marked as unverified.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum

from foot.markets.catalogue import MarketFamily, MarketOffer
from foot.markets.pricing import PricedOffer, price_offer
from foot.models.base import ScoreMatrix
from foot.provenance import utcnow

__all__ = [
    "Confidence",
    "Decision",
    "DecisionStatus",
    "RankingCriteria",
    "Scenario",
    "ScenarioKind",
    "ScoredOffer",
    "select_best",
]


class ScenarioKind(Enum):
    """Why an alternative picture is being considered — three different things.

    Conflating them overstates what the analysis has shown.  A fixed ±15 % shift
    is a *sensitivity* probe: it says how fragile a price is to an assumption,
    not that the assumption is likely.  Estimation noise is a different animal,
    derived from how much data actually backs each rating.  And a documented
    sporting event — a suspension, a confirmed absence — is a third, and the only
    one that may be described as a credible worst case, because something was
    actually reported.
    """

    SENSITIVITY = "sensibilité"
    """A deliberate, arbitrary perturbation. Says nothing about likelihood."""

    MODEL_UNCERTAINTY = "incertitude d'estimation"
    """Derived from the effective sample behind the ratings."""

    SPORTING_EVENT = "événement sportif documenté"
    """Backed by evidence; the only kind that supports a worst-case claim."""


@dataclass(frozen=True, slots=True)
class Scenario:
    """An alternative picture, with the reason it is being considered."""

    name: str
    matrix: ScoreMatrix
    kind: ScenarioKind = ScenarioKind.SENSITIVITY
    basis: str = ""
    """What the scenario rests on — required, so none can be silently arbitrary."""

    evidence_keys: tuple[str, ...] = ()
    note: str = ""

    def __post_init__(self) -> None:
        if self.kind is ScenarioKind.SPORTING_EVENT and not self.evidence_keys:
            raise ValueError(
                f"« {self.name} » est présenté comme un événement sportif documenté "
                f"mais ne cite aucune source"
            )


class Confidence(Enum):
    """How well founded the recommendation is — **not** its chance of winning.

    A 75% favourite backed on thin evidence is confidence C; a 40% shot backed
    on a complete dossier at a clearly wrong price can be confidence A.  The two
    axes are reported separately because conflating them is how a well-priced
    bet gets talked out of and a badly-priced one gets talked into.
    """

    A = "A — dossier complet, marge nette, robuste aux scénarios"
    B = "B — dossier solide, marge réelle mais sensible à une hypothèse"
    C = "C — lacunes documentées ou marge étroite"
    D = "D — information insuffisante pour engager"

    @property
    def letter(self) -> str:
        return self.name


class DecisionStatus(Enum):
    """What the selector concluded."""

    RECOMMENDED = "recommandé"
    PRICE_CONDITION = "angle sportif, sous condition de prix"
    NO_BET = "aucun pari"
    BLOCKED = "analyse impossible"


@dataclass(frozen=True, slots=True)
class RankingCriteria:
    """The rules, fixed before evaluation and carried into the report."""

    min_expected_value: float = 0.03
    """Minimum expected profit per unit staked to consider a bet at all."""

    min_worst_case_value: float = -0.01
    """Floor on expected value across scenarios: robustness, not optimism."""

    min_win_probability: float = 0.0
    """Optional floor; zero by default because a low-probability bet can be right."""

    max_stale_hours: float = 12.0
    reference_stake: float = 0.02
    """Common stake at which every market's log growth is compared."""

    exclude_families: frozenset[MarketFamily] = frozenset()
    gate_kinds: frozenset[ScenarioKind] = frozenset(
        {ScenarioKind.SENSITIVITY, ScenarioKind.SPORTING_EVENT}
    )
    """Scenario kinds allowed to *reject* a bet.

    Estimation noise is deliberately excluded: it is a two-sided band around the
    estimate, not an adverse event, and using it as a floor would reject every
    market on any realistic sample while pretending the rejection was about
    football.  It is measured and reported instead — see
    :attr:`ScoredOffer.uncertainty_range`.
    """

    version: str = "criteres-v2"
    declared_at: dt.datetime = field(default_factory=utcnow)

    def describe(self) -> str:
        return (
            f"Critères {self.version} (déclarés le "
            f"{self.declared_at.strftime('%Y-%m-%d %H:%M %Z')}, avant évaluation) : "
            f"EV ≥ {self.min_expected_value:+.1%}, "
            f"EV pire scénario ≥ {self.min_worst_case_value:+.1%}, "
            f"p ≥ {self.min_win_probability:.0%}, "
            f"cote de moins de {self.max_stale_hours:g} h, "
            f"classement par croissance logarithmique à {self.reference_stake:.0%} de mise."
        )


@dataclass(frozen=True, slots=True)
class ScoredOffer:
    """A priced offer with its stress results and its rank score."""

    priced: PricedOffer
    worst_case_value: float | None
    worst_case_scenario: str
    worst_case_kind: ScenarioKind | None = None
    uncertainty_range: tuple[float, float] | None = None
    """Expected value under ±1 σ of estimation noise — reported, never a filter."""

    log_growth: float | None = None
    passed: bool = False
    rejection: str = ""

    @property
    def offer(self) -> MarketOffer:
        return self.priced.offer

    @property
    def expected_value(self) -> float | None:
        return self.priced.expected_value

    def render(self) -> str:
        line = self.priced.render()
        if self.worst_case_value is not None:
            line += f"  pire {self.worst_case_value * 100:+6.2f}% ({self.worst_case_scenario})"
        if not self.passed and self.rejection:
            line += f"  ✗ {self.rejection}"
        return line


@dataclass(frozen=True, slots=True)
class Decision:
    """The outcome of comparing every market available on one match."""

    status: DecisionStatus
    criteria: RankingCriteria
    main: ScoredOffer | None = None
    runners_up: tuple[ScoredOffer, ...] = ()
    rejected: tuple[ScoredOffer, ...] = ()
    confidence: Confidence = Confidence.D
    rationale: str = ""
    main_risk: str = ""
    cancellation: str = ""
    price_condition: str = ""
    reason: str = ""
    compared: tuple[str, ...] = ()
    """Labels of the priced markets actually put in competition.

    An unpriced market cannot be compared, so naming what *was* compared stops a
    reader assuming the whole catalogue was weighed when only part of it carried
    a price.
    """

    alternatives: tuple[tuple[str, str], ...] = ()
    """``(déclencheur, repli)`` — an alternative only earns its place if it
    answers a stated change in price, risk or scenario."""

    @property
    def has_bet(self) -> bool:
        return self.status is DecisionStatus.RECOMMENDED and self.main is not None

    def headline(self) -> str:
        if self.main is None:
            return f"{self.status.value} — {self.reason}"
        odds = self.main.offer.odds
        price = f" @ {odds:.2f}" if odds else ""
        return f"{self.main.offer.label}{price} [{self.confidence.letter}]"


def _dominance(best: ScoredOffer, others: Sequence[ScoredOffer]) -> str:
    """Say *why* the winner beat the specific markets it was compared against."""
    if best.expected_value is None:
        return ""
    beaten: list[str] = []
    for other in others[:3]:
        if other.expected_value is None:
            continue
        gap = (best.expected_value - other.expected_value) * 100
        robust = ""
        if other.worst_case_value is not None and best.worst_case_value is not None:
            delta = (best.worst_case_value - other.worst_case_value) * 100
            if delta > 0.5:
                robust = f", et {delta:+.1f} pts en pire scénario"
        beaten.append(f"{other.offer.label} ({gap:+.1f} pts d'espérance{robust})")
    if not beaten:
        return "aucun autre marché coté ne remplissait les critères."
    return "domine " + " ; ".join(beaten) + "."


def _confidence_for(
    best: ScoredOffer,
    *,
    rubric_coverage: float,
    model_converged: bool,
    contradictions: int,
) -> Confidence:
    """Grade the *foundation* of the pick, deliberately ignoring its probability.

    Grade D means "information insuffisante pour engager".  A recommendation
    carrying it would contradict its own diagnosis, so :func:`select_best`
    declines rather than publishing both.
    """
    if not model_converged or best.expected_value is None:
        return Confidence.D
    worst = best.worst_case_value if best.worst_case_value is not None else -1.0
    if contradictions:
        return Confidence.C if worst > 0.0 else Confidence.D
    if rubric_coverage >= 0.70 and worst >= 0.02 and best.expected_value >= 0.05:
        return Confidence.A
    if rubric_coverage >= 0.45 and worst >= 0.0:
        return Confidence.B
    if worst >= -0.01:
        return Confidence.C
    return Confidence.D


def select_best(
    priced: Sequence[PricedOffer],
    *,
    criteria: RankingCriteria,
    scenarios: Sequence[Scenario] = (),
    rubric_coverage: float = 0.0,
    model_converged: bool = True,
    contradictions: int = 0,
    sport_angle: str = "",
) -> Decision:
    """Compare every market and return one decision, with its reasoning.

    Args:
        priced: the catalogue, already settled against the sealed dossier.
        criteria: declared *before* this call; echoed into the result.
        scenarios: alternative score matrices used to stress each candidate.
        rubric_coverage: share of the 22 rubrics actually answered, which drives
            confidence but never the probability.
        sport_angle: the sporting read, used when no price is available.
    """
    if not model_converged:
        # A model that did not converge has no usable probabilities, so nothing
        # downstream of it can be recommended — whatever the prices say.
        return Decision(
            status=DecisionStatus.BLOCKED,
            criteria=criteria,
            confidence=Confidence.D,
            reason=(
                "le modèle n'a pas convergé : aucune probabilité exploitable, "
                "donc aucune recommandation"
            ),
            rationale=sport_angle,
        )

    quoted = [p for p in priced if p.has_price]
    if not quoted:
        best_model = max(priced, key=lambda p: p.win_probability, default=None)
        condition = ""
        if best_model is not None and math.isfinite(best_model.fair_odds):
            # Solved on the settlement profile: scaling the break-even price is
            # wrong as soon as the market can refund.
            required = best_model.profile.odds_for_expected_value(
                criteria.min_expected_value
            )
            condition = (
                f"{best_model.offer.label} : équitable à {best_model.fair_odds:.2f}, "
                f"intéressant à partir de {required:.2f} "
                f"(marge exigée {criteria.min_expected_value:+.0%}). "
                f"Valeur NON vérifiée : aucune cote fournie."
            )
        return Decision(
            status=DecisionStatus.PRICE_CONDITION,
            criteria=criteria,
            confidence=Confidence.C if sport_angle else Confidence.D,
            reason="aucune cote fournie : la valeur ne peut pas être établie",
            rationale=sport_angle,
            price_condition=condition,
        )

    scored: list[ScoredOffer] = []
    for item in quoted:
        assert item.offer.odds is not None
        value = item.expected_value
        worst, worst_name = value, "estimation centrale"
        worst_kind: ScenarioKind | None = None
        band: list[float] = []
        for scenario in scenarios:
            stressed = price_offer(item.offer, scenario.matrix)
            candidate = stressed.expected_value(item.offer.odds)
            if scenario.kind is ScenarioKind.MODEL_UNCERTAINTY:
                band.append(candidate)
                continue
            if scenario.kind not in criteria.gate_kinds:
                continue
            if worst is None or candidate < worst:
                worst, worst_name, worst_kind = candidate, scenario.name, scenario.kind
        uncertainty = (min(band), max(band)) if band else None
        growth = item.log_growth(criteria.reference_stake)

        rejection = ""
        if item.offer.family in criteria.exclude_families:
            rejection = "famille exclue par les critères"
        elif item.is_stale(limit_hours=criteria.max_stale_hours):
            age = item.price_age
            hours = age.total_seconds() / 3600.0 if age else 0.0
            rejection = f"cote périmée ({hours:.1f} h)"
        elif value is None or value < criteria.min_expected_value:
            rejection = f"EV {0.0 if value is None else value:+.2%} sous le seuil"
        elif worst is not None and worst < criteria.min_worst_case_value:
            rejection = f"fragile : {worst:+.2%} en sensibilité « {worst_name} »"
        elif item.win_probability < criteria.min_win_probability:
            rejection = f"probabilité {item.win_probability:.1%} sous le plancher"

        scored.append(
            ScoredOffer(
                priced=item,
                worst_case_value=worst,
                worst_case_scenario=worst_name,
                worst_case_kind=worst_kind,
                uncertainty_range=uncertainty,
                log_growth=growth,
                passed=not rejection,
                rejection=rejection,
            )
        )

    survivors = [s for s in scored if s.passed]
    rejected = tuple(
        sorted(
            (s for s in scored if not s.passed),
            key=lambda s: -(s.expected_value or -9.9),
        )
    )

    if not survivors:
        closest = rejected[0] if rejected else None
        reason = (
            f"aucun marché ne franchit les critères ; le plus proche est "
            f"{closest.offer.label} ({closest.rejection})"
            if closest
            else "aucun marché évaluable"
        )
        return Decision(
            status=DecisionStatus.NO_BET,
            criteria=criteria,
            rejected=rejected,
            confidence=Confidence.D,
            reason=reason,
            rationale=sport_angle,
            compared=tuple(s.offer.label for s in scored),
        )

    survivors.sort(key=lambda s: -(s.log_growth if s.log_growth is not None else -math.inf))
    best, others = survivors[0], survivors[1:]
    confidence = _confidence_for(
        best,
        rubric_coverage=rubric_coverage,
        model_converged=model_converged,
        contradictions=contradictions,
    )

    alternatives: list[tuple[str, str]] = []
    for other in others[:2]:
        if other.offer.family is best.offer.family:
            trigger = "si la cote du choix principal baisse sous son seuil"
        elif other.priced.profile.push_probability > best.priced.profile.push_probability:
            trigger = "si vous voulez réduire le risque (remboursement possible)"
        else:
            trigger = "si le scénario défensif se confirme (compositions prudentes)"
        alternatives.append((trigger, other.offer.label))

    compared = tuple(s.offer.label for s in scored)
    if confidence is Confidence.D:
        # The grade says the dossier cannot support a stake; publishing a
        # recommendation alongside it would contradict the diagnosis.
        return Decision(
            status=DecisionStatus.NO_BET,
            criteria=criteria,
            rejected=rejected[:6],
            confidence=Confidence.D,
            reason=(
                f"le prix de {best.offer.label} passerait les seuils, mais le "
                f"dossier est noté D (information insuffisante pour engager) : "
                f"aucune recommandation n'est émise"
            ),
            rationale=_dominance(best, others),
            compared=compared,
        )

    kind = best.worst_case_kind
    label = kind.value if kind is not None else "estimation centrale"
    worst_text = (
        f"{best.worst_case_value:+.2%} sous « {best.worst_case_scenario} » ({label})"
        if best.worst_case_value is not None
        else "non évalué"
    )
    threshold = best.priced.profile.odds_for_expected_value(criteria.min_expected_value)
    return Decision(
        status=DecisionStatus.RECOMMENDED,
        criteria=criteria,
        main=best,
        runners_up=tuple(others[:3]),
        rejected=rejected[:6],
        confidence=confidence,
        rationale=_dominance(best, others),
        main_risk=(
            f"espérance la plus basse en analyse de sensibilité : {worst_text}"
            + (
                f" · incertitude d'estimation : "
                f"[{best.uncertainty_range[0]:+.2%}, {best.uncertainty_range[1]:+.2%}]"
                if best.uncertainty_range
                else ""
            )
        ),
        cancellation=(
            f"annuler si la cote passe sous {threshold:.2f} "
            f"(seuil résolu sur le profil de règlement exact, remboursements "
            f"compris), ou si une information sportive décisive invalide le "
            f"dossier scellé"
        ),
        alternatives=tuple(alternatives),
        compared=compared,
    )
