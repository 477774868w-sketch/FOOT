"""Acceptance tests for the whole journey, from typed line to decision.

Every scenario the operator listed is exercised here.  None of them touches the
network: a stub provider supplies controlled data, so the suite runs anywhere
and fails for the right reasons.  The tests that do use the live provider are in
``test_live_sources.py`` and skip themselves when it is unreachable.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import io
from collections.abc import Sequence
from zoneinfo import ZoneInfo

from foot.analysis.dossier import OddsLeakError, SportInput, assert_odds_free
from foot.analysis.engine import Engine, EngineConfig
from foot.analysis.lineups import LineupImpact, LineupObservation
from foot.analysis.request import KickoffStatus, RequestStatus, kickoff_status
from foot.cli import main
from foot.collect.base import (
    Capability,
    ProviderStatus,
    Reachability,
    ResultSet,
    SeasonData,
)
from foot.collect.registry import Registry
from foot.data.synthetic import SYNTHETIC_MARKER, synthetic_league
from foot.domain import Fixture, Match, MatchLog, Score
from foot.models.dixon_coles import DixonColesModel
from foot.provenance import Confidence, Evidence, Ledger, Source, utcnow
from foot.report.card import render_card
from foot.report.table import render_summary
from foot.report.web import build_page, render_run
from foot.validation.chrono import ChronoFold, chronological_folds

from support import assert_close, assert_raises

PARIS = ZoneInfo("Europe/Paris")
AS_OF = dt.datetime(2026, 9, 13, 12, 0, tzinfo=PARIS)

_TEAMS = [f"Club {chr(65 + i)}" for i in range(10)]
_COMP = "Ligue de contrôle"


def _controlled_history(*, start: dt.date = dt.date(2025, 8, 1), rounds: int = 14) -> MatchLog:
    """A deterministic double round-robin, strong teams first in the list."""
    matches: list[Match] = []
    day = start
    for round_number in range(rounds):
        for i in range(0, len(_TEAMS), 2):
            home, away = _TEAMS[i], _TEAMS[(i + 1 + round_number) % len(_TEAMS)]
            if home == away:
                continue
            strength = (_TEAMS.index(away) - _TEAMS.index(home)) // 3
            matches.append(
                Match(home, away, day, Score(max(0, 1 + strength), max(0, 1 - strength)),
                      competition=_COMP)
            )
        day += dt.timedelta(days=7)
    return MatchLog(matches)


class StubProvider:
    """A provider with fixed, inspectable data and declared capabilities."""

    def __init__(
        self,
        history: MatchLog,
        fixtures: Sequence[Fixture] = (),
        *,
        name: str = "stub",
        capabilities: frozenset[Capability] | None = None,
        reachable: bool = True,
    ) -> None:
        self._history = history
        self._fixtures = tuple(fixtures)
        self._name = name
        self._capabilities = capabilities or frozenset(
            {Capability.RESULTS, Capability.FIXTURES}
        )
        self._reachable = reachable

    @property
    def name(self) -> str:
        return self._name

    @property
    def upstream(self) -> str:
        return self._name

    @property
    def capabilities(self) -> frozenset[Capability]:
        return self._capabilities

    def competitions(self) -> Sequence[str]:
        return ("it.1",)

    def probe(self) -> ProviderStatus:
        return ProviderStatus(
            provider=self._name,
            reachability=Reachability.OK if self._reachable else Reachability.BLOCKED,
            capabilities=self._capabilities,
            checked_at=utcnow(),
            detail="" if self._reachable else "source indisponible (test)",
        )

    def results(self, competition: str = "it.1", season: str = "") -> ResultSet:
        _ = (competition, season)  # the stub serves one fixed dataset
        return ResultSet(matches=self._history)

    def season(self, competition: str, season: str) -> SeasonData:
        return SeasonData(
            competition=competition, season=season, label=_COMP,
            played=self._history, fixtures=self._fixtures,
            retrieved_at=utcnow(), url="stub://controlled",
        )


def _engine(provider: StubProvider | None = None, **config: object) -> Engine:
    fixtures = [
        Fixture("Club A", "Club B", dt.date(2026, 9, 14), competition=_COMP),
        Fixture("Club C", "Club D", dt.date(2026, 9, 14), competition=_COMP),
    ]
    stub = provider or StubProvider(_controlled_history(), fixtures)
    return Engine(
        Registry([stub]),
        config=EngineConfig(seasons=("2026-27",), min_matches=40, **config),  # type: ignore[arg-type]
    )


# --------------------------------------------------------------------------- #
# 1. Every requested match appears in the result
# --------------------------------------------------------------------------- #


def test_every_typed_line_reaches_the_summary_table() -> None:
    """The operator's hardest rule: nothing may be dropped for being awkward."""
    text = "\n".join(
        [
            "Club A - Club B",
            "Club C vs Club D",
            "Club E contre Club F",
            "Club Z - Club Y",          # unknown teams
            "pas un match du tout",      # unparseable
            "Club A - Club A",           # a team cannot play itself
        ]
    )
    run = _engine().run(text, as_of=AS_OF)
    assert run.requested == 6, "une ligne saisie a disparu"
    assert len(run.analyses) == 6
    rendered = render_summary(run)
    for line_number in range(1, 7):
        assert any(a.resolved.request.line_number == line_number for a in run.analyses)
    assert "Contrôle : chaque ligne saisie apparaît bien ci-dessus." in rendered
    for analysis in run.analyses:
        assert analysis.headline(), "une rencontre sans verdict lisible"


