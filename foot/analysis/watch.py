"""Le contrôle T−75 / T−60, réellement exécuté — ou déclaré non fait.

Jusqu'ici le logiciel *planifiait* le contrôle des compositions et écrivait, en
toutes lettres, qu'aucun automatisme ne l'exécutait.  C'était honnête, mais cela
laissait le travail à l'opérateur au pire moment : une heure avant le coup
d'envoi, sur un téléphone.

Ce module exécute la boucle.  Il ne promet rien de plus que ce qu'il fait :

* il **réessaie** tant que les compositions ne sont pas publiées, à un rythme
  qui se resserre à l'approche du coup d'envoi, et s'arrête au coup d'envoi ;
* il **enregistre chaque tentative**, réussie ou non, avec son heure : « dernière
  vérification réussie » est une information que l'opérateur doit pouvoir lire,
  et « aucune depuis 40 minutes » en est une autre ;
* il **relance l'analyse** quand une feuille arrive, et compare la décision
  d'avant à celle d'après — un changement de composition qui ne change rien à la
  décision est une information, pas un silence ;
* il **préserve l'heure réelle des cotes** : une cote saisie une fois garde
  l'heure de son relevé initial tant qu'aucun nouveau prix n'a réellement été
  observé. Relancer l'analyse à 19:45 ne rend pas plus fraîche une cote lue à
  19:30 ;
* il n'annonce « composition officielle » que devant **deux feuilles complètes**
  — une par équipe, onze titulaires chacune. Un joueur par équipe n'est pas une
  composition, et s'arrêter dessus ferait manquer la vraie ;
* il exécute **toujours** le contrôle T−60, même quand une feuille est arrivée à
  T−75 : c'est entre les deux que les compositions changent.

Rien dans ce module ne place de pari, et rien n'engage : il rafraîchit une
analyse et dit ce qui a changé.
"""

from __future__ import annotations

import datetime as dt
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from foot.analysis.engine import Engine
from foot.analysis.journey import JourneyResult, run_journey
from foot.analysis.request import DEFAULT_TIMEZONE

__all__ = [
    "WATCH_CACHE_TTL",
    "Attempt",
    "WatchPlan",
    "WatchReport",
    "watch_until_kickoff",
]

_BOTH_TEAMS = 2
"""A fixture has two team sheets. Anything less is a partial observation."""

FIRST_CHECK = dt.timedelta(minutes=75)
"""How long before kick-off the first check is due — the protocol's T−75."""

SECOND_CHECK = dt.timedelta(minutes=60)
"""The confirmation pass, the protocol's T−60."""

WATCH_CACHE_TTL = 120.0
"""Seconds a cached response stays usable **during a watch**.

The day-to-day cache holds for six hours, which is right for a season's results
and wrong for a team sheet: a T−75 response saying « nothing published » would
still be served at T−60, so the second check would replay the first one's answer
without ever asking again.

Two minutes is short enough that each protocol checkpoint is a real request, and
long enough that a burst of retries — or two fixtures watched side by side —
does not spend a quota on the same URL twice.
"""


@dataclass(frozen=True, slots=True)
class Attempt:
    """One check, with what it found — including when it found nothing."""

    at: dt.datetime
    found_official: bool
    """Two complete official sheets — not merely one confirmed line."""

    sheets: int
    complete_sheets: int = 0
    """Teams whose sheet names a full eleven."""

    decision: str = ""
    fingerprint: str = ""
    changed: bool = False
    """Whether the decision differs from the previous successful attempt."""

    note: str = ""

    def render(self) -> str:
        moment = self.at.strftime("%H:%M %Z")
        if not self.sheets:
            return f"  {moment} — aucune composition publiée {self.note}".rstrip()
        status = "officielle et complète" if self.found_official else "partielle"
        verdict = " · DÉCISION MODIFIÉE" if self.changed else " · décision inchangée"
        return (
            f"  {moment} — composition {status} ({self.sheets} joueur(s), "
            f"{self.complete_sheets}/2 feuille(s) complète(s)) → "
            f"{self.decision}{verdict}"
        )


