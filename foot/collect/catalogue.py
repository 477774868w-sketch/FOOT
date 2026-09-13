"""Ce que chaque fournisseur couvre, ce qu'il coûte, et ce que **notre compte** obtient.

Une documentation qui annonce une fonction ne prouve pas qu'un compte donné peut
récupérer la donnée : un plan gratuit expose souvent une fraction de ce que le
site décrit, et un quota épuisé ressemble à une panne.  Ce module sépare donc
trois choses que le reste du logiciel ne doit jamais confondre :

``declared``
    ce que le fournisseur **dit** offrir, avec sa source. C'est de la
    documentation, pas une preuve ;
``probe()``
    ce que **notre clé** obtient réellement, mesuré en appelant le service et en
    lisant la réponse — y compris le code d'erreur quand il en renvoie un ;
``cost``
    ce que l'accès coûte et sous quel quota, affiché **avant** tout engagement.

Rien ici ne s'abonne, ne paie, ni n'active quoi que ce soit : la commande
``foot fournisseurs --couverture`` imprime le tableau, l'opérateur décide.

Les fournisseurs sans clé continuent de fonctionner : une clé manquante retire
un fournisseur du tableau, elle n'arrête pas le reste.
"""

from __future__ import annotations

import datetime as dt
import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum

from foot.collect.base import Capability, Reachability

__all__ = [
    "PROVIDER_CATALOGUE",
    "AccessState",
    "CostModel",
    "Coverage",
    "ProviderCard",
    "credentials_present",
    "missing_credentials",
]


class AccessState(Enum):
    """How far this provider is from being usable, here and now."""

    OPERATIONAL = "opérationnel"
    """Reachable, and the probe came back with the data it promises."""

    NEEDS_KEY = "clé à fournir"
    """Adapter written and tested; the account credential is not configured."""

    UNREACHABLE = "injoignable depuis cet environnement"
    """Credential present or not needed, but the host refuses the connection."""

    NOT_BUILT = "non développé"
    """No adapter exists. Nothing would run even with full access."""

    @property
    def symbol(self) -> str:
        return {
            AccessState.OPERATIONAL: "●",
            AccessState.NEEDS_KEY: "◐",
            AccessState.UNREACHABLE: "○",
            AccessState.NOT_BUILT: "·",
        }[self]


@dataclass(frozen=True, slots=True)
class CostModel:
    """What access costs, in the operator's own terms."""

    free_tier: str
    """What the free plan actually allows — requests, competitions, delay."""

    paid_from: str = ""
    """Entry price of the first paid plan, empty when there is none."""

    quota: str = ""
    """Requests per minute / day, as the provider states them."""

    note: str = ""

    def render(self) -> str:
        parts = [f"gratuit : {self.free_tier}"]
        if self.quota:
            parts.append(f"quota : {self.quota}")
        if self.paid_from:
            parts.append(f"payant dès {self.paid_from}")
        if self.note:
            parts.append(self.note)
        return " · ".join(parts)


@dataclass(frozen=True, slots=True)
class Coverage:
    """Which competitions and seasons a provider serves, as *it* states."""

    competitions: tuple[str, ...] = ()
    seasons: str = ""
    freshness: str = ""
    """How quickly the data appears after the event, per the provider."""

    def render(self) -> str:
        parts: list[str] = []
        if self.competitions:
            shown = ", ".join(self.competitions[:6])
            more = "" if len(self.competitions) <= 6 else f" (+{len(self.competitions) - 6})"
            parts.append(f"compétitions : {shown}{more}")
        if self.seasons:
            parts.append(f"saisons : {self.seasons}")
        if self.freshness:
            parts.append(f"fraîcheur : {self.freshness}")
        return " · ".join(parts)


@dataclass(frozen=True, slots=True)
class ProviderCard:
    """One provider, as documented — never as proof that it works for us."""

    key: str
    name: str
    adapter: str
    """Module implementing it, empty when none is written."""

    declared: frozenset[Capability]
    """What the provider's own documentation announces."""

    coverage: Coverage
    cost: CostModel
    credential: str = ""
    """Environment variable holding the key, empty when none is needed."""

    homepage: str = ""
    note: str = ""

    @property
    def built(self) -> bool:
        return bool(self.adapter)

    @property
    def needs_credential(self) -> bool:
        return bool(self.credential)

    def credential_present(self) -> bool:
        return not self.credential or bool(os.environ.get(self.credential, "").strip())

    def state(self, reachability: Reachability | None = None) -> AccessState:
        """Where this provider stands, given what a probe found.

        ``reachability`` is the *measured* outcome; ``None`` means no probe was
        run — which is itself a reason not to claim the provider works.
        """
        if not self.built:
            return AccessState.NOT_BUILT
        if not self.credential_present():
            return AccessState.NEEDS_KEY
        if reachability is Reachability.OK:
            return AccessState.OPERATIONAL
        if reachability is None:
            return AccessState.NEEDS_KEY
        return AccessState.UNREACHABLE