def test_unidentified_matches_say_exactly_what_is_missing() -> None:
    run = _engine().run("Club Z - Club Y\npas un match\n", as_of=AS_OF)
    unknown, unparsed = run.analyses
    assert unknown.resolved.status is RequestStatus.UNKNOWN_TEAM
    assert "introuvables" in unknown.resolved.explain()
    assert unparsed.resolved.status is RequestStatus.UNPARSED
    assert "séparées par" in unparsed.resolved.explain()
    assert "Club Z" in render_card(unknown)


# --------------------------------------------------------------------------- #
# 2. Ambiguity, competitions and timezones
# --------------------------------------------------------------------------- #


def test_ambiguous_short_names_are_refused_with_candidates() -> None:
    """A short name matching several clubs must not be guessed."""
    history = _controlled_history()
    extra = MatchLog(
        list(history)
        + [
            Match("Racing Santander", "Racing Besancon", day, Score(1, 1), competition=_COMP)
            for day in (dt.date(2026, 8, 1), dt.date(2026, 8, 8))
        ]
    )
    run = _engine(StubProvider(extra)).run("Racing - Club B", as_of=AS_OF)
    analysis = run.analyses[0]
    assert analysis.resolved.status is RequestStatus.AMBIGUOUS
    assert len(analysis.resolved.candidates) >= 2
    assert "Précisez" in analysis.resolved.explain() or "précisez" in analysis.resolved.explain()


def test_unknown_competition_is_named_not_silently_ignored() -> None:
    run = _engine().run("Ligue Martienne: Club A - Club B", as_of=AS_OF)
    analysis = run.analyses[0]
    assert analysis.resolved.status is RequestStatus.UNKNOWN_COMPETITION
    assert "Ligue Martienne" in analysis.resolved.explain()


def test_timezone_is_honoured_and_a_bad_one_fails_loudly() -> None:
    run = _engine().run("Club A - Club B 14/09/2026 20:45", as_of=AS_OF, timezone="Europe/London")
    assert run.timezone == "Europe/London"
    local = run.analyses[0].resolved.kickoff_local()
    assert "BST" in local or "GMT" in local, local
    with assert_raises(ValueError, match="fuseau"):
        _engine().run("Club A - Club B", as_of=AS_OF, timezone="Mars/Olympus")


# --------------------------------------------------------------------------- #
# 3. Started, postponed and cancelled matches
# --------------------------------------------------------------------------- #


def test_a_started_match_gets_no_prematch_recommendation() -> None:
    """A pre-match read must never be dressed up as a live one."""
    # Club E vs Club F has no scheduled fixture, so the typed date is used as is.
    run = _engine().run("Club E - Club F 13/09/2026 10:00", as_of=AS_OF)
    analysis = run.analyses[0]
    assert analysis.resolved.kickoff_status in (
        KickoffStatus.IN_PLAY, KickoffStatus.FINISHED
    )
    assert not analysis.resolved.bettable
    assert analysis.decision is None
    assert "prématch" in analysis.blocked_reason
    assert analysis in run.started_matches()
    assert "hors prématch" in render_summary(run)


