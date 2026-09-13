"""The analysis engine: one pass from typed lines to a decision per match.

The order of operations is the requirement, not an implementation detail:

1. probe the providers, and record what cannot be reached;
2. identify every requested fixture, reporting precisely what is missing when
   one cannot be pinned down;
3. build the **sport dossier** from results only, and seal it;
4. *then* read prices and compare the markets;
5. decide, with the ranking criteria that were fixed before step 4.

Nothing in steps 1–3 can see a price: the sport phase is fed through
:class:`~foot.analysis.dossier.SportInput`, which admits four things and filters
market evidence out on the way in.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from foot.analysis.absence import combined_impact, describe_absences
from foot.analysis.dossier import (
    ExposureAudit,
    Finding,
    FindingKind,
    SealedDossier,
    SportDossier,
    SportInput,
    build_sport_input,
)
from foot.analysis.lineups import LineupPlan, plan_lineup_checks, record_supplied_lineups
from foot.analysis.naming import TeamIndex
from foot.analysis.quotes import offer_for_key
from foot.analysis.request import (
    DEFAULT_TIMEZONE,
    KickoffStatus,
    MatchRequest,
    RequestStatus,
    ResolvedMatch,
    combine_kickoff,
    kickoff_status,
    parse_requests,
    resolve_timezone,
)
from foot.analysis.rubrics import (
    PROTOCOL_PATH,
    RUBRICS,
    Rubric,
    RubricAssessment,
    RubricImplementation,
    RubricStatus,
    load_rubrics,
)
from foot.analysis.xg import XgBalance
from foot.collect.base import Capability, OddsSource, SeasonData, SeasonSource
from foot.collect.openfootball import COMPETITIONS, resolve_competition
from foot.collect.registry import Registry, RegistryReport
from foot.collect.supplements import FULL_LINEUP, SupplementSet
from foot.data.synthetic import SYNTHETIC_MARKER
from foot.domain import Fixture, Match, MatchLog, Outcome
from foot.markets.catalogue import standard_catalogue
from foot.markets.pricing import (
    GridDiagnostics,
    PricedOffer,
    Quote,
    grid_diagnostics,
    price_catalogue,
)
from foot.markets.selection import (
    Decision,
    RankingCriteria,
    Scenario,
    ScenarioKind,
    select_best,
)
from foot.models.base import ScoreMatrix
from foot.models.dixon_coles import DixonColesModel, dixon_coles_tau
from foot.provenance import Confidence, Evidence, Ledger, Source, utcnow
from foot.ratings.elo import EloRatingSystem, EloTable

__all__ = ["AnalysisRun", "Engine", "EngineConfig", "MatchAnalysis"]

_MODEL_VERSION = "dixon-coles+elo/2.0"


@dataclass(frozen=True, slots=True)
class EngineConfig:
    """Every modelling choice, stated so it can be argued with.

    The defaults are **assumptions**, not validated optima.  ``foot valider``
    tunes ``half_life_days`` and ``ridge`` chronologically; until it has been
    run on the competition at hand, the report says so.
    """

    half_life_days: float = 240.0
    seasons: tuple[str, ...] = ("2024-25", "2025-26", "2026-27")
    min_matches: int = 60
    ridge_pseudo_matches: float = 120.0
    """Shrinkage strength, expressed as pseudo-matches of league-average form."""

    scenario_shift: float = 0.15
    reference_stake: float = 0.02
    timezone: str = DEFAULT_TIMEZONE
    rubrics_path: Path | None = None
    """Protocol file driving the investigation grid.

    ``None`` loads the one shipped in ``protocole/``.  Pointing this at another
    JSON replaces the protocol for every report — grid, per-rubric treatment and
    stated effect — without touching a line of code.
    """

    allow_synthetic: bool = False
    """Real mode refuses synthetic data outright.

    Demonstration datasets carry an indelible marker, so the engine can detect
    them by inspection instead of trusting the caller to remember.  Set this to
    ``True`` only for a demo or a test that says so out loud.
    """

    def ridge_for(self, effective_matches: float) -> float:
        """Shrink hard on thin data, barely at all on a full history.

        Promoted clubs, new managers and the opening weeks of a season all
        produce the same problem — a handful of matches — and the same answer:
        pull the ratings toward the league average until the data earns its
        independence.
        """
        return max(0.0, self.ridge_pseudo_matches / max(effective_matches, 1.0))


@dataclass(frozen=True, slots=True)
class MatchAnalysis:
    """Everything the engine concluded about one requested line."""

    resolved: ResolvedMatch
    sealed: SealedDossier | None = None
    decision: Decision | None = None
    priced: tuple[PricedOffer, ...] = ()
    rubrics: tuple[RubricAssessment, ...] = ()
    diagnostics: GridDiagnostics | None = None
    exposure: ExposureAudit | None = None
    lineup_plan: LineupPlan | None = None
    ledger: Ledger = field(default_factory=Ledger)
    scenarios: tuple[Scenario, ...] = ()
    import_notes: tuple[str, ...] = ()
    """Supplied lines this analysis could not use, and why.

    Deliberately outside :attr:`sealed`: these are facts about the *import*, not
    about the match, and folding them into the dossier made its fingerprint
    depend on information that did not exist at the dossier's own date.
    """

    blocked_reason: str = ""

    @property
    def analysed(self) -> bool:
        return self.sealed is not None

    @property
    def rubric_coverage(self) -> float:
        if not self.rubrics:
            return 0.0
        done = sum(
            1 for a in self.rubrics
            if a.status in (RubricStatus.COVERED, RubricStatus.PARTIAL)
        )
        return done / len(self.rubrics)

    def headline(self) -> str:
        if self.decision is not None:
            return self.decision.headline()
        if self.blocked_reason:
            return self.blocked_reason
        return self.resolved.explain() or "non analysé"


@dataclass(frozen=True, slots=True)
class AnalysisRun:
    """The result of one invocation: every requested line, accounted for."""

    analyses: tuple[MatchAnalysis, ...]
    as_of: dt.datetime
    timezone: str
    registry_report: RegistryReport
    criteria: RankingCriteria
    config: EngineConfig
    started_at: dt.datetime = field(default_factory=utcnow)

    @property
    def requested(self) -> int:
        return len(self.analyses)

    def analysed(self) -> tuple[MatchAnalysis, ...]:
        return tuple(a for a in self.analyses if a.analysed)

    def unresolved(self) -> tuple[MatchAnalysis, ...]:
        return tuple(a for a in self.analyses if not a.resolved.analysable)

    def started_matches(self) -> tuple[MatchAnalysis, ...]:
        """Matches whose kick-off has passed — genuinely out of pre-match."""
        return tuple(
            a for a in self.analyses
            if a.resolved.analysable
            and not a.resolved.bettable
            and a.resolved.kickoff_status is not KickoffStatus.UNVERIFIED
        )

    def unverified_matches(self) -> tuple[MatchAnalysis, ...]:
        """Matches absent from the loaded calendar — a different problem entirely.

        Counting them as "started" told the operator to look for a kick-off that
        never happened, instead of telling them the fixture could not be found.
        """
        return tuple(
            a for a in self.analyses
            if a.resolved.kickoff_status is KickoffStatus.UNVERIFIED
        )

    def recommendations(self) -> tuple[MatchAnalysis, ...]:
        return tuple(a for a in self.analyses if a.decision and a.decision.has_bet)


class Engine:
    """Runs the whole pipeline for a batch of requested matches."""

    __slots__ = (
        "_config",
        "_criteria",
        "_duplicates",
        "_evidence",
        "_labels",
        "_price_sources",
        "_providers",
        "_registry",
        "_rubrics",
        "_used",
    )

    def __init__(
        self,
        registry: Registry,
        *,
        config: EngineConfig | None = None,
        criteria: RankingCriteria | None = None,
    ) -> None:
        self._registry = registry
        self._labels: dict[str, str] = {}
        self._config = config or EngineConfig()
        # Criteria are built here, before any price is read, and carried through.
        self._criteria = criteria or RankingCriteria(
            reference_stake=(config or EngineConfig()).reference_stake
        )
        # Depend on the *capability*, not on a concrete adapter: any provider
        # that can serve a competition-season plugs in, including a test stub.
        # Keep them all, in registry order — a provider that fails at fetch time
        # must not condemn the run when the next one works.
        self._providers: tuple[SeasonSource, ...] = tuple(
            provider
            for provider in registry
            if isinstance(provider, SeasonSource)
            and Capability.RESULTS in provider.capabilities
        )
        self._used: dict[str, str] = {}
        self._evidence: dict[str, list[Evidence]] = {}
        self._duplicates: dict[str, int] = {}
        # Providers able to quote prices, kept apart from the result sources:
        # they are consulted only *after* the sport dossier is sealed.
        self._price_sources: tuple[OddsSource, ...] = tuple(
            provider
            for provider in registry
            if isinstance(provider, OddsSource)
            and Capability.ODDS in provider.capabilities
        )
        self._rubrics = _load_grid(self._config.rubrics_path)

    @property
    def criteria(self) -> RankingCriteria:
        return self._criteria

    @property
    def rubrics(self) -> tuple[Rubric, ...]:
        """The investigation grid actually in force for this engine."""
        return self._rubrics

    @property
    def providers(self) -> tuple[SeasonSource, ...]:
        """Season sources, in the order they will be tried."""
        return self._providers

    def provider_used(self, competition: str) -> str | None:
        """Which provider actually supplied a competition's data."""
        return self._used.get(competition)

    def _competition_key(self, text: str) -> str | None:
        """Map the operator's wording onto a key some provider actually serves."""
        served = {key for p in self._providers for key in p.competitions()}
        cleaned = text.strip().lower()
        if cleaned in served:
            return cleaned
        mapped = resolve_competition(text)
        return mapped if mapped in served else None

    def _competition_label(self, key: str) -> str:
        return self._labels.get(key) or COMPETITIONS.get(key, key)

    # -- Entry point -------------------------------------------------------
    def run(
        self,
        text: str,
        *,
        as_of: dt.datetime | None = None,
        timezone: str | None = None,
        odds: Mapping[int, tuple[float, float, float]] | None = None,
        bookmaker: str | None = None,
        quoted_at: dt.datetime | None = None,
        supplements: SupplementSet | None = None,
    ) -> AnalysisRun:
        """Analyse every line of ``text``.

        Args:
            odds: prices keyed by input line number, for lines that did not
                carry them inline.
            quoted_at: when the prices were observed; used to flag stale quotes.
            supplements: operator-supplied xG, absences and lineups.  They fill
                rubrics no reachable provider can serve, and are marked as
                operator-supplied throughout so they are never mistaken for
                corroborated data.
        """
        zone_name = timezone or self._config.timezone
        resolve_timezone(zone_name)  # validated eagerly, so a typo fails loudly
        instant = as_of or utcnow()
        report = self._registry.probe()
        local_today = instant.astimezone(resolve_timezone(zone_name)).date()
        requests = parse_requests(text, today=local_today)

        rosters, histories, fixtures = self._load_competitions(requests, instant)
        analyses: list[MatchAnalysis] = []
        for request in requests:
            resolved = self._resolve(request, rosters, fixtures, instant, zone_name)
            inline = request.odds or (odds or {}).get(request.line_number)
            analyses.append(
                self._analyse_one(
                    resolved,
                    histories=histories,
                    as_of=instant,
                    report=report,
                    odds=inline,
                    bookmaker=bookmaker,
                    quoted_at=quoted_at,
                    quotes=dict(request.quotes),
                    supplements=supplements or SupplementSet(),
                )
            )
        return AnalysisRun(
            analyses=tuple(analyses),
            as_of=instant,
            timezone=zone_name,
            registry_report=report,
            criteria=self._criteria,
            config=self._config,
        )

    # -- Collection --------------------------------------------------------
    def _load_competitions(
        self, requests: Sequence[MatchRequest], as_of: dt.datetime
    ) -> tuple[dict[str, TeamIndex], dict[str, MatchLog], dict[str, tuple[Fixture, ...]]]:
        """Fetch every competition any request might belong to."""
        if not self._providers:
            return ({}, {}, {})
        served = tuple(
            dict.fromkeys(key for p in self._providers for key in p.competitions())
        )
        wanted: set[str] = set()
        for request in requests:
            text = request.competition_text
            key = self._competition_key(text) if text else None
            if key:
                wanted.add(key)
        if not wanted or any(not r.competition_text for r in requests):
            wanted.update(served)  # no competition given: search everything served

        rosters: dict[str, TeamIndex] = {}
        histories: dict[str, MatchLog] = {}
        fixtures: dict[str, tuple[Fixture, ...]] = {}
        for key in sorted(wanted & set(served)):
            matches: list[Match] = []
            upcoming: list[Fixture] = []
            names: set[str] = set()
            label = ""
            # A match is identified by ``(date, home, away)``, not by the season
            # file it came from.  A provider that ignores the season argument —
            # a manual import serves the same file for every season asked —
            # would otherwise have its 90 matches counted three times, silently
            # tripling every rate the model estimates.
            seen: set[tuple[dt.date, str, str]] = set()
            seen_fixtures: set[tuple[dt.date, str, str]] = set()
            for provider in self._providers:
                if key not in provider.competitions():
                    continue
                for season in self._config.seasons:
                    try:
                        data = provider.season(key, season)
                    except Exception:
                        continue
                    fresh = 0
                    for match in data.played:
                        identity = (match.date, match.home, match.away)
                        if identity in seen:
                            continue
                        seen.add(identity)
                        matches.append(match)
                        fresh += 1
                    names.update(data.teams)
                    label = data.label or label
                    self._used[key] = provider.name
                    self._duplicates[key] = self._duplicates.get(key, 0) + (
                        len(data.played) - fresh
                    )
                    self._evidence.setdefault(key, []).extend(
                        _dataset_evidence(provider, data)
                    )
                    for fixture in data.fixtures:
                        identity = (fixture.date, fixture.home, fixture.away)
                        if identity in seen_fixtures:
                            continue
                        seen_fixtures.add(identity)
                        upcoming.append(fixture)
                if names:
                    break  # the first provider that actually delivered wins
            if names:
                self._labels[key] = label or self._competition_label(key)
                rosters[key] = TeamIndex(sorted(names))
                # A cheap pre-filter; `build_sport_input` then applies the
                # stricter availability rule that owns the policy.
                histories[key] = MatchLog(matches).before(as_of.date(), inclusive=True)
                fixtures[key] = tuple(upcoming)
        return (rosters, histories, fixtures)

    # -- Identification ----------------------------------------------------
    def _resolve(
        self,
        request: MatchRequest,
        rosters: Mapping[str, TeamIndex],
        fixtures: Mapping[str, tuple[Fixture, ...]],
        as_of: dt.datetime,
        zone_name: str,
    ) -> ResolvedMatch:
        if not request.parsed:
            return ResolvedMatch(
                request=request,
                status=RequestStatus.UNPARSED,
                timezone=zone_name,
                missing=(
                    "deux équipes séparées par « - », « vs » ou « contre » "
                    "(exemple : « Arsenal - Chelsea »)",
                ),
            )

        declared = (
            self._competition_key(request.competition_text)
            if request.competition_text
            else None
        )
        if request.competition_text and declared is None:
            return ResolvedMatch(
                request=request,
                status=RequestStatus.UNKNOWN_COMPETITION,
                timezone=zone_name,
                missing=(f"compétition « {request.competition_text} » non couverte",),
                candidates=tuple(
                    f"{k} = {self._competition_label(k)}" for k in sorted(rosters)
                ),
            )

        search = [declared] if declared else list(rosters)
        hits: list[tuple[str, str, str]] = []
        ambiguity: list[str] = []
        for key in search:
            index = rosters.get(key)
            if index is None:
                continue
            home, away = index.resolve_pair(request.home_text, request.away_text)
            if home.resolution.name == "AMBIGUOUS" or away.resolution.name == "AMBIGUOUS":
                ambiguity.extend(home.candidates + away.candidates)
            if home.resolved and away.resolved and home.name != away.name:
                assert home.name and away.name
                hits.append((key, home.name, away.name))

        if not hits:
            if ambiguity:
                return ResolvedMatch(
                    request=request, status=RequestStatus.AMBIGUOUS, timezone=zone_name,
                    missing=(
                        "nom d'équipe trop court pour être unique : précisez le nom "
                        "complet parmi les candidats ci-dessous",
                    ),
                    candidates=tuple(dict.fromkeys(ambiguity))[:8],
                )
            suggestions: list[str] = []
            for key in search:
                index = rosters.get(key)
                if index is None:
                    continue
                for match in index.resolve_pair(request.home_text, request.away_text):
                    suggestions.extend(match.candidates)
            return ResolvedMatch(
                request=request, status=RequestStatus.UNKNOWN_TEAM, timezone=zone_name,
                missing=(
                    f"« {request.home_text} » et/ou « {request.away_text} » "
                    f"introuvables dans les compétitions couvertes "
                    f"({', '.join(sorted(rosters))})",
                ),
                candidates=tuple(dict.fromkeys(suggestions))[:8],
            )
        if len({h[0] for h in hits}) > 1:
            return ResolvedMatch(
                request=request, status=RequestStatus.AMBIGUOUS, timezone=zone_name,
                missing=("ces deux noms existent dans plusieurs compétitions : précisez-la",),
                candidates=tuple(
                    f"{k} ({self._competition_label(k)})" for k, _, _ in hits
                ),
            )

        key, home_name, away_name = hits[0]
        scheduled, nearby = self._find_fixture(
            fixtures.get(key, ()), home_name, away_name, request.date
        )
        fixture = scheduled or Fixture(
            home_name,
            away_name,
            request.date or as_of.astimezone(resolve_timezone(zone_name)).date(),
            competition=self._competition_label(key),
        )
        kickoff = combine_kickoff(fixture.date, request.time, zone_name)
        if kickoff is None and scheduled is not None:
            kickoff = None  # openfootball carries a time we do not re-import blindly
        notes: list[str] = []
        candidates: list[str] = []
        if scheduled is None:
            notes.append(
                "aucune rencontre correspondante au calendrier chargé pour cette "
                "date : l'existence de la rencontre n'est pas vérifiée"
            )
            for option in nearby:
                candidates.append(
                    f"{option.home} – {option.away} le {option.date.isoformat()}"
                )
            if candidates:
                notes.append(
                    "rencontre(s) proche(s) au calendrier : "
                    + " ; ".join(candidates)
                    + " — corrigez la date pour lever l'ambiguïté"
                )
        return ResolvedMatch(
            request=request,
            status=RequestStatus.RESOLVED,
            fixture=fixture,
            competition_key=key,
            competition_label=self._competition_label(key),
            kickoff=kickoff,
            kickoff_status=kickoff_status(
                kickoff,
                as_of,
                match_date=fixture.date,
                verified=scheduled is not None,
                timezone=zone_name,
            ),
            timezone=zone_name,
            notes=tuple(notes),
            candidates=tuple(candidates),
        )

    @staticmethod
    def _find_fixture(
        fixtures: Sequence[Fixture], home: str, away: str, date: dt.date | None
    ) -> tuple[Fixture | None, tuple[Fixture, ...]]:
        """Locate the scheduled fixture, and never slide a typed date onto another.

        Returns the confirmed fixture and any *nearby* ones as candidates.  An
        earlier version accepted a fixture up to three days from the typed date,
        which silently turned "12/09" into a different match and let a pre-match
        recommendation ride on a date nobody had verified.  A typed date must now
        match exactly; anything close is reported for the operator to confirm.
        """
        pairing = [f for f in fixtures if f.home == home and f.away == away]
        if date is None:
            return (pairing[0] if pairing else None, tuple(pairing[1:4]))
        exact = [f for f in pairing if f.date == date]
        if exact:
            return (exact[0], ())
        near = sorted(pairing, key=lambda f: abs((f.date - date).days))[:3]
        return (None, tuple(near))

    # -- Analysis ----------------------------------------------------------
    def _analyse_one(
        self,
        resolved: ResolvedMatch,
        *,
        histories: Mapping[str, MatchLog],
        as_of: dt.datetime,
        report: RegistryReport,
        odds: tuple[float, float, float] | None,
        bookmaker: str | None,
        quoted_at: dt.datetime | None,
        quotes: dict[str, float] | None = None,
        supplements: SupplementSet | None = None,
    ) -> MatchAnalysis:
        if not resolved.analysable or resolved.fixture is None:
            return MatchAnalysis(resolved=resolved)

        if not resolved.bettable:
            if resolved.kickoff_status is KickoffStatus.UNVERIFIED:
                reason = (
                    "aucune rencontre correspondante au calendrier chargé : "
                    "l'existence et l'horaire de la rencontre ne sont pas vérifiés, "
                    "donc aucune recommandation prématch n'est émise. Précisez la "
                    "date, ou attendez la publication du calendrier."
                )
            else:
                reason = (
                    f"rencontre {resolved.kickoff_status.value} : aucune analyse "
                    f"prématch n'est produite. Une lecture prématch ne doit jamais "
                    f"être présentée comme une analyse live."
                )
            return MatchAnalysis(resolved=resolved, blocked_reason=reason)

        history = histories.get(resolved.competition_key, MatchLog())
        synthetic = [m for m in history if m.competition and SYNTHETIC_MARKER in m.competition]
        if synthetic and not self._config.allow_synthetic:
            return MatchAnalysis(
                resolved=resolved,
                blocked_reason=(
                    f"données synthétiques détectées ({len(synthetic)} matchs marqués "
                    f"« {SYNTHETIC_MARKER} ») : refusées en mode réel. "
                    f"Activez explicitement EngineConfig(allow_synthetic=True) pour "
                    f"une démonstration."
                ),
            )
        if len(history) < self._config.min_matches:
            return MatchAnalysis(
                resolved=resolved,
                blocked_reason=(
                    f"historique insuffisant : {len(history)} matchs disponibles "
                    f"avant le {as_of.date().isoformat()}, minimum requis "
                    f"{self._config.min_matches}"
                ),
            )

        audit = ExposureAudit()
        ledger = Ledger()
        fixture = resolved.fixture

        # ---- Sport phase: no price may enter here ------------------------
        # The dataset evidence collected during loading travels with the input,
        # so the sealed dossier can cite the sources its findings refer to.
        extra = supplements or SupplementSet()
        # Only the dataset evidence is passed in: `build_sport_input` derives
        # the context's own evidence from the set it has just cut, so the ledger
        # and the dossier can never disagree about what was knowable.
        evidence = list(self._evidence.get(resolved.competition_key, ()))
        # One input, cut once. Everything downstream — estimate, findings,
        # scenarios, lineup check, decision — reads `sport_input`, never the
        # raw history or the raw supplements, so none of them can see further
        # back than the others.
        sport_input = build_sport_input(
            fixture,
            history,
            as_of,
            evidence,
            competition=resolved.competition_label,
            supplements=extra,
        )
        known_history = sport_input.history
        known_extra = sport_input.supplements
        # The T−75/T−60 plan is sporting information, so it is built *before*
        # the seal and fed the sheets the operator actually imported: a report
        # must never show an official lineup in one section and "no lineup
        # recorded" in another.
        plan = plan_lineup_checks(resolved, as_of)
        record_supplied_lineups(
            plan, fixture=fixture, supplements=known_extra, as_of=as_of
        )
        # Lines the operator supplied that this analysis cannot use. They are
        # part of the **import report**, never of the sealed dossier: a
        # historical dossier cannot depend on information that did not exist at
        # its own date, and a note about it changed the fingerprint.
        import_notes = extra.import_notes(
            teams=(fixture.home, fixture.away),
            on_or_before=fixture.date,
            as_of=as_of,
        )
        dossier = self._build_dossier(sport_input, ledger, known_extra, resolved, plan)
        sealed = dossier.seal()
        audit.sealed(sealed.sealed_at)

        # ---- Market phase: prices become visible only now ----------------
        matrix = sealed.score_matrix
        scenarios = self._scenarios(dossier, known_history, known_extra)
        # Every price carries its own observation time and its own source. A
        # single run-wide `quoted_at` let a re-run rejuvenate an imported odd:
        # the price was quoted 48 h ago, the analysis was asked for now, and the
        # staleness check saw a fresh price. Age belongs to the price, not to
        # the run.
        # A price **typed on the match line** is being given now: absent an
        # explicit hour it is dated at the analysis instant, which is when the
        # operator read it. A price an **importer** supplies is different — if
        # its source declares no hour, its age is genuinely unknown, and
        # inventing one would manufacture a freshness it never had.
        typed_at = quoted_at if quoted_at is not None else as_of
        prices: dict[str, Quote] = {
            key: Quote(price=value, quoted_at=typed_at, bookmaker=bookmaker)
            for key, value in (quotes or {}).items()
        }
        if odds is not None:
            for outcome, price in zip(Outcome, odds, strict=True):
                prices.setdefault(
                    f"1X2:{outcome.value}",
                    Quote(price=price, quoted_at=typed_at, bookmaker=bookmaker),
                )
        # Prices a provider can quote are collected **here**, after the seal,
        # never during loading: an imported odds file that never reaches the
        # selector is an option that exists only in the help text.
        imported, imported_at, imported_book = self._provider_prices(fixture)
        for key, price in imported.items():
            prices.setdefault(
                key,
                Quote(
                    price=price,
                    quoted_at=imported_at,
                    bookmaker=imported_book or bookmaker,
                ),
            )
        if prices:
            audit.saw_odds(f"{len(prices)} cote(s) fournies par l'opérateur")
            ledger.extend(self._odds_evidence(fixture, prices, as_of))

        # A quoted line the standard catalogue does not carry is built on
        # demand, so an operator's Asian -0.75 is compared rather than dropped.
        catalogue = list(standard_catalogue())
        known = {offer.key for offer in catalogue}
        for key in prices:
            if key not in known:
                built = offer_for_key(key)
                if built is not None:
                    catalogue.append(built)
                    known.add(key)

        priced = price_catalogue(
            catalogue, matrix, quotes=prices, quoted_at=quoted_at, as_of=as_of,
            bookmaker=bookmaker,
        )
        # `prices` is keyed by market; the market findings only need the keys.
        market_findings = _market_findings(priced, prices, scenarios)
        assessments = self._assess_rubrics(
            report,
            known_history,
            fixture,
            [*dossier.findings, *market_findings],
            known_extra,
        )
        decision = select_best(
            priced,
            criteria=self._criteria,
            scenarios=scenarios,
            rubric_coverage=_coverage(assessments),
            model_converged=bool(dossier.parameters.get("converged", False)),
            contradictions=len(ledger.contradictions()),
            sport_angle=_sport_angle(dossier),
        )
        home_rate, away_rate = dossier.expected_goals
        return MatchAnalysis(
            resolved=resolved,
            sealed=sealed,
            decision=decision,
            import_notes=import_notes,
            priced=tuple(priced),
            rubrics=tuple(assessments),
            diagnostics=grid_diagnostics(
                matrix, home_rate=home_rate, away_rate=away_rate,
                rho=_as_float(dossier.parameters.get("rho"), 0.0),
            ),
            exposure=audit,
            lineup_plan=plan,
            ledger=ledger,
            scenarios=tuple(scenarios),
        )

    def _provider_prices(
        self, fixture: Fixture
    ) -> tuple[dict[str, float], dt.datetime | None, str]:
        """1–N–2 prices a registered source quotes for this fixture.

        Consulted only after the dossier is sealed, so a price source cannot
        colour the sporting read.  A source that fails is skipped, not fatal:
        an unreachable bookmaker costs a comparison, never the analysis.
        """
        for source in self._price_sources:
            try:
                book, _evidence = source.odds()
            except Exception:
                continue
            quote = book.get(fixture)
            if quote is None:
                quote = next(
                    (
                        value
                        for key, value in book.items()
                        if (key.date, key.home, key.away)
                        == (fixture.date, fixture.home, fixture.away)
                    ),
                    None,
                )
            if quote is None:
                continue
            moment: dt.datetime | None = None
            getter = getattr(source, "quoted_at", None)
            if callable(getter):
                moment = getter()
            return (
                {
                    "1X2:H": quote.home,
                    "1X2:D": quote.draw,
                    "1X2:A": quote.away,
                },
                moment,
                source.name,
            )
        return ({}, None, "")

    # -- Sport dossier -----------------------------------------------------
    def _build_dossier(
        self,
        sport_input: SportInput,
        ledger: Ledger,
        supplements: SupplementSet | None = None,
        resolved: ResolvedMatch | None = None,
        plan: LineupPlan | None = None,
    ) -> SportDossier:
        fixture = sport_input.fixture
        history = sport_input.history
        elo = EloRatingSystem(season_regression=1 / 3).rate(history)
        model = DixonColesModel(
            half_life_days=self._config.half_life_days,
            ridge=self._config.ridge_for(_effective_matches(history, self._config.half_life_days)),
        ).fit(history, reference_date=sport_input.as_of.date())
        parameters = model.parameters
        matrix = model.score_matrix(fixture)
        home_rate, away_rate = model.rates(fixture)

        # Only the datasets the estimate is computed from.  Attaching every key
        # in the dossier would "prove" an attack coefficient with a team sheet,
        # and a traceability that attributes everything to everything cannot be
        # checked.  Operator supplements cite themselves, in their own findings.
        evidence_keys = tuple(
            item.key
            for item in sport_input.evidence
            if not item.key.startswith(("xg::", "absence::", "composition::"))
        )
        findings = self._findings(fixture, history, model, elo, evidence_keys)
        findings.extend(
            _supplement_findings(fixture, history, supplements or SupplementSet(), plan)
        )
        if resolved is not None:
            findings.insert(0, self._identification_finding(fixture, resolved))
        ledger.extend(sport_input.evidence)

        return SportDossier(
            fixture=fixture,
            as_of=sport_input.as_of,
            probabilities=matrix.outcome_probabilities(),
            score_matrix=matrix,
            expected_goals=(home_rate, away_rate),
            model_version=_MODEL_VERSION,
            parameters={
                "half_life_days": self._config.half_life_days,
                "ridge": self._config.ridge_for(
                    _effective_matches(history, self._config.half_life_days)
                ),
                "rho": parameters.rho,
                "home_advantage": parameters.home_advantage,
                "converged": parameters.diagnostics.converged,
                "log_likelihood": parameters.diagnostics.log_likelihood,
                "effective_matches": parameters.diagnostics.effective_matches,
                "attack_home": parameters.attack_of(fixture.home),
                "defence_home": parameters.defence_of(fixture.home),
                "attack_away": parameters.attack_of(fixture.away),
                "defence_away": parameters.defence_of(fixture.away),
                "elo_home": elo.rating(fixture.home),
                "elo_away": elo.rating(fixture.away),
                "teams": tuple(parameters.teams),
            },
            findings=tuple(findings),
            assumptions=(
                f"demi-vie de pondération {self._config.half_life_days:g} jours "
                f"(hypothèse, non calibrée sur cette compétition)",
                f"régularisation équivalente à {self._config.ridge_pseudo_matches:g} "
                f"matchs de forme moyenne (hypothèse)",
                _context_assumption(supplements or SupplementSet(), plan),
            ),
            evidence=sport_input.evidence,
            history_span=sport_input.history_span,
            history_size=len(history),
            counter_analysis=_counter_analysis(fixture, model, elo, supplements, plan),
        )

    def _identification_finding(self, fixture: Fixture, resolved: ResolvedMatch) -> Finding:
        """R01 in evidence form, so the grid records what was actually verified."""
        return Finding(
            rubric=1,
            kind=FindingKind.FACT,
            statement=(
                f"{fixture.home} – {fixture.away}, {fixture.competition}, "
                f"{resolved.kickoff_local()}, statut {resolved.kickoff_status.value}"
                + (" (terrain neutre)" if fixture.neutral else "")
            ),
            model_variable=None,
            effect=(
                "affiché seulement : aucune variable du modèle n'en dépend ; "
                "la vérification au calendrier agit en amont, comme condition "
                "d'accès à toute recommandation d'avant-match"
            ),
        )

    def _findings(
        self,
        fixture: Fixture,
        history: MatchLog,
        model: DixonColesModel,
        elo: EloTable,
        evidence_keys: tuple[str, ...] = (),
    ) -> list[Finding]:
        """Build the source → variable → effect chain, marking what is unused."""
        parameters = model.parameters
        findings: list[Finding] = []
        for team, side in ((fixture.home, "domicile"), (fixture.away, "extérieur")):
            recent = history.involving(team).last(10)
            points = sum(m.points_for(team) for m in recent)
            scored = sum(m.goals_for(team) for m in recent)
            conceded = sum(m.goals_against(team) for m in recent)
            findings.append(
                Finding(
                    rubric=4,
                    kind=FindingKind.FACT,
                    statement=(
                        f"{team} ({side}) : {points} pts sur {len(recent)} matchs, "
                        f"{scored} buts marqués / {conceded} encaissés"
                    ),
                    evidence_keys=evidence_keys,
                    model_variable=f"attaque[{team}], défense[{team}]",
                    effect=(
                        f"attaque {parameters.attack_of(team):+.3f}, "
                        f"défense {parameters.defence_of(team):+.3f} "
                        f"(log-buts, pondérés par ancienneté)"
                    ),
                )
            )
        findings.append(
            Finding(
                rubric=5,
                kind=FindingKind.MODEL,
                statement=(
                    "la force des adversaires est absorbée par l'estimation conjointe : "
                    "battre une équipe faible pèse moins qu'un résultat contre une forte"
                ),
                model_variable="attaque[·] et défense[·] estimées simultanément",
                effect="pas de correction ad hoc ; l'ajustement est structurel",
            )
        )
        findings.append(
            Finding(
                rubric=6,
                kind=FindingKind.MODEL,
                statement=(
                    f"avantage du terrain estimé sur {len(history)} matchs de la "
                    f"compétition"
                ),
                model_variable="home_advantage",
                effect=(
                    f"{parameters.home_advantage:+.4f} en log-buts, soit "
                    f"{(math.exp(parameters.home_advantage) - 1) * 100:+.1f}% "
                    f"de rythme offensif à domicile"
                ),
            )
        )
        for team in (fixture.home, fixture.away):
            last = history.involving(team).last(1)
            if last:
                rest = (fixture.date - last[0].date).days
                findings.append(
                    Finding(
                        rubric=13,
                        kind=FindingKind.FACT,
                        statement=f"{team} : {rest} jours depuis son dernier match",
                        evidence_keys=evidence_keys,
                        model_variable=None,
                        effect=(
                            "affiché seulement : aucun coefficient de fatigue n'est "
                            "appliqué faute d'estimation validée ; sert de déclencheur "
                            "de scénario si le repos est inférieur à 4 jours"
                        ),
                    )
                )
        findings.append(
            Finding(
                rubric=17,
                kind=FindingKind.MODEL,
                statement=(
                    f"Dixon-Coles pondéré (demi-vie {self._config.half_life_days:g} j) "
                    f"avec régularisation ; Elo en contrôle croisé "
                    f"({elo.rating(fixture.home):.0f} contre {elo.rating(fixture.away):.0f})"
                ),
                model_variable="modèle complet",
                effect=(
                    f"ρ = {parameters.rho:+.4f} ; convergence "
                    f"{'atteinte' if parameters.diagnostics.converged else 'NON ATTEINTE'}"
                ),
            )
        )
        return findings

    def _assess_rubrics(
        self,
        report: RegistryReport,
        history: MatchLog,
        fixture: Fixture,
        findings: Sequence[Finding],
        supplements: SupplementSet | None = None,
    ) -> list[RubricAssessment]:
        """Answer each rubric with its data, its treatment and its real effect."""
        supplied = _supplied_counts(supplements or SupplementSet())
        available = frozenset(
            c for s in report.statuses if s.usable for c in s.capabilities
        )
        adapters_built = frozenset(
            {"foot.collect.openfootball", "foot.collect.footballdata", "foot.collect.manual"}
        )
        by_rubric: dict[int, list[Finding]] = {}
        for finding in findings:
            by_rubric.setdefault(finding.rubric, []).append(finding)

        assessments: list[RubricAssessment] = []
        for rubric in self._rubrics:
            implementation = rubric.implementation(
                available=available, adapters_built=adapters_built
            )
            covered = by_rubric.get(rubric.number, [])
            gap = rubric.requires - available
            # A rubric the operator supplied data for is answered, not blocked.
            if gap and covered:
                implementation = RubricImplementation.OPERATOR_SUPPLIED
                gap = frozenset()
            if gap:
                blocker = {
                    RubricImplementation.BUILT_UNREACHABLE: (
                        f"adaptateur {rubric.adapter} écrit mais injoignable ici "
                        f"(manque : {', '.join(sorted(c.value for c in gap))})"
                    ),
                    RubricImplementation.OPERATOR_SUPPLIED: (
                        _unusable_import(rubric, supplied)
                        or f"aucune source automatique ; fournissez la donnée via "
                        f"{rubric.operator_import}"
                    ),
                }.get(
                    implementation,
                    "aucun adaptateur écrit pour : "
                    + ", ".join(sorted(c.value for c in gap)),
                )
                assessments.append(
                    RubricAssessment(
                        rubric=rubric,
                        status=RubricStatus.UNAVAILABLE,
                        implementation=implementation,
                        blocker=blocker,
                        treatment=rubric.treatment,
                        effect="aucun : la donnée n'est pas disponible et n'est pas "
                               "reconstituée",
                    )
                )
                continue

            data_used = (
                covered[0].statement
                if covered
                else f"{len(history)} matchs de {fixture.competition or 'la compétition'}"
            )
            effects = [f.effect for f in covered if f.effect]
            satisfied = _satisfied_requirements(
                rubric, supplements or SupplementSet(), bool(covered)
            )
            complete = not rubric.sub_requirements or len(satisfied) == len(
                rubric.sub_requirements
            )
            assessments.append(
                RubricAssessment(
                    rubric=rubric,
                    satisfied=satisfied,
                    status=RubricStatus.COVERED
                    if (covered or not rubric.requires) and complete
                    else RubricStatus.PARTIAL,
                    summary=_rubric_summary(rubric, history, fixture, covered),
                    implementation=implementation,
                    data_used=data_used,
                    treatment=rubric.treatment,
                    effect=" · ".join(effects) if effects else rubric.effect,
                    evidence_keys=tuple(
                        dict.fromkeys(k for f in covered for k in f.evidence_keys)
                    ),
                    model_variables=tuple(
                        f.model_variable for f in covered if f.model_variable
                    ),
                )
            )
        return assessments

    def _scenarios(
        self,
        dossier: SportDossier,
        history: MatchLog,
        supplements: SupplementSet | None = None,
    ) -> list[Scenario]:
        """Build the alternative pictures, each labelled with what it rests on.

        Three different things are kept apart, because calling them all "worst
        case" would claim evidence the analysis does not have:

        * **sensitivity** — deliberate ±shifts.  They measure how fragile a
          price is to an assumption, and assert nothing about likelihood.
        * **model uncertainty** — derived from how many matches actually back
          each side's ratings, so a thin sample widens the band and a full
          season narrows it.  This one is computed, not chosen.
        * **documented sporting events** — produced only when evidence supports
          them.  None can exist here: no lineup or injury provider is reachable,
          and :class:`Scenario` refuses an undocumented one by construction.
        """
        home, away = dossier.expected_goals
        rho = _as_float(dossier.parameters.get("rho"), 0.0)
        shift = self._config.scenario_shift

        def build(
            name: str,
            h: float,
            a: float,
            kind: ScenarioKind,
            basis: str,
            *,
            note: str = "",
            evidence_keys: tuple[str, ...] = (),
        ) -> Scenario:
            return Scenario(
                name=name,
                matrix=ScoreMatrix.from_rates(
                    max(h, 1e-3), max(a, 1e-3),
                    correction=lambda x, y, lam, mu: max(
                        dixon_coles_tau(x, y, lam, mu, rho), 0.0
                    ),
                ),
                kind=kind,
                basis=basis,
                note=note,
                evidence_keys=evidence_keys,
            )

        sensitivity_basis = (
            f"variation arbitraire de ±{shift:.0%} appliquée aux buts attendus ; "
            f"mesure de fragilité, pas une prévision"
        )
        scenarios = [
            build("attaque domicile −%.0f%%" % (shift * 100), home * (1 - shift), away,
                  ScenarioKind.SENSITIVITY, sensitivity_basis),
            build("attaque extérieur −%.0f%%" % (shift * 100), home, away * (1 - shift),
                  ScenarioKind.SENSITIVITY, sensitivity_basis),
            build("match fermé", home * (1 - shift), away * (1 - shift),
                  ScenarioKind.SENSITIVITY, sensitivity_basis),
            build("match ouvert", home * (1 + shift), away * (1 + shift),
                  ScenarioKind.SENSITIVITY, sensitivity_basis),
        ]

        # Estimation noise.  For a Poisson log-link the variance of an
        # estimated log-rate is about 1 / (m · lambda) with m matches per team,
        # and a predicted rate combines two parameters (the home attack and the
        # away defence), so the standard error is sqrt(2 / (m · lambda)).  It
        # narrows as the sample grows, which a fixed percentage never does.
        effective = _as_float(dossier.parameters.get("effective_matches"), 0.0)
        roster = dossier.parameters.get("teams", ())
        team_count = max(len(roster) if isinstance(roster, tuple) else 20, 2)
        per_team = max(2.0 * effective / team_count, 1.0)
        mean_rate = max(0.5 * (home + away), 0.2)
        sigma = math.sqrt(2.0 / max(per_team * mean_rate, 1e-6))
        uncertainty_basis = (
            f"±1 écart-type d'estimation (±{math.expm1(sigma) * 100:.0f}% de rythme "
            f"offensif), dérivé de {per_team:.0f} matchs effectifs par équipe — "
            f"bande bilatérale, non utilisée comme filtre"
        )
        scenarios.append(
            build(
                "ratings surestimés (−1 σ)",
                home * math.exp(-sigma), away * math.exp(-sigma),
                ScenarioKind.MODEL_UNCERTAINTY, uncertainty_basis,
            )
        )
        scenarios.append(
            build(
                "ratings sous-estimés (+1 σ)",
                home * math.exp(sigma), away * math.exp(sigma),
                ScenarioKind.MODEL_UNCERTAINTY, uncertainty_basis,
            )
        )
        scenarios.extend(
            _documented_scenarios(
                dossier,
                history,
                supplements or SupplementSet(),
                build,
            )
        )
        return scenarios

    @staticmethod
    def _odds_evidence(
        fixture: Fixture,
        prices: Mapping[str, Quote],
        as_of: dt.datetime,
    ) -> list[Evidence]:
        """One entry per quoted market, each with **its own** time and source.

        A single timestamp for the whole run would date an odd imported two days
        ago to the moment the operator pressed the button.
        """
        entries: list[Evidence] = []
        for key, quote in sorted(prices.items()):
            moment = quote.quoted_at or as_of
            age = as_of - moment
            hours = age.total_seconds() / 3600.0
            entries.append(
                Evidence(
                    key=f"cote::{key}::{fixture.home} vs {fixture.away}",
                    value=f"{quote.price:.2f}",
                    source=Source(
                        name=quote.bookmaker or "cotes opérateur",
                        provider="opérateur",
                    ),
                    retrieved_at=moment,
                    status=Confidence.PROBABLE,
                    fact_date=fixture.date,
                    note=(
                        f"prix fourni pour {key} ; relevé il y a {hours:.1f} h "
                        f"({moment.strftime('%Y-%m-%d %H:%M %Z')}) ; non recoupé"
                        if quote.quoted_at is not None
                        else f"prix fourni pour {key} ; heure de relevé non "
                        f"déclarée : l'ancienneté ne peut pas être vérifiée"
                    ),
                )
            )
        return entries


