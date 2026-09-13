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
verdict justified by what was actually supplied.

Since :mod:`foot.analysis.watch` exists, a check *can* now be executed — but the
plan says so only when one is actually running: ``mécanisme`` names the sources
the engine really holds, and ``suivi en cours`` appears only while
``foot suivre`` is driving the loop.  A one-shot report never claims a re-check
it is not performing.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum

from foot.analysis.request import ResolvedMatch, resolve_timezone
from foot.collect.supplements import (
    FULL_LINEUP,
    AbsenceRow,
    LineupRow,
    SupplementSet,
)
from foot.domain import Fixture
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
    """One team's sheet for one fixture, as actually seen.

    An observation belongs to **a team and a match**, never to a team in
    general: attaching an August sheet to a September fixture is how a report
    ends up announcing an official lineup nobody published.
    """

    observed_at: dt.datetime
    status: Confidence
    source: str
    team: str = ""
    goalkeeper: str | None = None
    absences: tuple[str, ...] = ()
    starters: int = 0
    bench: int = 0
    notes: str = ""

    @property
    def official(self) -> bool:
        return self.status is Confidence.CONFIRMED

    @property
    def complete(self) -> bool:
        """A sheet is complete only once a full eleven has been named."""
        return self.starters >= FULL_LINEUP

    def render(self) -> str:
        label = "officielle" if self.official else "probable"
        if not self.complete:
            label += f" mais PARTIELLE ({self.starters}/{FULL_LINEUP} titulaires)"
        parts = [f"{self.team} : composition {label} — {self.source}",
                 f"vue le {self.observed_at.strftime('%Y-%m-%d %H:%M %Z')}"]
        if self.goalkeeper:
            parts.append(f"gardien titulaire : {self.goalkeeper}")
        elif self.starters:
            parts.append("gardien titulaire non identifié")
        if self.bench:
            parts.append(f"banc : {self.bench}")
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
    """True only while a live watcher is actually performing the checks."""

    mechanism: str = ""
    """What will perform the check, named from what exists — never announced.

    Set by the engine from the lineup sources it really holds, so the sentence
    printed under « mécanisme » describes this deployment rather than a feature
    list.  Empty falls back to the plain statement that nothing is running.
    """

    observations: list[LineupObservation] = field(default_factory=list)
    """The current sheet per team — at most one each."""

    superseded: list[LineupObservation] = field(default_factory=list)
    """Earlier versions, kept so a revision can be shown rather than implied."""

    impact: LineupImpact | None = None
    impact_reason: str = ""

    def mechanism_line(self) -> str:
        """One sentence saying who checks, and whether anyone is checking now."""
        if self.automatic:
            return (
                "suivi en cours — les contrôles T−75/T−60 sont exécutés par "
                "« foot suivre », et chaque tentative est enregistrée"
            )
        if self.mechanism:
            return self.mechanism
        return (
            "AUCUN automatisme actif dans ce déploiement — "
            "le contrôle reste à effectuer manuellement"
        )

    def record(
        self, observation: LineupObservation, *, impact: LineupImpact, reason: str
    ) -> None:
        """Register a real observation and its effect on the dossier.

        A later sheet for the same team **replaces** the earlier one rather than
        piling up next to it: an official eleven published at T−60 supersedes the
        probable one, and a plan that keeps both cannot answer "who is playing".
        The replaced version is kept in :attr:`superseded` so the revision stays
        visible.
        """
        if not reason.strip():
            raise ValueError("un changement de composition doit être motivé")
        for index, existing in enumerate(self.observations):
            if existing.team == observation.team:
                self.superseded.append(existing)
                self.observations[index] = observation
                break
        else:
            self.observations.append(observation)
        self.impact = impact
        self.impact_reason = reason

    @property
    def has_official(self) -> bool:
        """True only when **every** side observed has an official sheet.

        ``any()`` here was a real defect: one official home sheet made a probable
        away sheet read as official too.
        """
        return bool(self.observations) and all(o.official for o in self.observations)

    @property
    def complete(self) -> bool:
        """Both teams named a full eleven — the only state that closes the check."""
        return len(self.observations) >= 2 and all(
            o.complete for o in self.observations
        )

    def state(self) -> str:
        if not self.observations:
            return "À FAIRE — aucune composition relevée à ce jour"
        verdict = self.impact.value if self.impact else "sans effet enregistré"
        sheets = " | ".join(o.render() for o in self.observations)
        missing = "" if self.complete else " — relevé PARTIEL, contrôle non clos"
        revised = (
            f" [{len(self.superseded)} version(s) remplacée(s)]"
            if self.superseded
            else ""
        )
        return f"{sheets}{missing}{revised} → dossier {verdict} ({self.impact_reason})"

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
        lines.append("  mécanisme      : " + self.mechanism_line())
        return "\n".join(lines)


