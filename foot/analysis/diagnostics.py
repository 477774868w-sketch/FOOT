"""Le contrôle des connexions, lisible depuis un téléphone.

Une clé posée dans une variable d'environnement ne prouve rien. Ce module
**appelle réellement** chaque fournisseur configuré et rapporte ce qui revient,
famille par famille : xG, absences, compositions, cotes. Puis il exerce le
parcours complet — Analyser, Suivre, Bilan — sur une rencontre réelle, et vérifie
que le bookmaker demandé pilote bien la sélection des prix.

Trois règles gouvernent ce qui s'affiche :

* **rien n'est déduit d'une capacité déclarée.** Un fournisseur qui annonce les
  xG et n'en renvoie pas est marqué comme n'en renvoyant pas ;
* **aucune clé n'apparaît**, ni en clair, ni dans un message d'erreur, ni
  tronquée au point d'être devinable. Les messages des fournisseurs sont
  nettoyés avant affichage ;
* **un échec est un résultat**, pas un silence : quota épuisé, clé refusée,
  réseau fermé et « rien publié » sont quatre lignes différentes.

L'ordre des contrôles suit celui des causes. Les clés d'abord, car elles se
vérifient sans rencontre et pour zéro crédit ; puis la rencontre elle-même ; puis
ce que chaque famille renvoie pour elle. Une rencontre introuvable au calendrier
n'empêche donc pas de savoir si les clés fonctionnent.
"""

from __future__ import annotations

import datetime as dt
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum

from foot.analysis.engine import Engine, MatchAnalysis
from foot.analysis.journey import run_journey
from foot.collect.apifootball import COMPETITION_IDS, ApiFootballProvider, season_of
from foot.collect.apifootball import CREDENTIAL as API_FOOTBALL_CREDENTIAL
from foot.collect.base import (
    CollectionError,
    ProviderBlockedError,
    QuotaReport,
    Reachability,
)
from foot.collect.cache import Cache
from foot.collect.oddsapi import CREDENTIAL as ODDS_CREDENTIAL
from foot.collect.oddsapi import OddsApiProvider, QuotedPrice, best_prices
from foot.domain import Fixture
from foot.provenance import utcnow

__all__ = ["Check", "CheckReport", "Outcome", "run_checks", "scrub"]


class Outcome(Enum):
    """What one check found."""

    OK = "obtenu"
    EMPTY = "répondu, rien à servir"
    BLOCKED = "refusé (clé ou quota)"
    UNREACHABLE = "injoignable"
    NOT_CONFIGURED = "clé absente"

    @property
    def symbol(self) -> str:
        return {
            Outcome.OK: "●",
            Outcome.EMPTY: "◐",
            Outcome.BLOCKED: "✗",
            Outcome.UNREACHABLE: "○",
            Outcome.NOT_CONFIGURED: "·",
        }[self]


_FROM_REACHABILITY = {
    Reachability.OK: Outcome.OK,
    Reachability.BLOCKED: Outcome.BLOCKED,
    Reachability.AUTH_REQUIRED: Outcome.NOT_CONFIGURED,
    Reachability.ERROR: Outcome.UNREACHABLE,
    Reachability.NOT_PROBED: Outcome.NOT_CONFIGURED,
}


@dataclass(frozen=True, slots=True)
class Check:
    """One verified thing, with what came back."""

    name: str
    outcome: Outcome
    detail: str = ""

    def render(self) -> str:
        line = f"{self.outcome.symbol} {self.name:<30} {self.outcome.value}"
        return f"{line} — {self.detail}" if self.detail else line