def _mentions(item: Evidence, fixture: Fixture) -> bool:
    """Whether a supplied fact concerns one of the two clubs in this fixture."""
    return fixture.home in item.key or fixture.away in item.key


def _xg_balance(
    team: str, history: MatchLog, supplements: SupplementSet, cutoff: dt.date
) -> XgBalance | None:
    """Pair each supplied xG row with the actual result of the same match.

    Pairing on ``(date, home, away)`` rather than counting the last *n* results
    is what keeps the comparison honest: an xG file covering a different set of
    matches than the history would otherwise produce a meaningless ratio.
    """
    played = {
        (m.date, m.home, m.away): m
        for m in history
        if m.involves(team) and m.date <= cutoff
    }
    goals = expected = 0.0
    matches = 0
    keys: list[str] = []
    for row in supplements.xg:
        if team not in (row.home, row.away) or row.date > cutoff:
            continue
        match = played.get((row.date, row.home, row.away))
        if match is None:
            continue  # xG for a match the history does not contain: skipped
        goals += match.goals_for(team)
        expected += row.home_xg if row.home == team else row.away_xg
        matches += 1
        keys.append(f"xg::{row.home} vs {row.away}::{row.date.isoformat()}")
    if matches == 0:
        return None
    return XgBalance(
        team=team, matches=matches, goals=goals, expected=expected, keys=tuple(keys)
    )