@dataclass(frozen=True, slots=True)
class CatalogueEntry:
    """A card plus whatever the last probe measured."""

    card: ProviderCard
    reachability: Reachability | None = None
    detail: str = ""
    observed: frozenset[Capability] = frozenset()
    checked_at: dt.datetime | None = None

    @property
    def state(self) -> AccessState:
        return self.card.state(self.reachability)

    @property
    def undelivered(self) -> frozenset[Capability]:
        """Capabilities the documentation announces but the probe did not return.

        This is the gap the operator most needs to see: a provider can be
        perfectly reachable and still not serve, on this plan, the thing it is
        listed for.
        """
        if self.reachability is not Reachability.OK:
            return frozenset()
        return self.card.declared - self.observed

    def render(self) -> str:
        line = f"{self.state.symbol} {self.card.name:<24} {self.state.value}"
        if self.detail:
            line += f" — {self.detail}"
        gap = self.undelivered
        if gap:
            line += (
                "\n    annoncé mais NON servi à ce compte : "
                + ", ".join(sorted(c.value for c in gap))
            )
        return line


PROVIDER_CATALOGUE: tuple[ProviderCard, ...] = (
    ProviderCard(
        key="openfootball",
        name="openfootball",
        adapter="foot.collect.openfootball",
        declared=frozenset({Capability.RESULTS, Capability.FIXTURES}),
        coverage=Coverage(
            competitions=("en.1", "es.1", "de.1", "it.1", "fr.1"),
            seasons="2024-25 à 2026-27",
            freshness="mise à jour communautaire, quelques heures à quelques jours",
        ),
        cost=CostModel(
            free_tier="intégral, sans compte",
            quota="aucun quota déclaré (fichiers statiques sur GitHub)",
            note="données ouvertes",
        ),
        homepage="https://github.com/openfootball/football.json",
        note="Aucune clé. C'est la source qui porte le calendrier et l'historique.",
    ),
    ProviderCard(
        key="football-data-uk",
        name="football-data.co.uk",
        adapter="foot.collect.footballdata",
        declared=frozenset(
            {Capability.RESULTS, Capability.ODDS, Capability.REFEREE}
        ),
        coverage=Coverage(
            competitions=("en.1", "es.1", "de.1", "it.1", "fr.1"),
            seasons="1993-94 à aujourd'hui",
            freshness="hebdomadaire, après les rencontres",
        ),
        cost=CostModel(
            free_tier="intégral, sans compte",
            quota="aucun quota déclaré",
            note="cotes de CLÔTURE et d'ouverture, utiles à la mesure, pas à la décision",
        ),
        homepage="https://www.football-data.co.uk/",
        note=(
            "Ses cotes sont historiques : elles servent au contrôle après match, "
            "jamais comme prix disponible au moment de décider."
        ),
    ),
    ProviderCard(
        key="football-data-org",
        name="football-data.org",
        adapter="foot.collect.footballdata_org",
        declared=frozenset(
            {Capability.RESULTS, Capability.FIXTURES, Capability.LINEUPS}
        ),
        coverage=Coverage(
            competitions=("PL", "PD", "BL1", "SA", "FL1", "CL"),
            seasons="saison en cours et archives selon le plan",
            freshness="quasi temps réel sur les plans payants",
        ),
        cost=CostModel(
            free_tier="12 compétitions, 10 requêtes/minute",
            paid_from="≈ 20 €/mois (plan « One »), à vérifier sur le site",
            quota="10 req/min en gratuit",
            note="les compositions ne sont pas servies par le plan gratuit",
        ),
        credential="FOOTBALL_DATA_ORG_TOKEN",
        homepage="https://www.football-data.org/",
        note=(
            "Adaptateur écrit et testé sur réponses enregistrées. La sonde dira "
            "ce que VOTRE clé obtient : le plan gratuit ne sert pas tout."
        ),
    ),
    ProviderCard(
        key="the-odds-api",
        name="The Odds API",
        adapter="foot.collect.oddsapi",
        declared=frozenset({Capability.ODDS}),
        coverage=Coverage(
            competitions=("soccer_epl", "soccer_spain_la_liga", "soccer_italy_serie_a"),
            seasons="rencontres à venir uniquement",
            freshness="quelques minutes",
        ),
        cost=CostModel(
            free_tier="500 requêtes/mois",
            paid_from="≈ 30 $/mois pour 20 000 requêtes, à vérifier sur le site",
            quota="crédits par requête, selon le nombre de bookmakers demandés",
            note="une requête consomme d'autant plus de crédits qu'elle demande de marchés",
        ),
        credential="ODDS_API_KEY",
        homepage="https://the-odds-api.com/",
        note=(
            "Seule voie automatique vers des cotes AVANT match testée ici. "
            "Chaque prix garde le bookmaker qui l'a donné et l'heure du relevé."
        ),
    ),
    ProviderCard(
        key="api-football",
        name="API-Football (api-sports)",
        adapter="",
        declared=frozenset(
            {
                Capability.RESULTS,
                Capability.FIXTURES,
                Capability.LINEUPS,
                Capability.INJURIES,
                Capability.ADVANCED_STATS,
                Capability.ODDS,
            }
        ),
        coverage=Coverage(
            competitions=("1100+ compétitions annoncées",),
            seasons="2010 à aujourd'hui, selon le plan",
            freshness="temps réel annoncé",
        ),
        cost=CostModel(
            free_tier="100 requêtes/jour",
            paid_from="≈ 19 €/mois (plan Pro), à vérifier sur le site",
            quota="100 req/jour en gratuit, 7 500/jour en Pro",
            note="couvre absences et xG, ce qu'aucune source gratuite ne fait ici",
        ),
        credential="API_FOOTBALL_KEY",
        homepage="https://www.api-football.com/",
        note=(
            "AUCUN adaptateur écrit à ce jour. C'est le fournisseur qui "
            "débloquerait absences et xG automatiques ; c'est donc le premier à "
            "développer si vous ouvrez un compte."
        ),
    ),
)
"""Every provider considered, built or not.

Listing an unbuilt provider is deliberate: knowing that API-Football would cover
injuries and xG — and that nothing here reads it yet — is what lets the operator
decide whether to pay for it. Hiding it would make the gap look like an absence
of options.
"""