def test_kickoff_states_cover_postponed_and_cancelled() -> None:
    kickoff = dt.datetime(2026, 9, 14, 20, 45, tzinfo=PARIS)
    assert kickoff_status(kickoff, AS_OF) is KickoffStatus.SCHEDULED
    for declared in (KickoffStatus.POSTPONED, KickoffStatus.CANCELLED):
        assert kickoff_status(kickoff, AS_OF, declared=declared) is declared
        assert not declared.allows_prematch


# --------------------------------------------------------------------------- #
# 4. Missing sources are never filled in
# --------------------------------------------------------------------------- #


def test_an_unreachable_source_blocks_its_rubrics_without_inventing_data() -> None:
    run = _engine().run("Club A - Club B 14/09/2026 20:45", as_of=AS_OF)
    analysis = run.analyses[0]
    assert analysis.analysed
    unavailable = [a for a in analysis.rubrics if a.blocker]
    assert unavailable, "aucune rubrique marquée indisponible alors que xG et compositions manquent"
    for assessment in unavailable:
        assert "aucun fournisseur accessible" in assessment.blocker
        assert not assessment.summary, "une rubrique bloquée ne doit rien affirmer"
    card = render_card(analysis)
    assert "aucune valeur n'a été substituée" in card


def test_capabilities_without_a_provider_are_reported() -> None:
    run = _engine().run("Club A - Club B 14/09/2026 20:45", as_of=AS_OF)
    missing = run.registry_report.missing_capabilities()
    assert Capability.ODDS in missing
    assert Capability.ADVANCED_STATS in missing
    assert "sans fournisseur accessible" in render_summary(run)


# --------------------------------------------------------------------------- #
# 5. Contradictions between sources stay visible
# --------------------------------------------------------------------------- #


def test_two_sites_republishing_one_feed_are_one_confirmation() -> None:
    now = utcnow()
    ledger = Ledger()
    for site in ("SiteA", "SiteB", "SiteC"):
        ledger.add(Evidence("forme::Club A", "WWDLW", Source(site, "feed-unique"), now))
    assert ledger.independent_sources("forme::Club A") == 1, "un écho compté comme confirmation"
    ledger.add(Evidence("forme::Club A", "WWDLW", Source("Autre", "feed-distinct"), now))
    assert ledger.independent_sources("forme::Club A") == 2


def test_a_contradiction_is_surfaced_not_averaged() -> None:
    now = utcnow()
    ledger = Ledger()
    ledger.add(Evidence("gardien", "Martin", Source("A", "feed-a"), now))
    ledger.add(Evidence("gardien", "Dupont", Source("B", "feed-b"), now))
    contradictions = ledger.contradictions()
    assert "gardien" in contradictions
    assert len(contradictions["gardien"]) == 2
    assert "contradictions" in ledger.summary()


def test_absence_from_a_list_is_not_proof_of_availability() -> None:
    now = utcnow()
    ledger = Ledger()
    ledger.add(
        Evidence("blessure::Club A", None, Source("A", "feed-a"), now, Confidence.UNAVAILABLE)
    )
    assert ledger.resolve("blessure::Club A") is None
    assert "blessure::Club A" in ledger.unavailable_keys()
    with assert_raises(ValueError, match="unavailable"):
        Evidence("x", "une valeur", Source("A", "a"), now, Confidence.UNAVAILABLE)


# --------------------------------------------------------------------------- #
# 6. Probable versus official lineups, and re-evaluation
# --------------------------------------------------------------------------- #


def test_lineup_plan_is_outstanding_until_something_records_a_check() -> None:
    """An announced automatic check must be backed by a live mechanism."""
    run = _engine().run("Club A - Club B 14/09/2026 20:45", as_of=AS_OF)
    plan = run.analyses[0].lineup_plan
    assert plan is not None and plan.kickoff is not None
    assert plan.first_check == plan.kickoff - dt.timedelta(minutes=75)
    assert plan.second_check == plan.kickoff - dt.timedelta(minutes=60)
    assert not plan.automatic
    assert "À FAIRE" in plan.state()
    assert "AUCUN automatisme actif" in plan.render()