_SUB_REQUIREMENT_SOURCES: Mapping[str, str] = {
    "buts": "results",
    "xG": "xg",
    "xGA": "xg",
}
"""Which supplied dataset answers each sub-requirement. Absent ⇒ nothing does."""


def _satisfied_requirements(
    rubric: Rubric, supplements: SupplementSet, has_finding: bool
) -> tuple[str, ...]:
    """Which parts of a composite rubric the available data actually answers.

    Ticking a whole rubric because one of its six parts arrived is how a report
    overstates its own coverage.  A part with no known source stays unmet, and
    the report names it.
    """
    if not rubric.sub_requirements:
        return ()
    served = {"results"}
    if supplements.xg and has_finding:
        served.add("xg")
    return tuple(
        requirement
        for requirement in rubric.sub_requirements
        if _SUB_REQUIREMENT_SOURCES.get(requirement) in served
    )


def _supplied_counts(supplements: SupplementSet) -> Mapping[str, int]:
    """How many rows the operator supplied through each import route."""
    return {
        "--xg-csv": len(supplements.xg),
        "--absences-csv": len(supplements.absences),
        "--compositions-csv": len(supplements.lineups),
    }


def _unusable_import(rubric: Rubric, supplied: Mapping[str, int]) -> str:
    """Say that supplied rows could not be used, instead of asking for them again.

    Telling an operator to provide data they have just provided sends them round
    a loop that cannot terminate.  When rows came in but none matched this
    fixture — wrong team spelling, a date outside the loaded history — the
    blocker must name that, so the fix is the one that actually helps.
    """
    option = rubric.operator_import.split()[0] if rubric.operator_import else ""
    count = supplied.get(option, 0)
    if count <= 0:
        return ""
    return (
        f"{count} ligne(s) importées via {option}, "
        f"aucune exploitable pour cette rencontre "
        f"(noms d'équipes ou dates hors de l'historique chargé) ; "
        f"rien n'a été substitué"
    )


