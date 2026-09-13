"""Lineup checks: scheduled honestly, or declared as still to be done.

The operator asked for a check around T−75/T−60 and a refresh when the sheets
are actually published.  The important clause is the last one: *if an automatic
re-check is announced, a live mechanism must perform it; otherwise the report
must say plainly that a check is still outstanding.*

This module therefore plans and records; it never claims a check happened.
:meth:`LineupPlan.state` returns ``À FAIRE`` until something calls
:meth:`LineupPlan.record`, and the report prints exactly that.  No provider in
this deployment serves lineups, so the plan stays outstanding unless the
operator imports the sheets — in which case
:func:`record_supplied_lineups` turns them into real observations, with a
verdict justified by what was actually supplied.  Nothing else moves the plan:
an automatic re-check is never claimed, because none runs here.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum

from foot.analysis.request import ResolvedMatch, resolve_timezone
from foot.collect.supplements import AbsenceRow, LineupRow
from foot.provenance import Confidence, Evidence, Source

__all__ = [
    "LineupImpact",
    "LineupObservation",
    "LineupPlan",
    "plan_lineup_checks",
    "record_supplied_lineups",
]

WATCHED_ROLES: tuple[str, ...] = (
    "gardien",
    "charnière centrale",
    "milieu défensif",
    "créateur",
    "buteur",
    "tireur de penalty",
    "banc",
)
"""Positions whose change is most likely to move a forecast materially."""


class LineupImpact(Enum):
    """The verdict after a decisive lineup change."""

    MAINTAINED = "maintenu"
    IMPROVED = "amélioré"
    DEGRADED = "dégradé"
    CANCELLED = "annulé"

    @property
    def requires_reprice(self) -> bool:
        return self is not LineupImpact.MAINTAINED


@dataclass(frozen=True, slots=True)
class LineupObservation:
    """A lineup actually seen, probable or official."""

    observed_at: dt.datetime
    status: Confidence
    source: str
    goalkeeper: str | None = None
    absences: tuple[str, ...] = ()
    notes: str = ""

    @property
    def official(self) -> bool:
        return self.status is Confidence.CONFIRMED

    def render(self) -> str:
        label = "officielle" if self.official else "probable"
        parts = [f"composition {label} — {self.source}",
                 f"vue le {self.observed_at.strftime('%Y-%m-%d %H:%M %Z')}"]
        if self.goalkeeper:
            parts.append(f"gardien : {self.goalkeeper}")
        if self.absences:
            parts.append(f"absents : {', '.join(self.absences)}")
        return " · ".join(parts)


@dataclass(slots=True)
class LineupPlan:
    """When the lineups should be checked, and whether anyone has."""

    fixture_label: str
    kickoff: dt.datetime | None
    timezone: str
    first_check: dt.datetime | None
    second_check: dt.datetime | None
    automatic: bool = False
    """True only if a live scheduler is wired up; false here, and reported as such."""

    observations: list[LineupObservation] = field(default_factory=list)
    impact: LineupImpact | None = None
    impact_reason: str = ""

    def record(
        self, observation: LineupObservation, *, impact: LineupImpact, reason: str
    ) -> None:
        """Register a real observation and its effect on the dossier."""
        if not reason.strip():
            raise ValueError("un changement de composition doit être motivé")
        self.observations.append(observation)
        self.impact = impact
        self.impact_reason = reason

    @property
    def has_official(self) -> bool:
        return any(o.official for o in self.observations)

    def state(self) -> str:
        if not self.observations:
            return "À FAIRE — aucune composition relevée à ce jour"
        latest = self.observations[-1]
        verdict = self.impact.value if self.impact else "sans effet enregistré"
        return f"{latest.render()} → dossier {verdict} ({self.impact_reason})"

    def render(self) -> str:
        zone = resolve_timezone(self.timezone)

        def moment(value: dt.datetime | None) -> str:
            return value.astimezone(zone).strftime("%d/%m %H:%M %Z") if value else "—"

        lines = [
            f"Contrôle des compositions — {self.fixture_label}",
            f"  coup d'envoi   : {moment(self.kickoff)}",
            f"  contrôle T−75  : {moment(self.first_check)}",
            f"  contrôle T−60  : {moment(self.second_check)}",
            f"  postes surveillés : {', '.join(WATCHED_ROLES)}",
            f"  état           : {self.state()}",
        ]
        lines.append(
            "  mécanisme      : "
            + (
                "planificateur actif"
                if self.automatic
                else "AUCUN automatisme actif dans ce déploiement — "
                "le contrôle reste à effectuer manuellement"
            )
        )
        return "\n".join(lines)


def plan_lineup_checks(
    resolved: ResolvedMatch, as_of: dt.datetime, *, automatic: bool = False
) -> LineupPlan:
    """Schedule the two checks relative to kickoff.

    With no kickoff time the checks cannot be placed, and the plan says so
    rather than inventing an hour.
    """
    kickoff = resolved.kickoff
    first = kickoff - dt.timedelta(minutes=75) if kickoff else None
    second = kickoff - dt.timedelta(minutes=60) if kickoff else None
    label = (
        f"{resolved.fixture.home} – {resolved.fixture.away}"
        if resolved.fixture
        else resolved.request.label()
    )
    plan = LineupPlan(
        fixture_label=label,
        kickoff=kickoff,
        timezone=resolved.timezone,
        first_check=first,
        second_check=second,
        automatic=automatic,
    )
    if kickoff is not None and as_of > kickoff - dt.timedelta(minutes=75):
        plan.impact_reason = "fenêtre T−75 déjà ouverte au moment de l'analyse"
    return plan


def record_supplied_lineups(
    plan: LineupPlan,
    *,
    sheets: Sequence[LineupRow],
    absences: Sequence[AbsenceRow],
    observed_at: dt.datetime,
) -> None:
    """Turn operator-imported team sheets into a real observation and a verdict.

    The verdict is derived only from what was supplied — never guessed:

    * a decisive absence reported for a team (a watched position) degrades the
      dossier, and the reason names the players;
    * otherwise the dossier is *maintained*, and the reason says explicitly that
      no reference lineup existed to compare against, so "maintained" means
      "nothing contradicts it", not "confirmed".

    No coefficient is applied either way: the verdict routes the dossier to a
    documented sporting scenario, which is where an absence is allowed to weigh.
    """
    if not sheets:
        return
    official = any(row.status is Confidence.CONFIRMED for row in sheets)
    keeper = next((row for row in sheets if "gardien" in row.role.lower()), None)
    source = sheets[0].source
    decisive = [row for row in absences if row.decisive]
    observation = LineupObservation(
        observed_at=observed_at,
        status=Confidence.CONFIRMED if official else Confidence.PROBABLE,
        source=source,
        goalkeeper=keeper.player if keeper else None,
        absences=tuple(row.player for row in decisive),
        notes=f"{len(sheets)} joueur(s) importés par l'opérateur",
    )
    if decisive:
        plan.record(
            observation,
            impact=LineupImpact.DEGRADED,
            reason=(
                "absence(s) à un poste suivi : "
                + ", ".join(f"{row.player} ({row.role})" for row in decisive)
                + " — le dossier est réévalué par scénario sportif documenté, "
                "sans coefficient automatique"
            ),
        )
        return
    plan.record(
        observation,
        impact=LineupImpact.MAINTAINED,
        reason=(
            "aucune absence à un poste suivi dans les données fournies ; "
            "aucune composition antérieure de référence n'existait, donc "
            "« maintenu » signifie « rien ne le contredit », pas « confirmé »"
        ),
    )


def lineup_evidence(observation: LineupObservation, fixture_label: str) -> Evidence:
    """Turn an observation into ledger evidence, keeping its confirmation status."""
    return Evidence(
        key=f"composition::{fixture_label}",
        value=observation.goalkeeper or "relevée",
        source=Source(
            name=observation.source,
            provider=observation.source,
            official=observation.official,
        ),
        retrieved_at=observation.observed_at,
        status=observation.status,
        note=observation.notes or None,
    )