@dataclass(frozen=True, slots=True)
class WatchPlan:
    """When to look, for one fixture."""

    kickoff: dt.datetime
    timezone: str = DEFAULT_TIMEZONE
    first: dt.timedelta = FIRST_CHECK
    second: dt.timedelta = SECOND_CHECK
    retry_every: dt.timedelta = dt.timedelta(minutes=5)
    """Gap between retries once the first window has opened."""

    def due_times(self) -> tuple[dt.datetime, ...]:
        """Every instant at which a check is due, first to last.

        The two protocol checkpoints, then a retry every few minutes until
        kick-off: a sheet that is not published at T−75 is usually published a
        few minutes later, and giving up at T−60 loses exactly the information
        the check exists for.
        """
        moments = [self.kickoff - self.first, self.kickoff - self.second]
        cursor = moments[-1] + self.retry_every
        while cursor < self.kickoff:
            moments.append(cursor)
            cursor += self.retry_every
        return tuple(moments)


@dataclass(slots=True)
class WatchReport:
    """Everything the watch did, and what it concluded."""

    fixture: str
    plan: WatchPlan
    attempts: list[Attempt] = field(default_factory=list)
    stopped_because: str = ""

    @property
    def last_success(self) -> Attempt | None:
        """The most recent check that actually saw a team sheet."""
        for attempt in reversed(self.attempts):
            if attempt.sheets:
                return attempt
        return None

    @property
    def decision_changed(self) -> bool:
        return any(attempt.changed for attempt in self.attempts)

    def render(self) -> str:
        lines = [
            f"Contrôle des compositions — {self.fixture}",
            f"  coup d'envoi : {self.plan.kickoff.strftime('%d/%m %H:%M %Z')}",
            f"  {len(self.attempts)} tentative(s)",
        ]
        lines.extend(attempt.render() for attempt in self.attempts)
        success = self.last_success
        if success is None:
            lines.append(
                "  DERNIÈRE VÉRIFICATION RÉUSSIE : aucune. Les compositions n'ont "
                "pas été publiées, ou la source ne les sert pas."
            )
        else:
            lines.append(
                f"  DERNIÈRE VÉRIFICATION RÉUSSIE : "
                f"{success.at.strftime('%d/%m %H:%M %Z')} — {success.decision}"
            )
        if self.stopped_because:
            lines.append(f"  arrêt : {self.stopped_because}")
        return "\n".join(lines)


def watch_until_kickoff(
    engine: Engine,
    *,
    matches: str,
    plan: WatchPlan,
    bookmaker: str | None = None,
    quoted_at: dt.datetime | None = None,
    now: Callable[[], dt.datetime] | None = None,
    sleep: Callable[[float], None] | None = None,
    max_attempts: int = 40,
    on_attempt: Callable[[Attempt, JourneyResult], None] | None = None,
) -> WatchReport:
    """Re-run the analysis at each due time, until a sheet arrives or kick-off.

    Args:
        now: injected so a test can run the whole loop in microseconds; the
            real clock otherwise.
        sleep: injected for the same reason. A watch that cannot be tested
            without waiting an hour would never be tested.
        quoted_at: when the operator's prices were actually observed. Defaults
            to the instant the watch **starts**, which is when they were typed —
            not to the first check, which may be hours later. It does
            **not** advance with the checks: re-running the analysis at 19:45
            does not make a price read at 19:30 fifteen minutes fresher, and
            letting it would have hidden exactly the staleness the freshness
            rule exists to catch.
        max_attempts: a hard stop, so a misconfigured kick-off cannot spin.

    The analysis is re-run **as of the moment of the check**, never as of the
    first run: that is what lets a sheet published at T−62 count. The prices,
    however, keep the hour they were really seen.
    """
    clock = now or (lambda: dt.datetime.now(plan.kickoff.tzinfo or dt.timezone.utc))
    pause = sleep or _sleep
    report = WatchReport(fixture=_label(matches), plan=plan)
    previous: str | None = None
    # Fixed once, at launch: the instant the operator handed the prices over. A
    # watch armed at 15:45 for a 20:45 kick-off was dating them 19:30, which
    # made a four-hour-old price look freshly read.
    priced_at = quoted_at if quoted_at is not None else clock()

    for due in plan.due_times():
        if len(report.attempts) >= max_attempts:
            report.stopped_because = f"plafond de {max_attempts} tentatives atteint"
            break
        current = clock()
        if current > plan.kickoff:
            report.stopped_because = "coup d'envoi passé"
            break
        # Sleep until the due time is *actually* reached. One pause was not
        # enough: the sleeper caps each wait at an hour, so a watch armed five
        # hours early woke at 16:45 and ran the T−75 check there — three hours
        # early, on a squad sheet nobody had published yet.
        current = _wait_until(due, clock=clock, pause=pause, kickoff=plan.kickoff)
        if current > plan.kickoff:
            report.stopped_because = "coup d'envoi passé"
            break
        result = run_journey(
            engine,
            matches=matches,
            as_of=current,
            timezone=plan.timezone,
            bookmaker=bookmaker,
            quoted_at=priced_at,
            watching=True,
        )
        attempt = _observe(result, at=current, previous=previous)
        report.attempts.append(attempt)
        if on_attempt is not None:
            on_attempt(attempt, result)
        if attempt.sheets:
            previous = attempt.fingerprint
            # Both sheets complete *and* past the T−60 checkpoint. A sheet that
            # arrives at T−75 is not the last word: the protocol's second check
            # exists because that is when it changes.
            if attempt.found_official and current >= plan.kickoff - plan.second:
                report.stopped_because = (
                    "deux compositions officielles complètes, contrôle T−60 effectué"
                )
                break
    else:
        if not report.stopped_because:
            report.stopped_because = "fin de la fenêtre de contrôle"
    return report