def _supplement_findings(
    fixture: Fixture,
    history: MatchLog,
    supplements: SupplementSet,
    plan: LineupPlan | None = None,
) -> list[Finding]:
    """Turn operator-supplied context into traceable findings.

    Each one states plainly whether it moved the forecast.  xG does not: no
    xG-weighted likelihood has been calibrated here, so it is reported and used
    to build a scenario, never folded silently into the estimate.
    """
    findings: list[Finding] = []
    for team in (fixture.home, fixture.away):
        balance = _xg_balance(team, history, supplements, fixture.date)
        if balance is not None:
            findings.append(
                Finding(
                    rubric=7,
                    kind=FindingKind.FACT,
                    statement=(
                        f"{team} : {balance.goals:.0f} buts pour {balance.expected:.2f} xG "
                        f"sur {balance.matches} match(s) apparié(s) "
                        f"(ratio {balance.ratio:.2f})"
                    ),
                    evidence_keys=balance.keys[:4],
                    model_variable=None,
                    effect=(
                        "affiché seulement : l'estimation reste fondée sur les buts. "
                        "L'écart alimente un scénario de retour au niveau xG dont "
                        "l'ampleur est mesurée sur ce ratio."
                    ),
                )
            )
        absences = supplements.absences_for(team, on_or_before=fixture.date)
        if absences:
            decisive = [row for row in absences if row.decisive]
            findings.append(
                Finding(
                    rubric=11,
                    kind=FindingKind.FACT,
                    statement=(
                        f"{team} : {len(absences)} absence(s) signalée(s), dont "
                        f"{len(decisive)} à un poste décisif "
                        f"({', '.join(row.player for row in decisive) or 'aucun'})"
                    ),
                    evidence_keys=tuple(
                        f"absence::{row.team}::{row.player}" for row in absences
                    ),
                    model_variable=None,
                    effect=(
                        "n'entre pas dans l'estimation ; produit un scénario sportif "
                        "documenté qui peut faire rejeter un pari. Aucun coefficient "
                        "du type « −15 % par absent » n'est appliqué au modèle."
                    ),
                )
            )
        opponent = fixture.away if team == fixture.home else fixture.home
        sheets = supplements.lineup_for(
            team, match_date=fixture.date, opponent=opponent
        )
        if sheets:
            official = all(row.status is Confidence.CONFIRMED for row in sheets)
            starters = [row for row in sheets if row.starting]
            keeper = next(
                (row for row in starters if "gardien" in row.role.lower()), None
            )
            findings.append(
                Finding(
                    rubric=10,
                    kind=FindingKind.FACT,
                    statement=(
                        f"{team} : composition {'officielle' if official else 'probable'} "
                        f"relevée ({len(starters)} titulaire(s) sur {FULL_LINEUP}, "
                        f"{len(sheets) - len(starters)} au banc)"
                        + (
                            f", gardien titulaire {keeper.player}"
                            if keeper
                            else ", gardien titulaire non identifié"
                        )
                    ),
                    evidence_keys=tuple(
                        f"composition::{row.team}::{row.role or row.player}"
                        for row in sheets
                    )[:6],
                    model_variable=None,
                    effect="alimente le contrôle T−75/T−60 et la réévaluation du dossier",
                )
            )
    if plan is not None and plan.observations:
        findings.append(
            Finding(
                rubric=21,
                kind=FindingKind.FACT,
                statement=plan.state(),
                evidence_keys=tuple(
                    f"composition::{row.team}::{row.role or row.player}"
                    for row in supplements.lineups
                )[:6],
                model_variable=None,
                effect=(
                    "verdict de réévaluation enregistré ; "
                    + (
                        "le dossier doit être re-tarifé"
                        if plan.impact is not None and plan.impact.requires_reprice
                        else "aucune re-tarification déclenchée"
                    )
                    + ". Aucun automatisme ne relève les compositions ici : "
                    "le contrôle reste manuel et la fiche le dit."
                ),
            )
        )
    return findings


