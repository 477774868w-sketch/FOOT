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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from foot.analysis.dossier import (
    ExposureAudit,
    Finding,
    FindingKind,
    SealedDossier,
    SportDossier,
    SportInput,
    build_sport_input,
)
from foot.analysis.lineups import LineupPlan, plan_lineup_checks
from foot.analysis.naming import TeamIndex
from foot.analysis.request import (
    DEFAULT_TIMEZONE,
    MatchRequest,
    RequestStatus,
    ResolvedMatch,
    combine_kickoff,
    kickoff_status,
    parse_requests,
    resolve_timezone,
)
from foot.analysis.rubrics import (
    RUBRICS,
    Rubric,
    RubricAssessment,
    RubricStatus,
)
from foot.collect.base import Capability, SeasonSource
from foot.collect.openfootball import COMPETITIONS, resolve_competition
from foot.collect.registry import Registry, RegistryReport
from foot.data.synthetic import SYNTHETIC_MARKER
from foot.domain import Fixture, Match, MatchLog, Outcome
from foot.markets.catalogue import standard_catalogue
from foot.markets.pricing import GridDiagnostics, PricedOffer, grid_diagnostics, price_catalogue
from foot.markets.selection import Decision, RankingCriteria, Scenario, select_best
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
        return tuple(
            a for a in self.analyses
            if a.resolved.analysable and not a.resolved.bettable
        )

    def recommendations(self) -> tuple[MatchAnalysis, ...]:
        return tuple(a for a in self.analyses if a.decision and a.decision.has_bet)


