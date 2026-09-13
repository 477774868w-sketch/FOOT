"""Staking and tickets: verified exclusivity, explicit budgets, no assumed bankroll.

Two things are settled here that the numeric Kelly routine cannot settle on its
own.

**Exclusivity is verified, not assumed.**  Given only probabilities and odds,
nothing distinguishes two genuinely exclusive outcomes from two overlapping bets
whose probabilities happen to sum to one — and the second, sized as the first,
over-stakes badly.  With the offers themselves in hand the question is decidable:
two selections are exclusive exactly when no scoreline in the grid wins both, so
:func:`verify_exclusive` checks the grid and refuses when they overlap.

**Money is only ever what the operator stated.**  There is no default bankroll.
Every stake is a share of a budget passed in explicitly, capped by configurable
limits, and a confidence grade never converts into a larger stake by itself.
This module recommends; it never places anything.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

from foot.markets.catalogue import MarketOffer
from foot.markets.selection import Confidence, Decision
from foot.markets.settlement import SettlementProfile, unit_return
from foot.models.base import ScoreMatrix

__all__ = [
    "OverlapError",
    "Stake",
    "StakePlan",
    "Ticket",
    "TicketLeg",
    "build_ticket",
    "plan_stakes",
    "verify_exclusive",
]


class OverlapError(ValueError):
    """Selections presented as exclusive can in fact win together."""


def verify_exclusive(offers: Sequence[MarketOffer], matrix: ScoreMatrix) -> None:
    """Raise unless no scoreline lets two of ``offers`` win together.

    This is the check the probability-only API cannot perform.  It is exact: a
    pair overlaps precisely when some cell of the grid settles both as wins.
    """
    for i, first in enumerate(offers):
        for second in offers[i + 1 :]:
            for home, row in enumerate(matrix.grid):
                for away, probability in enumerate(row):
                    if probability <= 0.0:
                        continue
                    wins_first = first.unit_result(home, away) > 0.0
                    if wins_first and second.unit_result(home, away) > 0.0:
                        raise OverlapError(
                            f"« {first.label} » et « {second.label} » gagnent tous deux "
                            f"sur le score {home}-{away} : ces sélections ne sont pas "
                            f"exclusives et ne peuvent pas être dimensionnées comme un "
                            f"portefeuille d'issues mutuellement exclusives"
                        )


@dataclass(frozen=True, slots=True)
class Stake:
    """One recommended stake, in the operator's own currency units."""

    label: str
    odds: float
    amount: float
    fraction_of_budget: float
    expected_value: float
    rationale: str = ""

    def render(self) -> str:
        return (
            f"{self.label:<40} @ {self.odds:5.2f}  mise {self.amount:8.2f} "
            f"({self.fraction_of_budget * 100:4.1f}% du budget)  "
            f"EV {self.expected_value * 100:+.1f}%"
        )


@dataclass(frozen=True, slots=True)
class StakePlan:
    """Stakes for a batch of decisions, inside a stated budget."""

    budget: float
    stakes: tuple[Stake, ...]
    kelly_fraction: float
    max_single: float
    notes: tuple[str, ...] = ()

    @property
    def committed(self) -> float:
        return math.fsum(s.amount for s in self.stakes)

    def render(self) -> str:
        lines = [
            f"MISES — budget déclaré {self.budget:.2f} "
            f"(Kelly ×{self.kelly_fraction:g}, plafond {self.max_single * 100:.0f}% par pari)",
            "─" * 84,
        ]
        lines.extend(f"  {s.render()}" for s in self.stakes)
        if not self.stakes:
            lines.append("  aucun pari retenu : rien n'est engagé")
        lines.append("─" * 84)
        lines.append(
            f"  engagé {self.committed:.2f} / {self.budget:.2f} "
            f"({self.committed / self.budget * 100:.1f}%) — le reste n'est pas misé"
        )
        lines.extend(f"  {note}" for note in self.notes)
        lines.append("  Le logiciel analyse et recommande ; il ne place aucun pari.")
        return "\n".join(lines)