def _market_findings(
    priced: Sequence[PricedOffer],
    prices: Mapping[str, Quote],
    scenarios: Sequence[Scenario],
) -> list[Finding]:
    """Findings produced by the market phase, so the grid reflects it too.

    Assembled *after* pricing rather than inside the sealed dossier: whether a
    market could be compared is a fact about the prices supplied, not about the
    sporting view, and the dossier must stay blind to it.
    """
    quoted = [item for item in priced if item.has_price]
    findings: list[Finding] = []
    if quoted:
        findings.append(
            Finding(
                rubric=19,
                kind=FindingKind.FACT,
                statement=(
                    f"{len(quoted)} marché(s) cotés sur {len(priced)} au catalogue "
                    f"({', '.join(sorted({i.offer.family.value for i in quoted}))})"
                ),
                evidence_keys=tuple(f"cote::{key}" for key in sorted(prices))[:6],
                model_variable=None,
                effect="mis en concurrence ; les autres n'ont pas de prix et ne "
                       "peuvent pas être comparés",
            )
        )
    findings.append(
        Finding(
            rubric=18,
            kind=FindingKind.SCENARIO,
            statement=(
                f"{len(scenarios)} scénario(s) : "
                + ", ".join(
                    f"{sum(1 for s in scenarios if s.kind is kind)} {kind.value}"
                    for kind in ScenarioKind
                    if any(s.kind is kind for s in scenarios)
                )
            ),
            evidence_keys=tuple(
                dict.fromkeys(k for s in scenarios for k in s.evidence_keys)
            )[:4],
            model_variable=None,
            effect="seuls la sensibilité et les événements documentés peuvent "
                   "rejeter un pari",
        )
    )
    return findings