def test_an_official_lineup_supersedes_a_probable_one_and_revises_the_dossier() -> None:
    run = _engine().run("Club A - Club B 14/09/2026 20:45", as_of=AS_OF)
    analysis = run.analyses[0]
    plan = analysis.lineup_plan
    sealed = analysis.sealed
    assert plan is not None and sealed is not None

    probable = LineupObservation(utcnow(), Confidence.PROBABLE, "presse", goalkeeper="Martin")
    plan.record(probable, impact=LineupImpact.MAINTAINED, reason="conforme aux attentes")
    assert [o.official for o in plan.observations] == [False]

    official = LineupObservation(
        utcnow(), Confidence.CONFIRMED, "club (officiel)", goalkeeper="Doublure",
        absences=("titulaire",),
    )
    plan.record(official, impact=LineupImpact.DEGRADED, reason="gardien titulaire absent")
    assert [o.official for o in plan.observations] == [False, True]
    assert plan.has_official
    recorded = plan.impact
    assert recorded is LineupImpact.DEGRADED
    # A degraded lineup must force a re-evaluation, not merely a footnote.
    assert LineupImpact.DEGRADED.requires_reprice
    assert not LineupImpact.MAINTAINED.requires_reprice

    revised = sealed.revise(sealed.dossier, reason="composition officielle")
    assert revised.supersedes == sealed.data_fingerprint
    assert revised.reason == "composition officielle"
    with assert_raises(ValueError):
        sealed.revise(sealed.dossier, reason="  ")


# --------------------------------------------------------------------------- #
# 7. Synthetic data cannot be used silently in real mode
# --------------------------------------------------------------------------- #


def test_synthetic_data_is_refused_in_real_mode_and_allowed_only_explicitly() -> None:
    synthetic, _ = synthetic_league(seed=3, seasons=2)
    fixtures = (Fixture("FC Alpha", "FC Bravo", dt.date(2026, 9, 14)),)
    provider = StubProvider(synthetic, fixtures)

    refused = _engine(provider).run("FC Alpha - FC Bravo 14/09/2026 20:45", as_of=AS_OF)
    analysis = refused.analyses[0]
    assert not analysis.analysed
    assert "synthétiques" in analysis.blocked_reason
    assert SYNTHETIC_MARKER in analysis.blocked_reason

    allowed = _engine(provider, allow_synthetic=True).run(
        "FC Alpha - FC Bravo 14/09/2026 20:45", as_of=AS_OF
    )
    assert allowed.analyses[0].analysed, "le mode démonstration explicite doit fonctionner"


# --------------------------------------------------------------------------- #
# 8. The sport phase is blind to prices
# --------------------------------------------------------------------------- #


def test_the_sport_input_admits_no_market_evidence() -> None:
    now = utcnow()
    source = Source("x", "y")
    entries = [
        Evidence("forme::Club A", "WWD", source, now),
        Evidence("cote::Club A vs Club B", "2.10", source, now),
        Evidence("odds::book", "1.90", source, now),
        Evidence("marché::1X2", "ouvert", source, now),
    ]
    sport_input = SportInput(
        fixture=Fixture("Club A", "Club B", dt.date(2026, 9, 14)),
        history=_controlled_history(),
        as_of=AS_OF,
        evidence=tuple(entries),
    )
    assert [e.key for e in sport_input.evidence] == ["forme::Club A"]


def test_the_seal_refuses_a_payload_carrying_a_price() -> None:
    with assert_raises(OddsLeakError, match="marché"):
        assert_odds_free({"parametres": {"attack": 1.0, "B365H": 2.1}}, where="test")
    with assert_raises(OddsLeakError):
        assert_odds_free({"a": [{"bookmaker": "X"}]}, where="test")
    assert_odds_free({"attack": 1.0, "defence": -0.2}, where="test")  # no raise


def test_prices_are_read_only_after_the_dossier_is_sealed() -> None:
    run = _engine().run(
        "Club A - Club B 14/09/2026 20:45 @ 2.10 3.40 3.60", as_of=AS_OF, bookmaker="Test"
    )
    analysis = run.analyses[0]
    assert analysis.exposure is not None and analysis.sealed is not None
    assert not analysis.exposure.leaked
    assert "séparation respectée" in analysis.exposure.verdict()
    assert analysis.exposure.first_odds_access is not None
    assert analysis.exposure.first_odds_access >= analysis.sealed.sealed_at


def test_the_sealed_fingerprint_is_stable_and_changes_with_the_dossier() -> None:
    first = _engine().run("Club A - Club B 14/09/2026 20:45", as_of=AS_OF).analyses[0]
    again = _engine().run("Club A - Club B 14/09/2026 20:45", as_of=AS_OF).analyses[0]
    assert first.sealed is not None and again.sealed is not None
    assert first.sealed.data_fingerprint == again.sealed.data_fingerprint
    later = _engine().run(
        "Club A - Club B 14/09/2026 20:45",
        as_of=AS_OF + dt.timedelta(days=1),
    ).analyses[0]
    assert later.sealed is not None
    assert later.sealed.data_fingerprint != first.sealed.data_fingerprint