@dataclass(frozen=True, slots=True)
class CheckReport:
    """Everything the control screen shows."""

    checks: tuple[Check, ...] = ()
    coverage: str = ""
    quotas: tuple[str, ...] = ()
    checked_at: dt.datetime = field(default_factory=utcnow)

    @property
    def ready(self) -> bool:
        """Whether every check that matters came back usable.

        ``EMPTY`` counts as not ready on purpose: a service that answers without
        the data is not a service the evening can rely on, and a screen that
        called that « prêt » would be telling the operator what he wants to hear.
        """
        return bool(self.checks) and all(c.outcome is Outcome.OK for c in self.checks)

    def render(self) -> str:
        lines = [
            f"mesuré le {self.checked_at:%d/%m à %H:%M %Z} en appelant les services,",
            "jamais déduit d'une capacité annoncée.",
            "",
        ]
        lines.extend(f"  {check.render()}" for check in self.checks)
        if self.quotas:
            lines.append("")
            lines.append("  QUOTAS RAPPORTÉS PAR LES SERVICES")
            lines.extend(f"    {line}" for line in self.quotas)
        if self.coverage:
            lines.append("")
            lines.extend(f"  {line}" for line in self.coverage.splitlines())
        return "\n".join(lines)


_SECRET_NAMES = (API_FOOTBALL_CREDENTIAL, ODDS_CREDENTIAL, "FOOTBALL_DATA_ORG_TOKEN")
_MIN_SECRET = 8
_KEY_IN_URL = re.compile(r"(?i)(api[_-]?key=|token=|[?&]key=)[^&\s'\"]*")


def scrub(text: str) -> str:
    """Remove any configured key from a message before it is shown or logged.

    Providers echo the key back in error messages, and a query string carries it
    in plain sight. A control screen exists to be read and pasted; it must not be
    a way to leak the thing it is checking.

    Short values are left alone deliberately: masking every occurrence of a
    three-character key would redact half the report and hide the failure the
    operator is trying to read.
    """
    cleaned = text
    for name in _SECRET_NAMES:
        value = os.environ.get(name, "").strip()
        if len(value) >= _MIN_SECRET:
            cleaned = cleaned.replace(value, f"[{name} masquée]")
    return _KEY_IN_URL.sub(r"\1[masquée]", cleaned)


def _key_check(name: str, quota: QuotaReport) -> Check:
    """Turn a quota probe into the « does this key work » line."""
    outcome = _FROM_REACHABILITY[quota.reachability]
    detail = scrub(quota.render().split(" · ", maxsplit=1)[-1])
    if outcome is Outcome.OK and not quota.measured and not quota.plan:
        # Reached, authenticated, but the service published no figure: say so
        # rather than printing a reassuring blank.
        return Check(name, Outcome.OK, detail or "clé acceptée")
    return Check(name, outcome, detail)


def _sport_source(engine: Engine, cache: Cache | None) -> ApiFootballProvider:
    """The API-Football adapter this engine actually uses, if it has one.

    Falling back to a freshly built one is not a detail: without a key the
    registry never receives the adapter at all, and the check must still be able
    to say « clé absente » rather than silently skipping the line.
    """
    for provider in engine.registry:
        if isinstance(provider, ApiFootballProvider):
            return provider
    return ApiFootballProvider(cache)


def _odds_source(
    engine: Engine, cache: Cache | None, bookmaker: str
) -> OddsApiProvider:
    """The Odds API adapter this engine uses, or one that will say it has no key."""
    for provider in engine.registry:
        if isinstance(provider, OddsApiProvider):
            return provider
    return OddsApiProvider(cache, bookmaker=bookmaker)


def _probe_keys(engine: Engine, cache: Cache | None) -> tuple[list[Check], list[str]]:
    """Ask each service about the key itself — no fixture, and no credit spent."""
    checks: list[Check] = []
    quotas: list[str] = []
    for name, quota in (
        ("Clé API-Football", _sport_source(engine, cache).quota()),
        ("Clé The Odds API", _odds_source(engine, cache, "").quota()),
    ):
        checks.append(_key_check(name, quota))
        if quota.reachability is not Reachability.AUTH_REQUIRED:
            quotas.append(scrub(quota.render()))
    return (checks, quotas)


def _season_label(date: dt.date) -> str:
    """``2026-27`` for a fixture played in that season, July to June."""
    start = season_of(date)
    return f"{start}-{(start + 1) % 100:02d}"