def _documented_scenarios(
    dossier: SportDossier,
    history: MatchLog,
    supplements: SupplementSet,
    build: Callable[..., Scenario],
) -> list[Scenario]:
    """Scenarios backed by a reported fact — the only kind entitled to a worst case.

    Their magnitudes differ in nature, and the basis says which is which:

    * **xG reversion** is *measured* — a side scoring 20 goals on 14 xG has its
      rate scaled by 14/20, a ratio read off the supplied data;
    * **a decisive absence** is *documented but not quantified* — the absence is
      sourced, while the size of its effect is an explicit hypothesis, because
      no validated estimate of what a given player is worth exists here.
    """
    home_team, away_team = dossier.fixture.home, dossier.fixture.away
    home_rate, away_rate = dossier.expected_goals
    scenarios: list[Scenario] = []

    for team, is_home in ((home_team, True), (away_team, False)):
        balance = _xg_balance(team, history, supplements, dossier.fixture.date)
        if balance is None or not balance.material:
            continue
        factor = balance.reversion_factor
        scenarios.append(
            build(
                f"retour au niveau xG ({team})",
                home_rate * (factor if is_home else 1.0),
                away_rate * (1.0 if is_home else factor),
                ScenarioKind.SPORTING_EVENT,
                balance.describe(),
                evidence_keys=balance.keys[:4],
            )
        )

    for team, is_home in ((home_team, True), (away_team, False)):
        decisive = [
            row
            for row in supplements.absences_for(team, on_or_before=dossier.fixture.date)
            if row.decisive and row.status.is_usable
        ]
        if not decisive:
            continue
        impact = combined_impact(decisive)
        if impact.neutral:
            continue
        # The channel matters more than the size: a missing keeper weakens the
        # defence, so it is the *opponent's* rate that rises.
        own, other = impact.own_attack, impact.opponent_attack
        scenarios.append(
            build(
                f"absences décisives ({team})",
                home_rate * (own if is_home else other),
                away_rate * (other if is_home else own),
                ScenarioKind.SPORTING_EVENT,
                describe_absences(decisive),
                evidence_keys=tuple(f"absence::{r.team}::{r.player}" for r in decisive),
            )
        )
    return scenarios