class Engine:
    """Runs the whole pipeline for a batch of requested matches."""

    __slots__ = ("_config", "_criteria", "_labels", "_provider", "_registry")

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
        self._provider: SeasonSource | None = None
        for provider in registry:
            if isinstance(provider, SeasonSource) and Capability.RESULTS in provider.capabilities:
                self._provider = provider
                break

    @property
    def criteria(self) -> RankingCriteria:
        return self._criteria

    def _competition_key(self, text: str) -> str | None:
        """Map the operator's wording onto a key the active provider serves."""
        served = set(self._provider.competitions()) if self._provider else set()
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
    ) -> AnalysisRun:
        """Analyse every line of ``text``.

        Args:
            odds: prices keyed by input line number, for lines that did not
                carry them inline.
            quoted_at: when the prices were observed; used to flag stale quotes.
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
                    resolved, histories, instant, report, inline, bookmaker, quoted_at
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
        if self._provider is None:
            return ({}, {}, {})
        served = tuple(self._provider.competitions())
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
            for season in self._config.seasons:
                try:
                    data = self._provider.season(key, season)
                except Exception:
                    continue
                matches.extend(data.played)
                names.update(data.teams)
                label = data.label or label
                if season == self._config.seasons[-1]:
                    upcoming.extend(data.fixtures)
            if names:
                self._labels[key] = label or self._competition_label(key)
                rosters[key] = TeamIndex(sorted(names))
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
        scheduled = self._find_fixture(fixtures.get(key, ()), home_name, away_name, request.date)
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
        if scheduled is not None and request.date and scheduled.date != request.date:
            notes.append(
                f"date saisie {request.date.isoformat()} corrigée sur le calendrier : "
                f"la rencontre est programmée le {scheduled.date.isoformat()}"
            )
        if scheduled is None:
            notes.append(
                "aucune rencontre correspondante au calendrier chargé : "
                "analyse conduite sur la paire d'équipes, date à confirmer"
            )
        return ResolvedMatch(
            request=request,
            status=RequestStatus.RESOLVED,
            fixture=fixture,
            competition_key=key,
            competition_label=self._competition_label(key),
            kickoff=kickoff,
            kickoff_status=kickoff_status(kickoff, as_of),
            timezone=zone_name,
            notes=tuple(notes),
        )

    @staticmethod
    def _find_fixture(
        fixtures: Sequence[Fixture], home: str, away: str, date: dt.date | None
    ) -> Fixture | None:
        candidates = [f for f in fixtures if f.home == home and f.away == away]
        if date is not None:
            exact = [f for f in candidates if f.date == date]
            if exact:
                return exact[0]
            near = [f for f in candidates if abs((f.date - date).days) <= 3]
            if near:
                return near[0]
        return candidates[0] if candidates else None

    # -- Analysis ----------------------------------------------------------
    def _analyse_one(
        self,
        resolved: ResolvedMatch,
        histories: Mapping[str, MatchLog],
        as_of: dt.datetime,
        report: RegistryReport,
        odds: tuple[float, float, float] | None,
        bookmaker: str | None,
        quoted_at: dt.datetime | None,
    ) -> MatchAnalysis:
        if not resolved.analysable or resolved.fixture is None:
            return MatchAnalysis(resolved=resolved)

        if not resolved.bettable:
            return MatchAnalysis(
                resolved=resolved,
                blocked_reason=(
                    f"rencontre {resolved.kickoff_status.value} : aucune analyse "
                    f"prématch n'est produite. Une lecture prématch ne doit jamais "
                    f"être présentée comme une analyse live."
                ),
            )

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
        sport_input = build_sport_input(
            fixture, history, as_of, competition=resolved.competition_label
        )
        dossier, assessments = self._build_dossier(sport_input, report, ledger)
        sealed = dossier.seal()
        audit.sealed(sealed.sealed_at)

        # ---- Market phase: prices become visible only now ----------------
        matrix = sealed.score_matrix
        scenarios = self._scenarios(dossier)
        catalogue = standard_catalogue()
        quotes: dict[str, float] = {}
        if odds is not None:
            audit.saw_odds("cotes 1X2 fournies par l'opérateur")
            for outcome, price in zip(Outcome, odds, strict=True):
                quotes[f"1X2:{outcome.value}"] = price
            ledger.extend(self._odds_evidence(fixture, odds, bookmaker, quoted_at or as_of))

        priced = price_catalogue(
            catalogue, matrix, quotes=quotes, quoted_at=quoted_at, as_of=as_of,
            bookmaker=bookmaker,
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
            priced=tuple(priced),
            rubrics=tuple(assessments),
            diagnostics=grid_diagnostics(
                matrix, home_rate=home_rate, away_rate=away_rate,
                rho=_as_float(dossier.parameters.get("rho"), 0.0),
            ),
            exposure=audit,
            lineup_plan=plan_lineup_checks(resolved, as_of),
            ledger=ledger,
            scenarios=tuple(scenarios),
        )

    # -- Sport dossier -----------------------------------------------------
    def _build_dossier(
        self, sport_input: SportInput, report: RegistryReport, ledger: Ledger
    ) -> tuple[SportDossier, list[RubricAssessment]]:
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

        findings = self._findings(fixture, history, model, elo)
        assessments = self._assess_rubrics(report, history, fixture, findings)
        ledger.extend(sport_input.evidence)

        dossier = SportDossier(
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
            },
            findings=tuple(findings),
            assumptions=(
                f"demi-vie de pondération {self._config.half_life_days:g} jours "
                f"(hypothèse, non calibrée sur cette compétition)",
                f"régularisation équivalente à {self._config.ridge_pseudo_matches:g} "
                f"matchs de forme moyenne (hypothèse)",
                "xG, compositions et absences indisponibles : rubriques marquées "
                "comme telles, aucune valeur substituée",
            ),
            evidence=sport_input.evidence,
            history_span=sport_input.history_span,
            history_size=len(history),
            counter_analysis=_counter_analysis(fixture, model, elo),
        )
        return (dossier, assessments)

    def _findings(
        self,
        fixture: Fixture,
        history: MatchLog,
        model: DixonColesModel,
        elo: EloTable,
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
                    evidence_keys=(f"résultats::{fixture.competition}",),
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
                        evidence_keys=(f"résultats::{fixture.competition}",),
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
    ) -> list[RubricAssessment]:
        available = {c for s in report.statuses if s.usable for c in s.capabilities}
        by_rubric: dict[int, list[Finding]] = {}
        for finding in findings:
            by_rubric.setdefault(finding.rubric, []).append(finding)

        assessments: list[RubricAssessment] = []
        for rubric in RUBRICS:
            gap = rubric.requires - available
            covered = by_rubric.get(rubric.number, [])
            if gap:
                assessments.append(
                    RubricAssessment(
                        rubric=rubric,
                        status=RubricStatus.UNAVAILABLE,
                        blocker="aucun fournisseur accessible pour : "
                        + ", ".join(sorted(c.value for c in gap)),
                    )
                )
                continue
            assessments.append(
                RubricAssessment(
                    rubric=rubric,
                    status=RubricStatus.COVERED if covered or not rubric.requires
                    else RubricStatus.PARTIAL,
                    summary=_rubric_summary(rubric, history, fixture, covered),
                    model_variables=tuple(
                        f.model_variable for f in covered if f.model_variable
                    ),
                )
            )
        return assessments

    def _scenarios(self, dossier: SportDossier) -> list[Scenario]:
        """Credible sporting alternatives used to stress every candidate bet."""
        home, away = dossier.expected_goals
        rho = _as_float(dossier.parameters.get("rho"), 0.0)
        shift = self._config.scenario_shift

        def build(name: str, h: float, a: float, note: str) -> Scenario:
            return Scenario(
                name=name,
                matrix=ScoreMatrix.from_rates(
                    h, a,
                    correction=lambda x, y, lam, mu: max(
                        dixon_coles_tau(x, y, lam, mu, rho), 0.0
                    ),
                ),
                note=note,
            )

        return [
            build("attaque domicile −15 %", home * (1 - shift), away,
                  "titulaire offensif absent ou bloc bas adverse"),
            build("attaque extérieur −15 %", home, away * (1 - shift),
                  "voyage, rotation ou absence offensive à l'extérieur"),
            build("match fermé", home * (1 - shift), away * (1 - shift),
                  "deux équipes prudentes, faible rythme"),
            build("match ouvert", home * (1 + shift), away * (1 + shift),
                  "rythme élevé, défenses en difficulté"),
        ]

    @staticmethod
    def _odds_evidence(
        fixture: Fixture,
        odds: tuple[float, float, float],
        bookmaker: str | None,
        quoted_at: dt.datetime,
    ) -> list[Evidence]:
        source = Source(name=bookmaker or "cotes opérateur", provider="opérateur")
        return [
            Evidence(
                key=f"cote::{fixture.home} vs {fixture.away}",
                value=f"{odds[0]:.2f} / {odds[1]:.2f} / {odds[2]:.2f}",
                source=source,
                retrieved_at=quoted_at,
                status=Confidence.PROBABLE,
                fact_date=fixture.date,
                note="prix fourni par l'opérateur ; non recoupé",
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


def _counter_analysis(
    fixture: Fixture, model: DixonColesModel, elo: EloTable
) -> tuple[str, ...]:
    """What would have to be true for the main read to be wrong."""
    parameters = model.parameters
    elo_gap = elo.rating(fixture.home) - elo.rating(fixture.away)
    dc_gap = parameters.strength(fixture.home) - parameters.strength(fixture.away)
    lines = [
        "Mécanisme d'invalidation : le modèle n'observe que les buts. "
        "Une équipe qui sur-performe ses occasions verra sa force surestimée ; "
        "les xG, qui le détecteraient, sont indisponibles ici.",
        "Donnée qui trancherait : xG et npxG sur les 10 derniers matchs, "
        "et la composition officielle (gardien, charnière, meneur).",
    ]
    if (elo_gap > 0) != (dc_gap > 0):
        lines.append(
            f"Signal contradictoire : Elo place {fixture.home} "
            f"{'devant' if elo_gap > 0 else 'derrière'} tandis que Dixon-Coles "
            f"conclut l'inverse — prudence, les deux lectures divergent."
        )
    return tuple(lines)