def _probe_api_football(
    engine: Engine, fixture: Fixture, cache: Cache | None = None
) -> tuple[list[Check], str]:
    """Call API-Football for the three families it is meant to serve."""
    checks: list[Check] = []
    provider = _sport_source(engine, cache)
    if not provider.configured:
        return (
            [
                Check(name, Outcome.NOT_CONFIGURED, f"{API_FOOTBALL_CREDENTIAL} absente")
                for name in ("xG (API-Football)", "Absences", "Compositions")
            ],
            "",
        )
    families = (
        ("xG (API-Football)", lambda: provider.xg_rows([fixture])[0]),
        ("Absences", lambda: provider.absences(fixture)[0]),
        ("Compositions", lambda: provider.team_sheets(fixture)[0]),
    )
    for name, call in families:
        try:
            rows = call()
        except ProviderBlockedError as error:
            checks.append(Check(name, Outcome.BLOCKED, scrub(str(error))))
            continue
        except CollectionError as error:
            checks.append(Check(name, Outcome.UNREACHABLE, scrub(str(error))))
            continue
        checks.append(
            Check(name, Outcome.OK, f"{len(rows)} ligne(s)")
            if rows
            else Check(
                name,
                Outcome.EMPTY,
                "le service a répondu sans cette donnée pour cette rencontre",
            )
        )
    competition = fixture.competition or ""
    if competition not in COMPETITION_IDS:
        # The coverage table is measured per competition-season. Running it on a
        # competition this adapter does not map would measure nothing and print
        # a table that looks like a measurement.
        return (checks, "")
    # The season is the one this fixture belongs to, not the calendar year: a
    # February match belongs to the season that started the previous July, and
    # probing coverage under the wrong season reports "nothing served" for an
    # account that serves it perfectly well.
    matrix = provider.coverage([competition], [_season_label(fixture.date)])
    return (checks, matrix.render())


def _probe_odds(
    engine: Engine, fixture: Fixture, bookmaker: str, cache: Cache | None = None
) -> list[Check]:
    """Call The Odds API, and verify the requested book decides the price."""
    provider = _odds_source(engine, cache, bookmaker)
    if not provider.configured:
        return [
            Check("Cotes (The Odds API)", Outcome.NOT_CONFIGURED,
                  f"{ODDS_CREDENTIAL} absente")
        ]
    competition = fixture.competition or ""
    if not competition:
        return [
            Check(
                "Cotes (The Odds API)",
                Outcome.EMPTY,
                "compétition inconnue pour cette ligne : rien n'a été demandé",
            )
        ]
    try:
        quotes, _retrieved = provider.odds_for(competition)
    except ProviderBlockedError as error:
        return [Check("Cotes (The Odds API)", Outcome.BLOCKED, scrub(str(error)))]
    except CollectionError as error:
        return [Check("Cotes (The Odds API)", Outcome.UNREACHABLE, scrub(str(error)))]
    checks = [
        Check("Cotes (The Odds API)", Outcome.OK, f"{len(quotes)} prix")
        if quotes
        else Check(
            "Cotes (The Odds API)",
            Outcome.EMPTY,
            "aucune rencontre cotée dans cette compétition pour l'instant",
        )
    ]
    checks.append(_bookmaker_check(quotes, bookmaker))
    return checks


def _bookmaker_check(quotes: Sequence[QuotedPrice], bookmaker: str) -> Check:
    """Prove the requested book actually decides which price is offered.

    Not « the option was accepted » : the prices that come back are re-selected
    here with the operator's book, and the line says how many markets that book
    actually won. A book that does not quote this competition is named as such,
    with the books the prices were taken from instead — never silently replaced.
    """
    if not bookmaker:
        return Check(
            "Bookmaker du formulaire",
            Outcome.EMPTY,
            "aucun bookmaker saisi : le meilleur prix disponible est retenu",
        )
    if not quotes:
        return Check(
            "Bookmaker du formulaire", Outcome.EMPTY, "aucun prix à départager"
        )
    books = {quote.bookmaker for quote in quotes}
    chosen = best_prices(quotes, prefer=bookmaker)
    taken = sorted({quote.bookmaker for quote in chosen.values()})
    if bookmaker in books:
        matching = sum(1 for q in chosen.values() if q.bookmaker == bookmaker)
        return Check(
            "Bookmaker du formulaire",
            Outcome.OK if matching else Outcome.EMPTY,
            f"{matching}/{len(chosen)} marché(s) pris chez {bookmaker}",
        )
    return Check(
        "Bookmaker du formulaire",
        Outcome.EMPTY,
        f"{bookmaker} ne cote pas ici ; prix retenus chez "
        f"{', '.join(taken[:4])} et étiquetés comme tels",
    )