def _load_grid(path: Path | None) -> tuple[Rubric, ...]:
    """Load the protocol, falling back to the built-in grid if the file is gone."""
    candidate = path or PROTOCOL_PATH
    try:
        return load_rubrics(candidate)
    except (OSError, ValueError, KeyError):
        return RUBRICS


def _dataset_evidence(provider: SeasonSource, data: SeasonData) -> list[Evidence]:
    """Turn a fetched dataset into ledger evidence.

    Without this the dossier was sealed with an empty ``evidence`` field while
    its findings cited source keys that existed nowhere — the report claimed a
    traceability it could not honour.
    """
    source = Source(
        name=getattr(provider, "name", "fournisseur"),
        provider=getattr(provider, "upstream", getattr(provider, "name", "inconnu")),
        url=data.url or None,
    )
    key = f"résultats::{data.competition}::{data.season}" if data.season else (
        f"résultats::{data.competition}"
    )
    return [
        Evidence(
            key=key,
            value=f"{len(data.played)} matchs joués, {len(data.fixtures)} à venir",
            source=source,
            retrieved_at=data.retrieved_at,
            status=Confidence.CONFIRMED if data.played else Confidence.UNAVAILABLE,
            fact_date=data.played.end if data.played else None,
            note=data.label,
        )
    ]