def plan_stakes(
    decisions: Sequence[tuple[str, Decision]],
    *,
    budget: float,
    kelly_fraction: float = 0.25,
    max_single: float = 0.05,
    max_total: float = 0.25,
) -> StakePlan:
    """Size the recommended bets inside an explicit budget.

    Args:
        decisions: ``(label, decision)`` pairs; only recommendations are staked.
        budget: the operator's stated budget.  There is no default: a library
            that assumes a bankroll is guessing with someone else's money.
        kelly_fraction: Kelly multiplier; quarter-Kelly by default.
        max_single: hard cap per bet, as a share of budget.
        max_total: hard cap on everything committed in one run.

    Confidence does **not** enter the sizing.  It grades how well founded the
    analysis is, and turning a letter grade into a bigger stake would double
    count the evidence that already moved the probability.
    """
    if budget <= 0.0:
        raise ValueError("le budget doit être strictement positif et fourni explicitement")
    if not 0.0 < kelly_fraction <= 1.0:
        raise ValueError("la fraction de Kelly doit être dans (0, 1]")
    if not 0.0 < max_single <= 1.0 or not 0.0 < max_total <= 1.0:
        raise ValueError("les plafonds doivent être dans (0, 1]")

    sized: list[tuple[str, str, float, float, float, str]] = []
    for label, decision in decisions:
        if not decision.has_bet or decision.main is None:
            continue
        odds = decision.main.offer.odds
        value = decision.main.expected_value
        if odds is None or value is None or value <= 0.0:
            continue
        # Kelly on a single bet with refunds: solved on the real settlement
        # profile rather than the binary formula, so a push is not mistaken for
        # a loss when sizing.
        fraction = min(_single_kelly(decision.main.priced.profile, odds) * kelly_fraction,
                       max_single)
        sized.append(
            (label, decision.main.offer.label, odds, value, fraction, decision.rationale)
        )

    notes: list[str] = []
    total = math.fsum(item[4] for item in sized)
    scale = 1.0
    if total > max_total:
        scale = max_total / total
        notes.append(
            f"plafond global atteint : mises réduites d'un facteur {scale:.2f} "
            f"pour rester sous {max_total * 100:.0f}% du budget"
        )

    stakes = tuple(
        Stake(
            label=f"{label} — {offer_label}",
            odds=odds,
            amount=budget * fraction * scale,
            fraction_of_budget=fraction * scale,
            expected_value=value,
            rationale=rationale,
        )
        for label, offer_label, odds, value, fraction, rationale in sized
    )
    if any(d.confidence is Confidence.A for _, d in decisions):
        notes.append(
            "une confiance A n'augmente pas la mise : elle qualifie le dossier, "
            "pas l'avantage de prix"
        )
    return StakePlan(
        budget=budget,
        stakes=stakes,
        kelly_fraction=kelly_fraction,
        max_single=max_single,
        notes=tuple(notes),
    )


def _single_kelly(profile: SettlementProfile, odds: float) -> float:
    """Growth-optimal fraction for one bet, honouring refunds and half-stakes."""
    probabilities = profile.probabilities

    def derivative(fraction: float) -> float:
        total = 0.0
        for settlement, probability in probabilities.items():
            if probability <= 0.0:
                continue
            edge = unit_return(settlement.unit, odds) - 1.0
            wealth = 1.0 + fraction * edge
            if wealth <= 1e-12:
                return -math.inf
            total += probability * edge / wealth
        return total

    if derivative(0.0) <= 0.0:
        return 0.0
    worst = min(
        (s.unit for s, p in probabilities.items() if p > 0.0 and s.unit < 0.0),
        default=0.0,
    )
    high = 1.0 if worst == 0.0 else min(1.0, 0.999 / abs(worst) if worst else 1.0)
    if derivative(high) > 0.0:
        return high
    low = 0.0
    for _ in range(80):  # bisection on a concave, monotone derivative
        mid = 0.5 * (low + high)
        if derivative(mid) > 0.0:
            low = mid
        else:
            high = mid
    return 0.5 * (low + high)


@dataclass(frozen=True, slots=True)
class TicketLeg:
    """One selection inside a combination."""

    label: str
    match_label: str
    odds: float
    probability: float
    expected_value: float
    drop_first: bool = False
    """Marks the weakest leg — the one to remove if the ticket must be shortened."""


