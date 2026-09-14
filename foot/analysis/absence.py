"""L'effet supposé d'une absence : où il s'applique, et de combien.

La version auditée appliquait **−15 % au rythme offensif de l'équipe** pour toute
absence à un poste suivi.  Deux choses y étaient fausses, indépendamment de
l'amplitude :

1. **Le canal.** Un gardien absent n'affaiblit pas l'attaque de son équipe ; il
   affaiblit sa défense, donc c'est l'attaque **adverse** qui doit monter. La
   version auditée faisait baisser les buts attendus de l'équipe privée de son
   gardien — l'inverse du mécanisme sportif.
2. **L'uniformité.** Un gardien, un buteur et un tireur de penalty recevaient
   exactement le même coefficient, et le remplaçant annoncé n'entrait nulle part.

Ce module corrige le canal et différencie les postes.  Il ne prétend pas pour
autant mesurer quoi que ce soit : **le barème ci-dessous est une hypothèse**,
déclarée comme telle partout où il produit un effet. Aucune validation hors
échantillon ne le soutient à ce jour, et la mesure de son apport prospectif
reste un travail à faire — elle figure explicitement au reste-à-faire de l'audit.

Ce que le module garantit, lui, est vérifiable :

* l'effet d'un poste défensif passe par l'attaque adverse, jamais par la sienne ;
* un remplaçant nommé réduit l'écart supposé — un remplaçant est un joueur de
  l'effectif, pas un vide ;
* les effets se composent multiplicativement et restent bornés, pour qu'une
  longue liste d'absents ne produise pas un scénario que rien ne soutient ;
* un poste non reconnu ne produit **aucun** effet, plutôt qu'un effet par défaut.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from foot.collect.supplements import AbsenceRow

__all__ = ["ROLE_IMPACTS", "AbsenceImpact", "combined_impact", "impact_of"]


@dataclass(frozen=True, slots=True)
class AbsenceImpact:
    """Multiplicateurs supposés, séparés par canal."""

    own_attack: float = 1.0
    """Facteur sur le rythme offensif de l'équipe privée du joueur."""

    opponent_attack: float = 1.0
    """Facteur sur le rythme offensif de l'adversaire — la défense affaiblie."""

    def __post_init__(self) -> None:
        for name, value in (
            ("own_attack", self.own_attack),
            ("opponent_attack", self.opponent_attack),
        ):
            if not 0.0 < value < 3.0:
                raise ValueError(f"{name} hors domaine plausible : {value!r}")

    @property
    def neutral(self) -> bool:
        return self.own_attack == 1.0 and self.opponent_attack == 1.0

    def softened(self, factor: float = 0.5) -> AbsenceImpact:
        """Atténue l'écart à 1, pour un poste effectivement remplacé."""
        return AbsenceImpact(
            own_attack=1.0 + (self.own_attack - 1.0) * factor,
            opponent_attack=1.0 + (self.opponent_attack - 1.0) * factor,
        )

    def then(self, other: AbsenceImpact) -> AbsenceImpact:
        return AbsenceImpact(
            own_attack=self.own_attack * other.own_attack,
            opponent_attack=self.opponent_attack * other.opponent_attack,
        )

    def clamped(self, low: float = 0.75, high: float = 1.30) -> AbsenceImpact:
        return AbsenceImpact(
            own_attack=min(max(self.own_attack, low), high),
            opponent_attack=min(max(self.opponent_attack, low), high),
        )

    def describe(self) -> str:
        parts: list[str] = []
        if self.own_attack != 1.0:
            parts.append(f"attaque de l'équipe ×{self.own_attack:.2f}")
        if self.opponent_attack != 1.0:
            parts.append(f"attaque adverse ×{self.opponent_attack:.2f}")
        return ", ".join(parts) or "aucun effet retenu"


ROLE_IMPACTS: Mapping[str, AbsenceImpact] = {
    # Postes défensifs : l'effet passe par l'attaque adverse.
    "gardien": AbsenceImpact(opponent_attack=1.10),
    "défenseur central": AbsenceImpact(opponent_attack=1.08),
    "charnière": AbsenceImpact(opponent_attack=1.08),
    "milieu défensif": AbsenceImpact(own_attack=0.98, opponent_attack=1.05),
    # Postes offensifs : l'effet passe par l'attaque de l'équipe.
    "créateur": AbsenceImpact(own_attack=0.90),
    "buteur": AbsenceImpact(own_attack=0.88),
    "tireur de penalty": AbsenceImpact(own_attack=0.96),
}
"""Barème par poste. **Hypothèse déclarée, non calibrée hors échantillon.**

Les ordres de grandeur suivent le protocole — un gardien et une charnière pèsent
sur ce que l'équipe encaisse, un buteur et un créateur sur ce qu'elle marque —
mais aucun de ces nombres n'est une estimation validée. Un poste absent de cette
table ne produit aucun effet : mieux vaut ne rien appliquer qu'appliquer au
hasard.
"""

NEUTRAL = AbsenceImpact()


def impact_of(row: AbsenceRow) -> AbsenceImpact:
    """L'effet supposé d'une absence, atténué si un remplaçant est nommé."""
    impact = ROLE_IMPACTS.get(row.role.strip().lower(), NEUTRAL)
    if impact.neutral:
        return NEUTRAL
    return impact.softened() if row.replacement.strip() else impact


def combined_impact(rows: Sequence[AbsenceRow]) -> AbsenceImpact:
    """Compose les effets des absences d'une même équipe, en restant borné."""
    total = NEUTRAL
    for row in rows:
        total = total.then(impact_of(row))
    return total.clamped()


def describe_absences(rows: Sequence[AbsenceRow]) -> str:
    """Phrase publiable : ce qui est documenté, ce qui est supposé."""
    if not rows:
        return "aucune absence retenue"
    named = ", ".join(
        f"{row.player} ({row.role or 'poste non précisé'}"
        + (f", remplacé par {row.replacement}" if row.replacement else "")
        + ")"
        for row in rows
    )
    impact = combined_impact(rows)
    return (
        f"absence(s) rapportée(s) : {named} — fait DOCUMENTÉ et sourcé. "
        f"L'ampleur ({impact.describe()}) est une HYPOTHÈSE de barème par poste, "
        f"non calibrée hors échantillon : elle oriente le canal (défense ou "
        f"attaque) plus qu'elle ne chiffre le joueur."
    )