def _as_float(value: object, default: float) -> float:
    """Read a numeric parameter out of the untyped parameter mapping."""
    return float(value) if isinstance(value, (int, float)) else default


def _effective_matches(history: MatchLog, half_life_days: float) -> float:
    if not history:
        return 0.0
    decay = math.log(2.0) / half_life_days
    end = history.end
    return math.fsum(math.exp(-decay * (end - m.date).days) for m in history)


def _coverage(assessments: Sequence[RubricAssessment]) -> float:
    if not assessments:
        return 0.0
    done = sum(
        1 for a in assessments
        if a.status in (RubricStatus.COVERED, RubricStatus.PARTIAL)
    )
    return done / len(assessments)


def _rubric_summary(  # noqa: PLR0911 - a lookup table written as guards
    rubric: Rubric, history: MatchLog, fixture: Fixture, findings: Sequence[Finding]
) -> str:
    if findings:
        return findings[0].statement
    if rubric.number == 1:
        return f"{fixture.home} – {fixture.away}, {fixture.competition}"
    if rubric.number == 2:
        return "sources et horodatages consignés au registre"
    if rubric.number == 3:
        return "dossier sportif scellé avant toute lecture de cote"
    if rubric.number == 20:
        return "loi jointe des scores ; règlements asiatiques traités explicitement"
    if rubric.number == 22:
        return "fiche produite avec confiance, risque et condition d'annulation"
    return f"{len(history)} matchs d'historique exploités"


def _sport_angle(dossier: SportDossier) -> str:
    home, away = dossier.expected_goals
    probabilities = dossier.probabilities
    return (
        f"buts attendus {home:.2f} – {away:.2f} ; "
        f"{probabilities} ; score central "
        f"{dossier.score_matrix.most_likely_score()[0]}"
    )


def _context_assumption(supplements: SupplementSet, plan: LineupPlan | None) -> str:
    """State what contextual data the dossier rests on, as it really stands."""
    supplied: list[str] = []
    if supplements.xg:
        supplied.append("xG")
    if supplements.absences:
        supplied.append("absences")
    if plan is not None and plan.observations:
        supplied.append("compositions")
    if not supplied:
        return (
            "xG, compositions et absences indisponibles : rubriques marquées "
            "comme telles, aucune valeur substituée"
        )
    return (
        f"{', '.join(supplied)} issus d'une saisie opérateur non recoupée "
        f"(source unique, statut porté ligne à ligne) ; aucune de ces données "
        f"n'entre dans l'estimation, elles alimentent les scénarios documentés"
    )


def _counter_analysis(
    fixture: Fixture,
    model: DixonColesModel,
    elo: EloTable,
    supplements: SupplementSet | None = None,
    plan: LineupPlan | None = None,
) -> tuple[str, ...]:
    """What would have to be true for the main read to be wrong.

    The text follows what was actually supplied.  Claiming xG are unavailable on
    a card that prints a goals/xG ratio two sections above describes a different
    dossier than the one produced, and a counter-analysis that does not match its
    own report cannot be checked by the reader.
    """
    parameters = model.parameters
    elo_gap = elo.rating(fixture.home) - elo.rating(fixture.away)
    dc_gap = parameters.strength(fixture.home) - parameters.strength(fixture.away)
    extra = supplements or SupplementSet()
    has_xg = any(
        fixture.home in (row.home, row.away) or fixture.away in (row.home, row.away)
        for row in extra.xg
    )
    has_sheets = bool(plan is not None and plan.observations)
    if has_xg:
        lines = [
            "Mécanisme d'invalidation : le modèle n'observe que les buts. "
            "L'écart aux xG fournis mesure cette sur-performance, mais ces xG "
            "sont une saisie opérateur, non recoupée par une seconde source "
            "indépendante, et n'entrent dans aucune variable du modèle.",
        ]
    else:
        lines = [
            "Mécanisme d'invalidation : le modèle n'observe que les buts. "
            "Une équipe qui sur-performe ses occasions verra sa force surestimée ; "
            "les xG, qui le détecteraient, sont indisponibles ici.",
        ]
    missing = []
    if not has_xg:
        missing.append("xG et npxG sur les 10 derniers matchs")
    if not has_sheets:
        missing.append("la composition officielle (gardien, charnière, meneur)")
    if missing:
        lines.append("Donnée qui trancherait : " + ", et ".join(missing) + ".")
    else:
        lines.append(
            "Donnée qui trancherait : un second relevé xG d'origine indépendante, "
            "et les minutes réellement jouées après le coup d'envoi — les deux "
            "permettraient de recouper une saisie aujourd'hui unique."
        )
    if (elo_gap > 0) != (dc_gap > 0):
        lines.append(
            f"Signal contradictoire : Elo place {fixture.home} "
            f"{'devant' if elo_gap > 0 else 'derrière'} tandis que Dixon-Coles "
            f"conclut l'inverse — prudence, les deux lectures divergent."
        )
    return tuple(lines)