@dataclass(frozen=True, slots=True)
class Ticket:
    """A combination, built as short as it can be while staying justified."""

    legs: tuple[TicketLeg, ...]
    combined_odds: float
    naive_probability: float
    warnings: tuple[str, ...] = ()
    keep_single: tuple[str, ...] = field(default_factory=tuple)

    @property
    def independence_assumed(self) -> bool:
        return len({leg.match_label for leg in self.legs}) > 1

    def render(self) -> str:
        lines = [
            f"COMBINÉ — {len(self.legs)} sélection(s), cote {self.combined_odds:.2f}",
            "─" * 84,
        ]
        for leg in self.legs:
            mark = "  ← première à retirer" if leg.drop_first else ""
            lines.append(
                f"  {leg.match_label:<28} {leg.label:<30} @ {leg.odds:5.2f} "
                f"p={leg.probability * 100:4.1f}%{mark}"
            )
        lines.append("─" * 84)
        lines.append(
            f"  probabilité sous hypothèse d'indépendance : "
            f"{self.naive_probability * 100:.2f}%"
        )
        lines.extend(f"  ⚠ {warning}" for warning in self.warnings)
        for label in self.keep_single:
            lines.append(f"  → à garder en simple : {label}")
        return "\n".join(lines)


def build_ticket(
    decisions: Sequence[tuple[str, Decision]],
    *,
    max_legs: int = 3,
    min_expected_value: float = 0.03,
) -> Ticket:
    """Build the **shortest** combination that stays justified.

    ``max_legs`` is a ceiling, never a quota: legs are added only while they
    still clear the value threshold, and a ticket of one selection is a valid
    and frequent answer.  No leg is ever added to lift the price.
    """
    if max_legs < 1:
        raise ValueError("un combiné compte au moins une sélection")

    candidates: list[TicketLeg] = []
    for label, decision in decisions:
        if not decision.has_bet or decision.main is None:
            continue
        odds = decision.main.offer.odds
        value = decision.main.expected_value
        if odds is None or value is None or value < min_expected_value:
            continue
        candidates.append(
            TicketLeg(
                label=decision.main.offer.label,
                match_label=label,
                odds=odds,
                probability=decision.main.priced.win_probability,
                expected_value=value,
            )
        )

    candidates.sort(key=lambda leg: -leg.expected_value)
    chosen = candidates[:max_legs]
    warnings: list[str] = []
    keep_single: list[str] = []

    if not chosen:
        return Ticket(
            legs=(), combined_odds=1.0, naive_probability=0.0,
            warnings=("aucune sélection ne justifie un combiné",),
        )

    by_match: dict[str, int] = {}
    for leg in chosen:
        by_match[leg.match_label] = by_match.get(leg.match_label, 0) + 1
    duplicated = [match for match, count in by_match.items() if count > 1]
    if duplicated:
        warnings.append(
            "sélections issues d'une même rencontre ("
            + ", ".join(duplicated)
            + ") : elles sont dépendantes ; la cote combinée d'un bookmaker "
            "suppose l'indépendance et surévalue donc le gain espéré"
        )
    if len(chosen) > 1:
        warnings.append(
            "l'indépendance entre rencontres est une hypothèse, pas une certitude "
            "(météo, arbitrage, enjeux de journée corrèlent les résultats)"
        )
    if len(candidates) > len(chosen):
        keep_single.extend(leg.match_label for leg in candidates[len(chosen):])
        warnings.append(
            f"{len(candidates) - len(chosen)} sélection(s) retenue(s) hors combiné : "
            "à jouer en simple plutôt qu'allonger le ticket"
        )

    weakest = min(range(len(chosen)), key=lambda i: chosen[i].expected_value)
    legs = tuple(
        TicketLeg(
            label=leg.label, match_label=leg.match_label, odds=leg.odds,
            probability=leg.probability, expected_value=leg.expected_value,
            drop_first=(index == weakest and len(chosen) > 1),
        )
        for index, leg in enumerate(chosen)
    )
    combined = math.prod(leg.odds for leg in legs)
    naive = math.prod(leg.probability for leg in legs)
    return Ticket(
        legs=legs, combined_odds=combined, naive_probability=naive,
        warnings=tuple(warnings), keep_single=tuple(dict.fromkeys(keep_single)),
    )