# --------------------------------------------------------------------------- #
# 9. No temporal leakage
# --------------------------------------------------------------------------- #


def test_the_sport_input_rejects_a_history_that_postdates_as_of() -> None:
    future = MatchLog(
        [Match("Club A", "Club B", dt.date(2026, 12, 1), Score(1, 0), competition=_COMP)]
    )
    with assert_raises(ValueError, match="fuite temporelle"):
        SportInput(
            fixture=Fixture("Club A", "Club B", dt.date(2026, 9, 14)),
            history=future,
            as_of=AS_OF,
        )


def test_the_engine_truncates_history_at_as_of() -> None:
    history = _controlled_history(start=dt.date(2025, 8, 1), rounds=40)
    provider = StubProvider(history, (Fixture("Club A", "Club B", dt.date(2026, 9, 14)),))
    run = _engine(provider).run("Club A - Club B 14/09/2026 20:45", as_of=AS_OF)
    analysis = run.analyses[0]
    assert analysis.sealed is not None
    span_end = analysis.sealed.dossier.history_span.split("→")[-1].strip()
    assert dt.date.fromisoformat(span_end) <= AS_OF.date()


def test_chronological_folds_refuse_training_beyond_the_cutoff() -> None:
    history = _controlled_history(rounds=60)
    folds = chronological_folds(history, folds=3, min_train=100)
    assert folds
    for fold in folds:
        assert all(m.date < fold.cutoff for m in fold.train)
        assert all(m.date >= fold.cutoff for m in fold.test)
    with assert_raises(ValueError, match="fuite temporelle"):
        ChronoFold(index=0, cutoff=dt.date(2025, 9, 1), train=history, test=history)


# --------------------------------------------------------------------------- #
# 10. Declared variables really move the forecast
# --------------------------------------------------------------------------- #


def test_findings_separate_what_moves_the_model_from_what_is_only_shown() -> None:
    run = _engine().run("Club A - Club B 14/09/2026 20:45", as_of=AS_OF)
    dossier = run.analyses[0].sealed.dossier  # type: ignore[union-attr]
    used = dossier.model_findings()
    shown = dossier.display_only_findings()
    assert used, "aucune information déclarée comme utilisée"
    assert shown, "le repos devrait être affiché sans être utilisé"
    for finding in used:
        assert finding.model_variable and finding.effect
    for finding in shown:
        assert finding.model_variable is None
        assert "affiché seulement" in (finding.effect or "")


def test_a_declared_variable_actually_changes_the_forecast() -> None:
    """Home advantage is announced as a model variable, so removing it must move the number."""
    history = _controlled_history(rounds=30)
    fixture = Fixture("Club A", "Club B", dt.date(2026, 9, 14))
    model = DixonColesModel(half_life_days=240).fit(history)
    home_rate, away_rate = model.rates(fixture)
    neutral_home, neutral_away = model.parameters.rates(
        fixture.home, fixture.away, neutral=True
    )
    assert abs(home_rate - neutral_home) > 1e-6, "l'avantage du terrain ne change rien"
    assert_close(away_rate, neutral_away, tolerance=1e-12, label="taux extérieur inchangé")


# --------------------------------------------------------------------------- #
# 13. The interface reaches the final report
# --------------------------------------------------------------------------- #


def test_the_web_interface_renders_a_full_report() -> None:
    run = _engine().run(
        "Club A - Club B 14/09/2026 20:45 @ 2.10 3.40 3.60\nClub Z - Club Y",
        as_of=AS_OF, bookmaker="Test",
    )
    body = render_run(run, budget=100.0, combine=True)
    assert "Récapitulatif" in body
    assert "Club A" in body and "Club Z" in body
    assert "Mises" in body and "Combiné" in body
    page = build_page(matchs="Club A - Club B", body=body)
    assert page.startswith("<!doctype html>")
    assert 'lang="fr"' in page
    assert "Analyser" in page
    assert "<script" not in page.lower(), "la page ne doit pas exécuter de script"


def test_the_cli_runs_the_whole_journey() -> None:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = main(["rubriques"])
    assert code == 0
    output = buffer.getvalue()
    assert "R01" in output and "R22" in output
    assert "22 RUBRIQUES" in output