def plan_lineup_checks(
    resolved: ResolvedMatch,
    as_of: dt.datetime,
    *,
    automatic: bool = False,
    mechanism: str = "",
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
        mechanism=mechanism,
    )
    if kickoff is not None and as_of > kickoff - dt.timedelta(minutes=75):
        plan.impact_reason = "fenêtre T−75 déjà ouverte au moment de l'analyse"
    return plan


def _observe(
    team: str,
    rows: Sequence[LineupRow],
    *,
    absences: Sequence[AbsenceRow],
    as_of: dt.datetime,
) -> LineupObservation:
    """Turn one published sheet into an observation, with its own status."""
    starters = [row for row in rows if row.starting]
    keeper = next((row for row in starters if "gardien" in row.role.lower()), None)
    return LineupObservation(
        observed_at=max(
            (row.published_at for row in rows if row.published_at is not None),
            default=as_of,
        ),
        status=(
            Confidence.CONFIRMED
            if rows and all(r.status is Confidence.CONFIRMED for r in rows)
            else Confidence.PROBABLE
        ),
        source=rows[0].source if rows else "",
        team=team,
        goalkeeper=keeper.player if keeper else None,
        absences=tuple(row.player for row in absences),
        starters=len(starters),
        bench=len(rows) - len(starters),
        notes=f"{len(rows)} joueur(s) importés par l'opérateur",
    )


def record_supplied_lineups(
    plan: LineupPlan,
    *,
    fixture: Fixture,
    supplements: SupplementSet,
    as_of: dt.datetime,
) -> None:
    """Turn operator-imported team sheets into real observations and a verdict.

    Three rules make the difference between a check and a claim:

    * a sheet is read **per team and per fixture**. A row dated another day, or
      naming another opponent, is not this match's lineup and is ignored;
    * a sheet's status is its **own**. An official home sheet says nothing about
      the away side, which keeps its "probable" until someone publishes it;
    * a sheet of one line is a **partial** sheet. Completeness is stated, so the
      T−75 check is never reported as closed when it is not.

    The verdict is derived only from what was supplied — never guessed:

    * an absence still holding at kick-off, at a watched position, degrades the
      dossier, and the reason names the players;
    * otherwise the dossier is *maintained*, and the reason says explicitly that
      no reference lineup existed to compare against, so "maintained" means
      "nothing contradicts it", not "confirmed".

    No coefficient is applied either way: the verdict routes the dossier to a
    documented sporting scenario, which is where an absence is allowed to weigh.
    """
    observations: list[LineupObservation] = []
    superseded: list[LineupObservation] = []
    decisive: list[AbsenceRow] = []
    for team, opponent in ((fixture.home, fixture.away), (fixture.away, fixture.home)):
        # Every sheet published for this fixture, oldest first. The last one is
        # the team's lineup; the ones before it are kept as replaced versions,
        # so a revision can be shown rather than merely implied.
        versions = supplements.lineup_versions(
            team, match_date=fixture.date, opponent=opponent, as_of=as_of
        )
        rows = versions[-1] if versions else ()
        superseded.extend(
            _observe(team, sheet, absences=(), as_of=as_of) for sheet in versions[:-1]
        )
        team_absences = [
            row
            for row in supplements.absences_for(
                team, on_or_before=fixture.date, as_of=as_of
            )
            if row.decisive and row.status.is_usable
        ]
        decisive.extend(team_absences)
        if not rows:
            continue
        observations.append(
            _observe(team, rows, absences=team_absences, as_of=as_of)
        )
    if not observations:
        return
    plan.superseded.extend(superseded)
    if decisive:
        impact = LineupImpact.DEGRADED
        reason = (
            "absence(s) à un poste suivi : "
            + ", ".join(f"{row.player} ({row.role})" for row in decisive)
            + " — le dossier est réévalué par scénario sportif documenté, "
            "sans coefficient automatique"
        )
    else:
        impact = LineupImpact.MAINTAINED
        reason = (
            "aucune absence à un poste suivi dans les données fournies ; "
            "aucune composition antérieure de référence n'existait, donc "
            "« maintenu » signifie « rien ne le contredit », pas « confirmé »"
        )
    for observation in observations:
        plan.record(observation, impact=impact, reason=reason)


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
