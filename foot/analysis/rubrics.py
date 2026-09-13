"""The 22-rubric investigation grid.

.. warning::

   The operator's canonical protocol document was **not supplied** with the
   request and is not present in the repository.  The grid below is a faithful
   reconstruction from the operator's own written requirements, and it is kept
   as *data* precisely so the canonical list can replace it without touching a
   line of logic: edit :data:`RUBRICS`, or load a replacement with
   :func:`load_rubrics`, and every report follows.

Each rubric declares the collection capabilities it needs.  The engine checks
those against the *probed* provider registry, so a rubric whose data cannot be
reached is reported as unavailable with the reason — never quietly skipped and
never filled with a plausible-looking invention.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from foot.collect.base import Capability

__all__ = [
    "PROTOCOL_PATH",
    "RUBRICS",
    "Rubric",
    "RubricAssessment",
    "RubricImplementation",
    "RubricPhase",
    "RubricStatus",
    "load_rubrics",
]

PROTOCOL_PATH = Path(__file__).resolve().parents[2] / "protocole" / "protocole-22-rubriques.json"
"""Where the protocol ships in the repository.

The grid is *data*, and the engine reads it from here by default, so replacing
the file replaces the protocol without touching a line of code.
"""


class RubricPhase(Enum):
    """Which half of the analysis a rubric belongs to."""

    IDENTIFICATION = "identification"
    SPORT = "dossier sportif"
    MODEL = "modèle"
    MARKET = "marché"
    REPORT = "restitution"


class RubricImplementation(Enum):
    """How far a rubric has actually been built — three states, never blurred.

    The distinction matters because "unavailable" hides two very different
    situations: an adapter that exists and is merely blocked can be used
    elsewhere today, whereas one that was never written cannot be used anywhere.
    """

    OPERATIONAL = "opérationnel"
    """Code exists, runs here, and its output reaches the analysis."""

    BUILT_UNREACHABLE = "développé mais inaccessible"
    """An adapter is written and tested; the network or a key blocks it here."""

    NOT_BUILT = "non développé"
    """No adapter exists for this data. Nothing would run even with full access."""

    OPERATOR_SUPPLIED = "opérationnel sur données fournies"
    """No automatic source, but the operator can supply the data by import."""

    @property
    def symbol(self) -> str:
        return {
            RubricImplementation.OPERATIONAL: "●",
            RubricImplementation.OPERATOR_SUPPLIED: "◐",
            RubricImplementation.BUILT_UNREACHABLE: "○",
            RubricImplementation.NOT_BUILT: "·",
        }[self]


class RubricStatus(Enum):
    """How completely a rubric could be answered for one match."""

    COVERED = "traitée"
    PARTIAL = "partielle"
    UNAVAILABLE = "indisponible"
    NOT_APPLICABLE = "sans objet"

    @property
    def symbol(self) -> str:
        return {
            RubricStatus.COVERED: "✓",
            RubricStatus.PARTIAL: "~",
            RubricStatus.UNAVAILABLE: "✗",
            RubricStatus.NOT_APPLICABLE: "·",
        }[self]


@dataclass(frozen=True, slots=True)
class Rubric:
    """One line of the investigation grid."""

    number: int
    title: str
    phase: RubricPhase
    requires: frozenset[Capability] = frozenset()
    detail: str = ""
    feeds_model: bool = False
    """True when this rubric can change a model parameter, not merely the text."""

    adapter: str = ""
    """Module that would serve this rubric; empty when none has been written."""

    operator_import: str = ""
    """CLI option through which the operator can supply the data by hand."""

    treatment: str = ""
    """How the data is processed once obtained."""

    effect: str = ""
    """What the processed data actually changes in the forecast or the decision."""

    def implementation(
        self, *, available: frozenset[Capability], adapters_built: frozenset[str]
    ) -> RubricImplementation:
        """Classify this rubric against what is built and what is reachable."""
        if not self.requires:
            return RubricImplementation.OPERATIONAL
        if self.requires <= available:
            return RubricImplementation.OPERATIONAL
        if self.adapter and self.adapter in adapters_built:
            return RubricImplementation.BUILT_UNREACHABLE
        if self.operator_import:
            return RubricImplementation.OPERATOR_SUPPLIED
        return RubricImplementation.NOT_BUILT

    def __str__(self) -> str:
        return f"R{self.number:02d} · {self.title}"


@dataclass(frozen=True, slots=True)
class RubricAssessment:
    """What the engine actually managed to do for one rubric, on one match."""

    rubric: Rubric
    status: RubricStatus
    summary: str = ""
    evidence_keys: tuple[str, ...] = ()
    model_variables: tuple[str, ...] = ()
    """Model inputs this rubric actually moved — empty when it only informs prose."""

    blocker: str = ""
    implementation: RubricImplementation = RubricImplementation.NOT_BUILT
    data_used: str = ""
    """The concrete datum consumed, named so it can be checked."""

    treatment: str = ""
    """What was done with it."""

    effect: str = ""
    """What changed as a result — in the forecast, or explicitly nothing."""

    def render(self) -> str:
        line = (
            f"{self.status.symbol} {self.implementation.symbol} "
            f"R{self.rubric.number:02d} {self.rubric.title}"
        )
        if self.summary:
            line += f" — {self.summary}"
        if self.model_variables:
            line += f"  [→ modèle : {', '.join(self.model_variables)}]"
        if self.blocker:
            line += f"  ({self.implementation.value} : {self.blocker})"
        return line

    def detail_lines(self) -> list[str]:
        """The three-line answer the operator asked for, per rubric."""
        lines = [
            f"R{self.rubric.number:02d} · {self.rubric.title}",
            f"    état      : {self.status.value} / {self.implementation.value}",
        ]
        if self.data_used:
            lines.append(f"    donnée    : {self.data_used}")
        if self.treatment:
            lines.append(f"    traitement: {self.treatment}")
        if self.effect:
            lines.append(f"    effet     : {self.effect}")
        if self.blocker:
            lines.append(f"    blocage   : {self.blocker}")
        if self.evidence_keys:
            lines.append(f"    preuves   : {', '.join(self.evidence_keys)}")
        return lines


def _r(
    number: int,
    title: str,
    phase: RubricPhase,
    requires: Iterable[Capability] = (),
    *,
    detail: str = "",
    feeds_model: bool = False,
    adapter: str = "",
    operator_import: str = "",
    treatment: str = "",
    effect: str = "",
) -> Rubric:
    return Rubric(
        number, title, phase, frozenset(requires), detail, feeds_model,
        adapter, operator_import, treatment, effect,
    )


RUBRICS: tuple[Rubric, ...] = (
    _r(1, "Identification de la rencontre et statut", RubricPhase.IDENTIFICATION,
       [Capability.FIXTURES],
       detail="Compétition, équipes, catégorie, domicile réel, terrain neutre, "
              "horaire, règles de règlement, statut (à venir / commencé / reporté).",
       treatment="résolution des noms saisis contre le calendrier chargé ; la date "
                 "doit correspondre exactement, aucune rencontre n'est glissée",
       effect="une rencontre non vérifiée au calendrier bloque toute recommandation "
              "prématch"),
    _r(2, "Traçabilité des sources et statut de confirmation", RubricPhase.IDENTIFICATION,
       detail="Valeur, unité, source, fournisseur d'origine, date du fait, "
              "date de publication, date de récupération, statut.",
       treatment="chaque fait entre au registre avec son URL, sa date de fait et sa "
                 "date de relevé ; l'indépendance se compte sur le fournisseur amont",
       effect="les contradictions restent visibles et abaissent la confiance"),
    _r(3, "Séparation sport / cotes et scellement du dossier", RubricPhase.IDENTIFICATION,
       detail="as_of fixé, dossier sportif scellé et empreinté avant toute "
              "lecture du marché.",
       treatment="liste blanche à l'entrée de la phase sportive, empreinte SHA-256 "
                 "au scellement, journal d'exposition aux cotes",
       effect="une exposition antérieure au scellement est signalée dans la fiche"),
    _r(4, "Forme récente (5 à 10 matchs) dans un historique long", RubricPhase.SPORT,
       [Capability.RESULTS], feeds_model=True, adapter="foot.collect.openfootball",
       treatment="tous les résultats disponibles entrent dans la vraisemblance, "
                 "pondérés par une demi-vie",
       effect="fixe attaque[équipe] et défense[équipe], donc les buts attendus"),
    _r(5, "Qualité des adversaires rencontrés, au niveau de l'époque", RubricPhase.SPORT,
       [Capability.RESULTS], feeds_model=True, adapter="foot.collect.openfootball",
       detail="Force des adversaires estimée à la date du match, pas aujourd'hui.",
       treatment="estimation conjointe de toutes les équipes : la force adverse est "
                 "un paramètre, pas une correction ajoutée après coup",
       effect="un résultat contre une équipe forte pèse davantage, structurellement"),
    _r(6, "Différentiel domicile / extérieur et taille d'échantillon", RubricPhase.SPORT,
       [Capability.RESULTS], feeds_model=True, adapter="foot.collect.openfootball",
       treatment="paramètre d'avantage du terrain estimé sur toute la compétition",
       effect="décale le taux de buts domicile ; mis à zéro sur terrain neutre"),
    _r(7, "Buts, xG, npxG, xGA, tirs, grosses occasions, qualité des tirs", RubricPhase.SPORT,
       [Capability.ADVANCED_STATS], feeds_model=True,
       operator_import="--xg-csv",
       treatment="comparaison buts marqués / xG sur les dix derniers matchs",
       effect="écart buts−xG signalé et converti en scénario de sur-performance ; "
              "n'entre pas dans l'estimation, faute de calibration validée"),
    _r(8, "Penalties, exclusions et périodes déformantes", RubricPhase.SPORT,
       [Capability.ADVANCED_STATS],
       treatment="nécessite les événements horodatés du match",
       effect="aucun : la donnée n'est pas disponible et n'est pas reconstituée"),
    _r(9, "Performance à onze contre onze et selon l'état du score", RubricPhase.SPORT,
       [Capability.ADVANCED_STATS],
       detail="Exige des événements détaillés ; ne se déduit pas des totaux.",
       treatment="segmentation par état du score, impossible sans flux d'événements",
       effect="aucun : explicitement déclaré indisponible plutôt qu'approché"),
    _r(10, "Gardien titulaire, remplaçant et indicateurs", RubricPhase.SPORT,
        [Capability.LINEUPS], operator_import="--compositions-csv",
        treatment="relevé du gardien annoncé et de son statut (probable / officiel)",
        effect="un changement de gardien déclenche une réévaluation du dossier"),
    _r(11, "Absences, retours, minutes attendues et interactions", RubricPhase.SPORT,
        [Capability.INJURIES], operator_import="--absences-csv",
        treatment="liste d'absences avec poste et statut de confirmation",
        effect="produit un scénario sportif documenté, cité en source, qui peut "
               "faire rejeter un pari ; n'applique aucun coefficient arbitraire"),
    _r(12, "Entraîneur, changement de système, confrontation de styles", RubricPhase.SPORT,
        [Capability.LINEUPS],
        treatment="nécessite la composition et le dispositif annoncés",
        effect="aucun : non développé"),
    _r(13, "Repos, déplacements, rotation, prolongations récentes", RubricPhase.SPORT,
        [Capability.RESULTS], feeds_model=True, adapter="foot.collect.openfootball",
        detail="Jours de repos calculables depuis le calendrier des résultats.",
        treatment="jours écoulés depuis le dernier match, par équipe",
        effect="affiché seulement ; aucun coefficient de fatigue n'est appliqué "
               "faute d'estimation validée"),
    _r(14, "Coups de pied arrêtés, transitions, banc, fins de match", RubricPhase.SPORT,
        [Capability.ADVANCED_STATS],
        treatment="nécessite les événements détaillés",
        effect="aucun : non développé"),
    _r(15, "Météo, pelouse et arbitre", RubricPhase.SPORT,
        [Capability.WEATHER, Capability.REFEREE], adapter="foot.collect.footballdata",
        treatment="l'arbitre figure dans l'archive football-data.co.uk ; la météo "
                  "exigerait un fournisseur distinct",
        effect="aucun ici : l'archive est inaccessible depuis cet environnement"),
    _r(16, "Liaison donnée → variable → effet sur la prévision", RubricPhase.MODEL,
        detail="Toute donnée affichée n'est pas une donnée utilisée ; la "
               "distinction est explicite.",
        treatment="chaque constat porte son type, sa variable et son effet, ou la "
                  "mention « affiché seulement »",
        effect="la fiche sépare les informations utilisées des informations montrées"),
    _r(17, "Modèle de référence, enrichissements et régularisation", RubricPhase.MODEL,
        [Capability.RESULTS], feeds_model=True, adapter="foot.collect.openfootball",
        detail="Poisson, Dixon-Coles et Elo comme références ; petits "
               "échantillons traités par régularisation.",
        treatment="Dixon-Coles pondéré, régularisation inversement proportionnelle "
                  "au nombre de matchs effectifs, Elo en contrôle croisé",
        effect="fournit la loi jointe des scores dont dérivent tous les marchés"),
    _r(18, "Scénarios, contre-analyse et conditions d'invalidation", RubricPhase.MODEL,
        treatment="scénarios typés : sensibilité, incertitude d'estimation, "
                  "événement sportif documenté",
        effect="seules la sensibilité et les événements documentés peuvent rejeter "
               "un pari ; l'incertitude d'estimation est rapportée, pas filtrante"),
    _r(19, "Comparaison des marchés disponibles", RubricPhase.MARKET,
        [Capability.ODDS], adapter="foot.collect.footballdata",
        operator_import="cotes saisies (1=, N=, 2=, BTTS:, TOTAL:, DC:, DNB:, AH:, TE:)",
        treatment="chaque cote fournie est réglée sur la même loi jointe, "
                  "remboursements et demi-règlements compris",
        effect="classe les marchés par croissance logarithmique ; la fiche nomme "
               "ceux qui ont réellement été comparés"),
    _r(20, "Calculs de marché : loi jointe et règlements asiatiques", RubricPhase.MARKET,
        treatment="unions, intersections et lignes quart calculées case par case "
                  "sur la grille des scores",
        effect="seuils de prix résolus sur le profil de règlement exact"),
    _r(21, "Compositions probables puis officielles, et réévaluation", RubricPhase.MARKET,
        [Capability.LINEUPS], operator_import="--compositions-csv",
        treatment="contrôles planifiés à T−75 et T−60, statut probable puis officiel",
        effect="un changement décisif produit un nouveau dossier scellé qui "
               "remplace l'ancien, avec sa raison"),
    _r(22, "Décision, confiance, risque et restitution", RubricPhase.REPORT,
        treatment="confiance A/B/C/D fondée sur la couverture, la convergence et "
                  "les contradictions — jamais sur la probabilité de gain",
        effect="une confiance D ou un modèle non convergé interdisent la "
               "recommandation"),
)
assert len(RUBRICS) == 22, "la grille doit compter exactement 22 rubriques"


def load_rubrics(path: str | Path) -> tuple[Rubric, ...]:
    """Replace the grid from a JSON file — the hook for the canonical protocol.

    Expected shape: a list of objects with ``number``, ``title``, ``phase`` and
    optional ``requires`` (capability names), ``detail`` and ``feeds_model``.
    """
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("le fichier de rubriques doit contenir une liste")
    by_name = {c.name: c for c in Capability}
    rubrics: list[Rubric] = []
    for raw in payload:
        requires = frozenset(by_name[name] for name in raw.get("requires", []))
        rubrics.append(
            Rubric(
                number=int(raw["number"]),
                title=str(raw["title"]),
                phase=RubricPhase(raw.get("phase", "dossier sportif")),
                requires=requires,
                detail=str(raw.get("detail", "")),
                feeds_model=bool(raw.get("feeds_model", False)),
                adapter=str(raw.get("adapter", "")),
                operator_import=str(raw.get("operator_import", "")),
                treatment=str(raw.get("treatment", "")),
                effect=str(raw.get("effect", "")),
            )
        )
    return tuple(sorted(rubrics, key=lambda r: r.number))


def coverage_table(assessments: Sequence[RubricAssessment]) -> str:
    """A compact grid showing what was answered and what was not."""
    lines = ["Grille des 22 rubriques", "-" * 78]
    lines.extend(a.render() for a in sorted(assessments, key=lambda a: a.rubric.number))
    counts: dict[RubricStatus, int] = {}
    for assessment in assessments:
        counts[assessment.status] = counts.get(assessment.status, 0) + 1
    lines.append("-" * 78)
    lines.append(
        "  ".join(f"{status.symbol} {status.value} : {n}" for status, n in sorted(
            counts.items(), key=lambda kv: kv[0].value
        ))
    )
    return "\n".join(lines)


def unmet_requirements(
    rubrics: Sequence[Rubric], available: Iterable[Capability]
) -> Mapping[int, frozenset[Capability]]:
    """Per rubric, the capabilities it needs that no provider can supply."""
    have = set(available)
    missing: dict[int, frozenset[Capability]] = {}
    for rubric in rubrics:
        gap = rubric.requires - have
        if gap:
            missing[rubric.number] = frozenset(gap)
    return missing
