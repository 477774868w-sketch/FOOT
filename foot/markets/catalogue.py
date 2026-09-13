"""The market catalogue: what can be bet, and exactly how it settles.

Each offer carries a *unit-result function* over scorelines, so its settlement
is read off the joint distribution rather than assembled from marginals.  That
single choice makes three of the operator's requirements automatic:

* a **group of correct scores** is a union — the cells simply add up;
* a **combination of conditions on one match** is an intersection — a cell
  counts only if it satisfies every condition, so nothing is ever multiplied as
  though the conditions were independent;
* an **Asian quarter line** is the average of its two neighbours, which falls
  out of averaging two unit results.

Markets that genuinely cannot be derived from a final-score distribution —
cards, corners, goalscorers — are refused by name rather than approximated.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from enum import Enum

from foot.domain import Outcome, Score

__all__ = [
    "MarketFamily",
    "MarketOffer",
    "UnsupportedMarketError",
    "asian_handicap",
    "both_teams_to_score",
    "correct_score_group",
    "double_chance",
    "draw_no_bet",
    "match_result",
    "refuse_if_unsupported",
    "standard_catalogue",
    "team_total",
    "total_goals",
]


class MarketFamily(Enum):
    """Market families this engine prices from the scoreline distribution."""

    ONE_X_TWO = "1-N-2"
    DOUBLE_CHANCE = "double chance"
    DRAW_NO_BET = "remboursé si nul"
    ASIAN_HANDICAP = "handicap asiatique"
    TOTAL = "total de buts"
    TEAM_TOTAL = "total d'équipe"
    BTTS = "les deux marquent"
    CORRECT_SCORE = "score exact"
    COMBINATION = "combinaison même match"


_REFUSED = {
    "cartons": "les cartons dépendent de l'arbitre et du contexte disciplinaire",
    "corners": "les corners dépendent du volume de jeu, pas du score final",
    "buteur": "un buteur dépend de la répartition des buts dans l'effectif",
    "cards": "les cartons dépendent de l'arbitre et du contexte disciplinaire",
    "corner": "les corners dépendent du volume de jeu, pas du score final",
    "scorer": "un buteur dépend de la répartition des buts dans l'effectif",
}


class UnsupportedMarketError(ValueError):
    """A market that cannot honestly be derived from the final-score law."""


def refuse_if_unsupported(name: str) -> None:
    """Raise for markets needing their own model, instead of approximating them."""
    lowered = name.lower()
    for token, reason in _REFUSED.items():
        if token in lowered:
            raise UnsupportedMarketError(
                f"« {name} » ne se déduit pas de la loi des scores finaux : {reason}. "
                f"Un modèle dédié est requis ; aucune estimation n'est produite."
            )


UnitResult = Callable[[int, int], float]
"""``(buts domicile, buts extérieur) -> +1 gagné / 0 remboursé / -1 perdu``."""


@dataclass(frozen=True, slots=True)
class MarketOffer:
    """One bettable selection, with its settlement rule stated in full."""

    key: str
    family: MarketFamily
    label: str
    unit_result: UnitResult = field(repr=False)
    rule: str = ""
    line: float | None = None
    components: tuple[MarketOffer, ...] = ()
    """Neighbouring lines a quarter line splits across, if any."""

    odds: float | None = None
    bookmaker: str | None = None

    def with_odds(self, odds: float, *, bookmaker: str | None = None) -> MarketOffer:
        """Attach a price.  Offers are created blind; prices arrive later."""
        if odds <= 1.0:
            raise ValueError(f"cote décimale invalide pour {self.key} : {odds!r}")
        return MarketOffer(
            key=self.key,
            family=self.family,
            label=self.label,
            unit_result=self.unit_result,
            rule=self.rule,
            line=self.line,
            components=self.components,
            odds=odds,
            bookmaker=bookmaker,
        )

    def combined_with(self, other: MarketOffer) -> MarketOffer:
        """Intersection with another condition **on the same match**.

        A cell counts only when both conditions win there, which is what makes
        this an intersection over the joint law rather than a product of two
        probabilities.

        Precedence follows the standard house rule and is deliberate: a leg that
        **loses** sinks the ticket even if another leg is refunded, because a
        refund cannot rescue a loss; only when nothing has lost does a refunded
        leg void the whole combination.  Asian quarter lines are refused
        outright — their half-settlements make the combined result a matter of
        bookmaker policy rather than arithmetic, and guessing it would be worse
        than declining.
        """
        for offer in (self, other):
            if offer.components:
                raise UnsupportedMarketError(
                    f"{offer.key} est une ligne asiatique fractionnée ; "
                    "sa combinaison dépend des règles du bookmaker"
                )

        def unit(home: int, away: int) -> float:
            left, right = self.unit_result(home, away), other.unit_result(home, away)
            if left < 0.0 or right < 0.0:
                return -1.0
            if left == 0.0 or right == 0.0:  # any refunded leg voids the combination
                return 0.0
            return 1.0

        return MarketOffer(
            key=f"{self.key}+{other.key}",
            family=MarketFamily.COMBINATION,
            label=f"{self.label} ET {other.label}",
            unit_result=unit,
            rule=(
                "intersection sur la loi jointe des scores du même match ; "
                "les probabilités ne sont jamais multipliées entre elles"
            ),
        )

    def __str__(self) -> str:
        price = f" @ {self.odds:.2f}" if self.odds else ""
        return f"{self.label}{price}"


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #


def match_result(outcome: Outcome) -> MarketOffer:
    """1–N–2: a plain binary market."""
    labels = {Outcome.HOME_WIN: "Victoire domicile (1)",
              Outcome.DRAW: "Match nul (N)",
              Outcome.AWAY_WIN: "Victoire extérieur (2)"}

    def unit(home: int, away: int) -> float:
        return 1.0 if Score(home, away).outcome is outcome else -1.0

    return MarketOffer(
        key=f"1X2:{outcome.value}",
        family=MarketFamily.ONE_X_TWO,
        label=labels[outcome],
        unit_result=unit,
        rule="gagné si le résultat à la fin du temps réglementaire correspond",
    )


def double_chance(first: Outcome, second: Outcome) -> MarketOffer:
    """Double chance: a union of two of the three outcomes."""
    if first is second:
        raise ValueError("une double chance couvre deux issues distinctes")
    codes = {Outcome.HOME_WIN: "1", Outcome.DRAW: "N", Outcome.AWAY_WIN: "2"}
    pair = {first, second}

    def unit(home: int, away: int) -> float:
        return 1.0 if Score(home, away).outcome in pair else -1.0

    tag = "".join(codes[o] for o in (Outcome.HOME_WIN, Outcome.DRAW, Outcome.AWAY_WIN)
                  if o in pair)
    return MarketOffer(
        key=f"DC:{tag}",
        family=MarketFamily.DOUBLE_CHANCE,
        label=f"Double chance {tag}",
        unit_result=unit,
        rule="gagné si l'une des deux issues couvertes se produit (union)",
    )


def draw_no_bet(side: Outcome) -> MarketOffer:
    """Remboursé si nul — the textbook market the binary EV formula gets wrong."""
    if side is Outcome.DRAW:
        raise ValueError("« remboursé si nul » se joue sur une équipe, pas sur le nul")

    def unit(home: int, away: int) -> float:
        result = Score(home, away).outcome
        if result is Outcome.DRAW:
            return 0.0
        return 1.0 if result is side else -1.0

    label = "domicile" if side is Outcome.HOME_WIN else "extérieur"
    return MarketOffer(
        key=f"DNB:{side.value}",
        family=MarketFamily.DRAW_NO_BET,
        label=f"Remboursé si nul — {label}",
        unit_result=unit,
        rule="mise remboursée en cas de nul ; l'espérance intègre ce remboursement",
    )


def _is_quarter(line: float) -> bool:
    return abs(line * 4 - round(line * 4)) < 1e-9 and abs(line * 2 - round(line * 2)) > 1e-9


def asian_handicap(line: float, *, home: bool = True) -> MarketOffer:
    """Asian handicap on the home (or away) side.

    ``line`` is added to the backed team's goals.  Quarter lines are built as a
    pair of neighbouring offers and blended at pricing time.
    """
    if not math.isfinite(line):
        raise ValueError("ligne de handicap invalide")
    side = "domicile" if home else "extérieur"
    key = f"AH:{'H' if home else 'A'}:{line:+g}"
    label = f"Handicap asiatique {side} {line:+g}"

    if _is_quarter(line):
        low = asian_handicap(line - 0.25, home=home)
        high = asian_handicap(line + 0.25, home=home)

        def blended(home_goals: int, away_goals: int) -> float:
            return 0.5 * (low.unit_result(home_goals, away_goals)
                          + high.unit_result(home_goals, away_goals))

        return MarketOffer(
            key=key, family=MarketFamily.ASIAN_HANDICAP, label=label,
            unit_result=blended, line=line, components=(low, high),
            rule=(f"ligne quart : mise partagée entre {line - 0.25:+g} et "
                  f"{line + 0.25:+g} ; demi-gain et demi-perte possibles"),
        )

    def unit(home_goals: int, away_goals: int) -> float:
        margin = (home_goals - away_goals) if home else (away_goals - home_goals)
        adjusted = margin + line
        if adjusted > 1e-9:
            return 1.0
        if adjusted < -1e-9:
            return -1.0
        return 0.0

    return MarketOffer(
        key=key, family=MarketFamily.ASIAN_HANDICAP, label=label,
        unit_result=unit, line=line,
        rule=("handicap appliqué au score ; ligne entière remboursée en cas "
              "d'égalité après handicap"),
    )


def total_goals(line: float, *, over: bool = True) -> MarketOffer:
    """Over/under on total goals, with pushes and quarter lines handled."""
    if line < 0.0 or not math.isfinite(line):
        raise ValueError("ligne de total invalide")
    direction = "Plus de" if over else "Moins de"
    key = f"OU:{line:g}:{'over' if over else 'under'}"
    label = f"{direction} {line:g} buts"

    if _is_quarter(line):
        low = total_goals(line - 0.25, over=over)
        high = total_goals(line + 0.25, over=over)

        def blended(home: int, away: int) -> float:
            return 0.5 * (low.unit_result(home, away) + high.unit_result(home, away))

        return MarketOffer(
            key=key, family=MarketFamily.TOTAL, label=label, unit_result=blended,
            line=line, components=(low, high),
            rule=f"ligne quart : mise partagée entre {line - 0.25:g} et {line + 0.25:g}",
        )

    def unit(home: int, away: int) -> float:
        total = home + away
        if abs(total - line) < 1e-9:
            return 0.0
        above = total > line
        return 1.0 if above == over else -1.0

    return MarketOffer(
        key=key, family=MarketFamily.TOTAL, label=label, unit_result=unit, line=line,
        rule="ligne entière remboursée si le total égale la ligne",
    )


def team_total(line: float, *, home: bool = True, over: bool = True) -> MarketOffer:
    """Total goals for one team — summed over the joint law, not a marginal shortcut."""
    side = "domicile" if home else "extérieur"
    direction = "plus de" if over else "moins de"
    key = f"TT:{'H' if home else 'A'}:{line:g}:{'over' if over else 'under'}"

    def unit(home_goals: int, away_goals: int) -> float:
        goals = home_goals if home else away_goals
        if abs(goals - line) < 1e-9:
            return 0.0
        return 1.0 if (goals > line) == over else -1.0

    return MarketOffer(
        key=key, family=MarketFamily.TEAM_TOTAL,
        label=f"Équipe {side} : {direction} {line:g} but(s)",
        unit_result=unit, line=line,
        rule="total de l'équipe seule ; sommé sur la loi jointe",
    )


def both_teams_to_score(*, yes: bool = True) -> MarketOffer:
    """Both teams to score — read off the joint law, never from two marginals.

    Under Dixon-Coles the two scorelines are *not* independent, so multiplying
    ``P(home scores)`` by ``P(away scores)`` would be wrong by exactly the
    amount the low-score correction moves.
    """
    def unit(home: int, away: int) -> float:
        scored = home > 0 and away > 0
        return 1.0 if scored == yes else -1.0

    return MarketOffer(
        key=f"BTTS:{'yes' if yes else 'no'}",
        family=MarketFamily.BTTS,
        label="Les deux équipes marquent" + ("" if yes else " : non"),
        unit_result=unit,
        rule="gagné si les deux équipes marquent au moins un but (ou l'inverse)",
    )


def correct_score_group(scores: Iterable[Score], *, label: str | None = None) -> MarketOffer:
    """A group of exact scores — a **union**, so the cells simply add."""
    wanted = frozenset((s.home, s.away) for s in scores)
    if not wanted:
        raise ValueError("un groupe de scores exacts ne peut pas être vide")

    def unit(home: int, away: int) -> float:
        return 1.0 if (home, away) in wanted else -1.0

    rendered = ", ".join(f"{h}-{a}" for h, a in sorted(wanted))
    return MarketOffer(
        key=f"CS:{rendered.replace(' ', '')}",
        family=MarketFamily.CORRECT_SCORE,
        label=label or f"Score exact parmi {rendered}",
        unit_result=unit,
        rule="union de scores distincts : les probabilités des cases s'additionnent",
    )


def standard_catalogue(
    *,
    totals: Sequence[float] = (1.5, 2.5, 3.5),
    handicaps: Sequence[float] = (-1.5, -1.0, -0.5, -0.25, 0.0, 0.5, 1.0, 1.5),
    team_totals: Sequence[float] = (0.5, 1.5),
) -> list[MarketOffer]:
    """The markets a European book typically posts on a league match.

    Built without any price: the sport phase may inspect this catalogue safely.
    """
    offers: list[MarketOffer] = [match_result(o) for o in Outcome]
    offers.append(double_chance(Outcome.HOME_WIN, Outcome.DRAW))
    offers.append(double_chance(Outcome.HOME_WIN, Outcome.AWAY_WIN))
    offers.append(double_chance(Outcome.DRAW, Outcome.AWAY_WIN))
    offers.append(draw_no_bet(Outcome.HOME_WIN))
    offers.append(draw_no_bet(Outcome.AWAY_WIN))
    for line in handicaps:
        offers.append(asian_handicap(line, home=True))
        offers.append(asian_handicap(-line, home=False))
    for line in totals:
        offers.append(total_goals(line, over=True))
        offers.append(total_goals(line, over=False))
    for line in team_totals:
        offers.append(team_total(line, home=True, over=True))
        offers.append(team_total(line, home=False, over=True))
    offers.append(both_teams_to_score(yes=True))
    offers.append(both_teams_to_score(yes=False))
    seen: dict[str, MarketOffer] = {}
    for offer in offers:
        seen.setdefault(offer.key, offer)
    return list(seen.values())