def _journey_checks(analysis: MatchAnalysis) -> list[Check]:
    """What the Analyser → Suivre → Bilan journey produced for this fixture."""
    checks = [
        Check(
            "Contexte retenu",
            Outcome.OK if analysis.collected else Outcome.EMPTY,
            ", ".join(analysis.collected) if analysis.collected
            else "aucune collecte automatique active",
        )
    ]
    plan = analysis.lineup_plan
    checks.append(
        Check(
            "Parcours Suivre",
            Outcome.OK if plan is not None and plan.mechanism else Outcome.EMPTY,
            plan.mechanism_line()[:96] if plan is not None
            else "aucun contrôle des compositions prévu pour cette rencontre",
        )
    )
    checks.append(
        Check(
            "Parcours Bilan",
            Outcome.OK if analysis.decision is not None else Outcome.EMPTY,
            "prévision journalisable ; la mesure attend le résultat du match"
            if analysis.decision is not None
            else "aucune décision à enregistrer : rien à mesurer plus tard",
        )
    )
    return checks


def _typed_fixture(analysis: MatchAnalysis) -> Fixture | None:
    """The fixture as the operator typed it, when the calendar did not know it.

    Used only as a **lookup key** for the providers: the names and the date come
    from the line, nothing is invented, and a provider that does not find them
    says so. Without this, a match missing from the loaded calendar meant the
    connection check reported nothing at all about the providers.
    """
    if analysis.resolved.fixture is not None:
        return analysis.resolved.fixture
    request = analysis.resolved.request
    if not request.parsed or request.date is None:
        return None
    try:
        return Fixture(
            home=request.home_text,
            away=request.away_text,
            date=request.date,
            competition=analysis.resolved.competition_key or None,
        )
    except (ValueError, TypeError):
        return None


def run_checks(
    engine: Engine,
    *,
    fixture_line: str,
    bookmaker: str = "",
    timezone: str = "Europe/Paris",
    cache: Cache | None = None,
) -> CheckReport:
    """Verify the connections, then the journey, on a real fixture.

    ``fixture_line`` is a match written the way the form takes them. The journey
    is exercised for real — live, like the ordinary button — so a failure here is
    a failure the operator would have hit tonight, not a failure of a mock.

    ``cache`` is the server's own store. Passing it means this screen re-reads
    what the analysis already paid for instead of spending credits again.
    """
    now = utcnow()
    checks, quotas = _probe_keys(engine, cache)
    coverage = ""

    try:
        result = run_journey(
            engine, matches=fixture_line, as_of=now, timezone=timezone,
            bookmaker=bookmaker or None, live=True,
        )
    except (ValueError, KeyError) as error:
        # An unreadable fixture line is the operator's to fix, and saying so is
        # more useful than a stack trace he cannot act on. The key checks above
        # are kept: they are what he most likely came to read.
        checks.append(Check("Parcours Analyser", Outcome.EMPTY, scrub(str(error))))
        return CheckReport(checks=tuple(checks), quotas=tuple(quotas), checked_at=now)

    analyses = result.run.analyses
    analysed = [a for a in analyses if a.analysed]
    reason = analyses[0].headline() if analyses and not analysed else ""
    checks.append(
        Check(
            "Parcours Analyser",
            Outcome.OK if analysed else Outcome.EMPTY,
            f"{len(analysed)}/{len(analyses)} rencontre(s) analysée(s)"
            + (f" — {scrub(reason)[:96]}" if reason else ""),
        )
    )

    first = analysed[0] if analysed else (analyses[0] if analyses else None)
    fixture = _typed_fixture(first) if first is not None else None
    if fixture is not None:
        found, coverage = _probe_api_football(engine, fixture, cache)
        checks.extend(found)
        checks.extend(_probe_odds(engine, fixture, bookmaker, cache))
    if first is not None and first.analysed:
        checks.extend(_journey_checks(first))
    return CheckReport(
        checks=tuple(checks),
        coverage=coverage,
        quotas=tuple(quotas),
        checked_at=now,
    )