def _wait_until(
    due: dt.datetime,
    *,
    clock: Callable[[], dt.datetime],
    pause: Callable[[float], None],
    kickoff: dt.datetime,
) -> dt.datetime:
    """Sleep in bounded steps until ``due`` is reached, kick-off, or a stall.

    Returns the instant actually reached. A clock that stops advancing ends the
    wait rather than spinning: in a test that is a fixed clock, and in
    production it would be a broken one — neither should loop forever.
    """
    current = clock()
    while current < due and current <= kickoff:
        pause((due - current).total_seconds())
        moved = clock()
        if moved <= current:
            return moved
        current = moved
    return current


def _observe(
    result: JourneyResult, *, at: dt.datetime, previous: str | None
) -> Attempt:
    """Turn one run into an attempt record, comparing it with the previous one."""
    analyses = result.run.analyses
    analysis = next((a for a in analyses if a.analysed), None)
    if analysis is None or analysis.sealed is None:
        return Attempt(
            at=at,
            found_official=False,
            sheets=0,
            decision="analyse non produite",
            fingerprint="",
            note="(rencontre non analysable à cet instant)",
        )
    plan = analysis.lineup_plan
    observations = plan.observations if plan else []
    sheets = sum(o.starters + o.bench for o in observations)
    # « Officielle » means both teams, each with a full eleven. One confirmed
    # line per side satisfied `has_official` and stopped the watch at T−75 with
    # two players on the sheet — the run then never looked again, and the real
    # lineup was never seen.
    complete = sum(1 for o in observations if o.official and o.complete)
    official = complete >= _BOTH_TEAMS
    decision = analysis.decision.status.value if analysis.decision else "—"
    # Compared on **substance**, never on the sealed fingerprint: a dossier is
    # dated, so its digest moves at every check and every re-run would be
    # announced as « DÉCISION MODIFIÉE ». What an operator needs to see changed
    # is the call, its price, its confidence and the sheets behind it.
    chosen = analysis.decision.main if analysis.decision else None
    fingerprint = "|".join(
        (
            decision,
            chosen.offer.key if chosen else "",
            f"{chosen.offer.odds:.2f}" if chosen and chosen.offer.odds else "",
            analysis.decision.confidence.value if analysis.decision else "",
            f"{complete}/{sheets}",
        )
    )
    return Attempt(
        at=at,
        found_official=official,
        sheets=sheets,
        complete_sheets=complete,
        decision=decision,
        fingerprint=fingerprint,
        changed=previous is not None and fingerprint != previous,
    )


def _label(matches: str) -> str:
    first = next((line.strip() for line in matches.splitlines() if line.strip()), "")
    return first[:60]


def _sleep(seconds: float) -> None:
    """Wait, but never longer than a check interval would sensibly need."""
    time.sleep(max(0.0, min(seconds, 3600.0)))


def due_soon(
    plan: WatchPlan, *, now: dt.datetime, within: dt.timedelta = dt.timedelta(minutes=5)
) -> bool:
    """Whether a check falls in the next ``within`` — for a scheduler to poll."""
    return any(now <= due <= now + within for due in plan.due_times())


def plans_for(
    kickoffs: Sequence[dt.datetime], *, timezone: str = DEFAULT_TIMEZONE
) -> tuple[WatchPlan, ...]:
    return tuple(WatchPlan(kickoff=k, timezone=timezone) for k in kickoffs)