def missing_credentials(
    cards: Sequence[ProviderCard] = PROVIDER_CATALOGUE,
) -> tuple[ProviderCard, ...]:
    """Built providers whose key is not configured — each a one-line fix."""
    return tuple(
        card for card in cards if card.built and not card.credential_present()
    )


def credentials_present(
    cards: Sequence[ProviderCard] = PROVIDER_CATALOGUE,
) -> tuple[ProviderCard, ...]:
    """Cards that are both written and keyed — the ones worth probing."""
    return tuple(card for card in cards if card.built and card.credential_present())


@dataclass(frozen=True, slots=True)
class CatalogueReport:
    """The whole table, ready to print before anyone pays for anything."""

    entries: tuple[CatalogueEntry, ...] = field(default_factory=tuple)

    def by_state(self, state: AccessState) -> tuple[CatalogueEntry, ...]:
        return tuple(entry for entry in self.entries if entry.state is state)

    def served(self) -> frozenset[Capability]:
        """Capabilities a probe actually returned, from any provider."""
        served: set[Capability] = set()
        for entry in self.entries:
            if entry.state is AccessState.OPERATIONAL:
                served |= entry.observed
        return frozenset(served)

    def render(self) -> str:
        lines = ["Fournisseurs — couverture, coût, et ce que VOTRE compte obtient", "-" * 78]
        for entry in self.entries:
            lines.append(entry.render())
            lines.append(f"    {entry.card.coverage.render()}")
            lines.append(f"    {entry.card.cost.render()}")
            if entry.card.credential and not entry.card.credential_present():
                lines.append(
                    f"    clé attendue dans {entry.card.credential} "
                    f"— voir « foot config »"
                )
            if entry.card.note:
                lines.append(f"    {entry.card.note}")
        missing = frozenset(Capability) - self.served()
        lines.append("-" * 78)
        if missing:
            lines.append(
                "Capacités qu'aucun fournisseur ne sert ici : "
                + ", ".join(sorted(c.value for c in missing))
            )
            lines.append(
                "  → les rubriques correspondantes restent marquées indisponibles, "
                "jamais comblées par une valeur inventée."
            )
        lines.append(
            "Aucun engagement payant n'est pris par ce logiciel : ce tableau "
            "existe pour que la décision reste la vôtre."
        )
        return "\n".join(lines)
