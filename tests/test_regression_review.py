"""Régressions issues de la revue indépendante du commit 8813955.

Chaque test de ce fichier **échoue sur la version auditée** et documente un
défaut reproduit sur données de contrôle.  Les noms suivent la numérotation de
la revue pour que la correspondance soit immédiate.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import math
import sys
import tempfile
import unittest
from collections.abc import Sequence
from pathlib import Path
from zoneinfo import ZoneInfo

from test_acceptance import _COMP, StubProvider, _controlled_history

from foot.analysis.dossier import build_sport_input
from foot.analysis.engine import Engine, EngineConfig
from foot.analysis.request import KickoffStatus, parse_line
from foot.analysis.rubrics import RUBRICS, RubricImplementation
from foot.cli import build_parser
from foot.collect.base import (
    Capability,
    CollectionError,
    ProviderStatus,
    Reachability,
    SeasonSource,
)
from foot.collect.footballdata import FootballDataProvider
from foot.collect.manual import ManualProvider
from foot.collect.registry import Registry
from foot.collect.supplements import load_supplements
from foot.data.csv_source import write_matches
from foot.domain import Fixture, Match, MatchLog, Score
from foot.markets.catalogue import standard_catalogue
from foot.markets.pricing import price_catalogue
from foot.markets.selection import (
    Confidence,
    DecisionStatus,
    RankingCriteria,
    ScenarioKind,
    select_best,
)
from foot.markets.settlement import SettlementProfile
from foot.models.base import ScoreMatrix
from foot.report.card import render_card
from foot.validation.chrono import AblationResult, chronological_folds, validate

from support import assert_close

PARIS = ZoneInfo("Europe/Paris")
AS_OF = dt.datetime(2026, 9, 13, 12, 0, tzinfo=PARIS)


def _fixtures() -> list[Fixture]:
    return [
        Fixture("Club A", "Club B", dt.date(2026, 9, 14), competition=_COMP),
        Fixture("Club C", "Club D", dt.date(2026, 9, 15), competition=_COMP),
    ]


def _engine(provider: object | None = None, **config: object) -> Engine:
    stub = provider or StubProvider(_controlled_history(), _fixtures())
    return Engine(
        Registry([stub]),  # type: ignore[list-item]
        config=EngineConfig(seasons=("2026-27",), min_matches=40, **config),  # type: ignore[arg-type]
    )


# =========================================================================== #
# 1 — Temporalité
# =========================================================================== #


def test_r1a_a_past_dated_match_absent_from_the_calendar_is_never_recommended() -> None:
    """Une rencontre datée d'hier, sans horaire, ne doit pas recevoir « recommandé ».

    Reproduit sur la version auditée : Club C – Club D daté du 12/09, analysé le
    13/09 à 12 h, sans horaire et sans rencontre correspondante au calendrier,
    recevait « Match nul (N) @ 3.40 ».
    """
    run = _engine().run(
        "Club C - Club D 12/09/2026 @ 2.10 3.40 3.60", as_of=AS_OF, bookmaker="T"
    )
    analysis = run.analyses[0]
    assert not analysis.resolved.bettable, (
        "une date antérieure à as_of ne peut pas être un prématch"
    )
    assert analysis.decision is None or not analysis.decision.has_bet
    assert analysis.blocked_reason, "le motif du blocage doit être écrit"


def test_r1a_an_unverified_fixture_blocks_the_prematch_recommendation() -> None:
    """Sans rencontre au calendrier, aucune recommandation prématch.

    La paire d'équipes peut exister sans qu'une rencontre soit programmée ; le
    système doit le dire plutôt que d'analyser une rencontre qu'il n'a pas
    vérifiée.
    """
    run = _engine().run(
        "Club E - Club F 20/09/2026 20:45 @ 2.10 3.40 3.60", as_of=AS_OF, bookmaker="T"
    )
    analysis = run.analyses[0]
    assert analysis.resolved.kickoff_status is KickoffStatus.UNVERIFIED
    assert not analysis.resolved.bettable
    assert analysis.decision is None or not analysis.decision.has_bet
    assert "calendrier" in analysis.blocked_reason.lower()


def test_r1b_same_day_results_are_excluded_without_sufficient_timing() -> None:
    """À minuit, les résultats du jour ne sont pas encore connus.

    ``build_sport_input`` retenait les scores finaux du jour même dès que
    ``as_of`` tombait sur cette date, quelle que soit l'heure.
    """
    today = dt.date(2026, 9, 13)
    history = MatchLog(
        [
            Match("A", "B", dt.date(2026, 9, 12), Score(1, 0), competition="X"),
            Match("C", "D", today, Score(2, 1), competition="X"),
        ]
    )
    fixture = Fixture("A", "C", dt.date(2026, 9, 14))

    midnight = build_sport_input(
        fixture, history, dt.datetime(2026, 9, 13, 0, 0, tzinfo=PARIS)
    )
    assert len(midnight.history) == 1, (
        "un résultat du jour ne peut pas être connu à minuit"
    )
    assert midnight.history[0].date == dt.date(2026, 9, 12)

    # Le lendemain, le résultat est disponible.
    tomorrow = build_sport_input(
        fixture, history, dt.datetime(2026, 9, 14, 0, 0, tzinfo=PARIS)
    )
    assert len(tomorrow.history) == 2


def test_r1b_availability_dates_are_carried_on_the_sport_input() -> None:
    """La coupure doit s'appuyer sur une date de disponibilité, pas sur la date du match."""
    history = MatchLog(
        [Match("A", "B", dt.date(2026, 9, 12), Score(1, 0), competition="X")]
    )
    sport_input = build_sport_input(
        Fixture("A", "C", dt.date(2026, 9, 14)), history,
        dt.datetime(2026, 9, 13, 12, 0, tzinfo=PARIS),
    )
    assert sport_input.knowledge_cutoff is not None
    assert sport_input.knowledge_cutoff <= sport_input.as_of
    assert "disponibilité" in sport_input.cutoff_rule.lower()


# =========================================================================== #
# 2 — Fournisseurs de secours et imports
# =========================================================================== #


def test_r2a_fallback_providers_satisfy_the_season_source_contract() -> None:
    """ManualProvider et FootballDataProvider doivent pouvoir alimenter le moteur."""
    for provider in (
        ManualProvider(results=_controlled_history(), label="manuel"),
        FootballDataProvider(),
    ):
        assert isinstance(provider, SeasonSource), (
            f"{provider.name} ne satisfait pas SeasonSource : "
            f"le moteur ne peut pas l'utiliser"
        )
        assert hasattr(provider, "season")


def test_r2b_the_engine_falls_back_when_the_first_provider_fails() -> None:
    """Un fournisseur en panne ne doit pas condamner l'analyse."""

    class BrokenProvider:
        name = "en panne"
        upstream = "en panne"
        capabilities = frozenset({Capability.RESULTS, Capability.FIXTURES})

        def competitions(self) -> Sequence[str]:
            return ("it.1",)

        def probe(self) -> ProviderStatus:
            return ProviderStatus(
                provider=self.name, reachability=Reachability.OK,
                capabilities=self.capabilities,
                checked_at=dt.datetime.now(dt.timezone.utc),
            )

        def season(self, competition: str, season: str) -> object:
            _ = (competition, season)
            raise CollectionError("panne simulée")

    working = StubProvider(_controlled_history(), _fixtures())
    engine = Engine(
        Registry([BrokenProvider(), working]),  # type: ignore[list-item]
        config=EngineConfig(seasons=("2026-27",), min_matches=40),
    )
    run = engine.run("Club A - Club B 14/09/2026 20:45", as_of=AS_OF)
    analysis = run.analyses[0]
    assert analysis.analysed, "le moteur n'a pas basculé sur le second fournisseur"
    assert analysis.sealed is not None
    assert analysis.sealed.dossier.history_size > 0


def test_r2c_csv_import_feeds_the_main_journey() -> None:
    """--resultats-csv doit réellement alimenter le parcours principal."""
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "resultats.csv"
        history = _controlled_history()
        write_matches(path, history)

        provider = ManualProvider(
            results_csv=path,
            fixtures=_fixtures(),
            competition_key="manuel",
            label="import manuel",
        )
        engine = Engine(
            Registry([provider]),
            config=EngineConfig(seasons=("",), min_matches=40),
        )
        run = engine.run("manuel | Club A - Club B | 14/09/2026 20:45", as_of=AS_OF)
        analysis = run.analyses[0]
        assert analysis.analysed, "les données CSV n'ont pas atteint le moteur"
        assert analysis.sealed is not None
        assert analysis.sealed.dossier.history_size == len(history)
        # La provenance doit nommer l'import manuel.
        keys = [e.source.provider for e in analysis.ledger]
        assert any("opérateur" in k for k in keys), keys


# =========================================================================== #
# 3 — Cotes de tous les marchés
# =========================================================================== #


def test_r3a_odds_for_every_family_reach_the_selector() -> None:
    """Le parcours doit accepter les cotes BTTS, totaux, DC, DNB et handicaps."""
    saisie = (
        "Club A - Club B 14/09/2026 20:45 "
        "@ 2.10 3.40 3.60 "
        "| BTTS:oui=1.85 | TOTAL:+2.5=1.95 | TOTAL:-2.5=1.90 "
        "| DC:1N=1.30 | DNB:1=1.62 | AH:H:-0.5=2.05"
    )
    run = _engine().run(saisie, as_of=AS_OF, bookmaker="Test")
    analysis = run.analyses[0]
    assert analysis.analysed
    priced = [p for p in analysis.priced if p.has_price]
    families = {p.offer.family.value for p in priced}
    assert len(priced) >= 9, f"seules {len(priced)} offres cotées : {[p.offer.key for p in priced]}"
    for expected in ("1-N-2", "les deux marquent", "total de buts",
                     "double chance", "remboursé si nul", "handicap asiatique"):
        assert expected in families, f"famille absente : {expected} (présentes : {families})"


def test_r3b_the_recommendation_names_the_markets_actually_compared() -> None:
    """La fiche doit dire quels marchés cotés ont réellement été mis en concurrence."""
    run = _engine().run(
        "Club A - Club B 14/09/2026 20:45 @ 2.10 3.40 3.60 | BTTS:oui=1.85 | TOTAL:+2.5=1.95",
        as_of=AS_OF, bookmaker="Test",
    )
    analysis = run.analyses[0]
    decision = analysis.decision
    assert decision is not None
    assert decision.compared, "la décision ne liste pas les marchés comparés"
    assert len(decision.compared) >= 5
    card = render_card(analysis)
    assert "marchés cotés comparés" in card.lower()


def test_r3c_quoted_odds_carry_line_rule_bookmaker_and_timestamp() -> None:
    quoted_at = AS_OF - dt.timedelta(hours=2)
    run = _engine().run(
        "Club A - Club B 14/09/2026 20:45 | TOTAL:+2.5=1.95",
        as_of=AS_OF, bookmaker="Pinnacle", quoted_at=quoted_at,
    )
    analysis = run.analyses[0]
    priced = [p for p in analysis.priced if p.has_price]
    assert priced
    item = priced[0]
    assert item.offer.bookmaker == "Pinnacle"
    assert item.offer.line == 2.5
    assert item.offer.rule
    assert item.quoted_at == quoted_at
    assert item.price_age == AS_OF - quoted_at


def test_r3d_quotes_separated_by_spaces_do_not_contaminate_the_team_name() -> None:
    """Défaut reproduit à la vérification finale : cotes groupées dans un seul champ.

    Le guide montre les cotes 1–N–2 séparées par des espaces (« @ 2.55 3.55 2.65 »),
    donc un utilisateur groupe naturellement aussi les autres marchés dans le même
    champ.  Avant correction, ``_extract_quotes`` n'essayait de lire qu'un jeton par
    segment « | » : le bloc entier restait dans la ligne et finissait **collé au nom
    de l'équipe extérieure**, qui devenait introuvable.  Une saisie valide était
    donc silencieusement transformée en « équipe inconnue ».
    """
    saisie = (
        "Club A - Club B 14/09/2026 20:45 "
        "1=2.10 N=3.40 2=3.60 BTTS:oui=1.85 TOTAL:+2.5=1.95 DC:1N=1.30 AH:H:-0.5=2.05"
    )
    request = parse_line(saisie, 1, today=AS_OF.date())
    assert request.away_text == "Club B", f"nom d'équipe contaminé : {request.away_text!r}"
    assert request.home_text == "Club A"
    assert len(request.quotes) == 7, f"cotes lues : {sorted(request.quotes)}"

    run = _engine().run(saisie, as_of=AS_OF, bookmaker="Test")
    analysis = run.analyses[0]
    assert analysis.analysed, f"statut : {analysis.resolved.explain()}"
    priced = [p for p in analysis.priced if p.has_price]
    families = {p.offer.family.value for p in priced}
    for expected in ("1-N-2", "les deux marquent", "total de buts",
                     "double chance", "handicap asiatique"):
        assert expected in families, f"famille absente : {expected} (présentes : {families})"


def test_r3e_a_pipe_field_may_mix_quotes_and_text() -> None:
    """Les deux écritures documentées coexistent, y compris mélangées."""
    request = parse_line(
        "it.1 | Napoli - Bologna | 13/09/2026 20:45 | 1=1.62 N=4.00 2=5.50 | BTTS:oui=1.85",
        1, today=dt.date(2026, 9, 13),
    )
    assert request.home_text == "Napoli"
    assert request.away_text == "Bologna"
    assert request.competition_text == "it.1"
    assert request.time == dt.time(20, 45)
    assert len(request.quotes) == 4, f"cotes lues : {sorted(request.quotes)}"


def test_r3f_a_refused_market_is_reported_not_silently_dropped() -> None:
    """Corners, cartons et buteurs : refus explicite, jamais une estimation déduite du score.

    Le jeton doit quitter la ligne — collé au nom de l'équipe, il la rendrait
    introuvable — mais le refus doit apparaître dans la fiche.
    """
    request = parse_line(
        "Club A - Club B 14/09/2026 20:45 CORNERS:+9.5=1.90", 1, today=AS_OF.date()
    )
    assert request.quotes == {}, "les corners n'ont pas de modèle : aucun prix retenu"
    assert request.away_text == "Club B", "le jeton refusé ne doit pas contaminer le nom d'équipe"
    assert request.refused_markets == ("CORNERS:+9.5",)

    run = _engine().run(
        "Club A - Club B 14/09/2026 20:45 1=2.10 N=3.40 2=3.60 CORNERS:+9.5=1.90",
        as_of=AS_OF, bookmaker="Test",
    )
    analysis = run.analyses[0]
    assert analysis.analysed
    assert not any("corner" in p.offer.key.lower() for p in analysis.priced)
    card = render_card(analysis)
    assert "CORNERS:+9.5" in card, "le marché refusé doit être nommé dans la fiche"
    assert "modèle dédié" in card.lower()


# =========================================================================== #
# 4 — Validation
# =========================================================================== #


def _control_set(count: int = 180) -> MatchLog:
    day = dt.date(2025, 1, 6)
    matches: list[Match] = []
    for index in range(count):
        matches.append(
            Match(f"T{index % 20}", f"T{(index + 7) % 20}", day, Score(1, 0), competition="C")
        )
        if index % 5 == 4:
            day += dt.timedelta(days=7)
    return MatchLog(matches)


def test_r4a_validation_windows_are_disjoint() -> None:
    """180 rencontres distinctes ne peuvent pas produire plus de 180 évaluations."""
    log = _control_set(180)
    folds = chronological_folds(log, folds=4, min_train=60)
    seen: dict[tuple[dt.date, str, str], int] = {}
    for fold in folds:
        for match in fold.test:
            key = (match.date, match.home, match.away)
            seen[key] = seen.get(key, 0) + 1
    duplicates = {k: v for k, v in seen.items() if v > 1}
    assert not duplicates, (
        f"{sum(v - 1 for v in duplicates.values())} rencontres évaluées plusieurs fois"
    )
    total = sum(len(fold.test) for fold in folds)
    assert total == len(seen)
    assert total <= len(log)


def test_r4b_ablation_isolates_a_single_component() -> None:
    """Chaque ablation ne doit faire varier qu'un composant."""
    report = validate(_control_set(300), folds=3, min_train=100, tune=False)
    assert report.ablations
    for ablation in report.ablations:
        assert ablation.varied, f"{ablation.component} ne déclare pas ce qui varie"
        assert len(ablation.varied) == 1, (
            f"{ablation.component} fait varier {ablation.varied} : ce n'est pas une ablation"
        )


def test_r4c_a_positive_gain_alone_does_not_confirm_a_contribution() -> None:
    """Le verdict doit reposer sur l'incertitude de la différence, pas sur son signe."""
    tiny = AblationResult(
        component="composant marginal", varied=("rho",),
        rps_with=0.21000, rps_without=0.21002,
        difference_interval=(-0.0015, 0.0015), paired_differences=(0.0,) * 50,
    )
    assert tiny.gain > 0.0
    assert not tiny.confirmed, "un gain positif dont l'IC contient zéro n'est pas confirmé"
    assert "NON CONFIRMÉ" in tiny.render()

    solid = AblationResult(
        component="composant réel", varied=("modèle",),
        rps_with=0.21000, rps_without=0.22900,
        difference_interval=(0.012, 0.026), paired_differences=(0.019,) * 50,
    )
    assert solid.confirmed
    assert "confirmé" in solid.render()


# =========================================================================== #
# 5 — Cohérence de la décision
# =========================================================================== #


def test_r5a_a_non_converged_model_is_never_recommended() -> None:
    """« recommandé » avec confiance D et modèle non convergé est contradictoire."""
    matrix = ScoreMatrix.from_rates(1.6, 1.1)
    priced = price_catalogue(
        standard_catalogue(), matrix,
        quotes={"1X2:H": 2.60, "1X2:D": 3.60, "1X2:A": 4.60},
    )
    decision = select_best(
        priced, criteria=RankingCriteria(), model_converged=False, rubric_coverage=0.0
    )
    assert decision.status is not DecisionStatus.RECOMMENDED
    assert not decision.has_bet
    assert "converg" in decision.reason.lower()

    graded = select_best(
        priced, criteria=RankingCriteria(), model_converged=True, rubric_coverage=0.0
    )
    if graded.status is DecisionStatus.RECOMMENDED:
        assert graded.confidence is not Confidence.D, (
            "une confiance D signifie « information insuffisante pour engager »"
        )


def test_r5b_scenarios_are_typed_and_built_from_available_information() -> None:
    """Sensibilité, incertitude du modèle et événements sportifs sont distincts."""
    run = _engine().run("Club A - Club B 14/09/2026 20:45 @ 2.10 3.40 3.60",
                        as_of=AS_OF, bookmaker="T")
    analysis = run.analyses[0]
    kinds = {scenario.kind for scenario in analysis.scenarios}
    assert ScenarioKind.SENSITIVITY in kinds
    assert ScenarioKind.MODEL_UNCERTAINTY in kinds, (
        "l'incertitude d'estimation doit être un scénario à part entière"
    )
    for scenario in analysis.scenarios:
        assert scenario.basis, f"{scenario.name} ne dit pas sur quoi il repose"
    # Un scénario sportif documenté n'est produit que si une source l'étaye.
    documented = [s for s in analysis.scenarios if s.kind is ScenarioKind.SPORTING_EVENT]
    for scenario in documented:
        assert scenario.evidence_keys, "un événement sportif doit citer sa source"


def test_r5c_the_report_separates_sensitivity_from_worst_case() -> None:
    run = _engine().run("Club A - Club B 14/09/2026 20:45 @ 2.10 3.40 3.60",
                        as_of=AS_OF, bookmaker="T")
    card = render_card(run.analyses[0])
    assert "sensibilité" in card.lower()
    assert "pire scénario sportif" not in card.lower(), (
        "des variations fixes ne démontrent pas un pire scénario sportif"
    )


# =========================================================================== #
# 6 — Seuils de cote sur marchés remboursables
# =========================================================================== #


def test_r6_minimum_odds_solve_the_exact_settlement_profile() -> None:
    """L'exemple exact de la revue : 40 % / 35 % / 25 %, seuil 1,70 et non 1,67."""
    profile = SettlementProfile.from_units({1.0: 0.40, 0.0: 0.35, -1.0: 0.25})
    threshold = profile.odds_for_expected_value(0.03)
    assert_close(threshold, 1.70, tolerance=1e-9, label="seuil exigeant 3 % d'espérance")
    assert_close(
        profile.expected_value(threshold), 0.03, tolerance=1e-12, label="EV au seuil"
    )
    naive = profile.break_even_odds() * 1.03
    assert abs(naive - threshold) > 0.02, "l'ancienne formule doit être nettement fausse"


def test_r6_minimum_odds_handle_half_settlements() -> None:
    profile = SettlementProfile.from_units({1.0: 0.45, -0.5: 0.20, -1.0: 0.35})
    for target in (0.0, 0.02, 0.05, 0.10):
        odds = profile.odds_for_expected_value(target)
        assert_close(
            profile.expected_value(odds), target, tolerance=1e-12,
            label=f"EV visée {target}",
        )
    hopeless = SettlementProfile.from_units({0.0: 0.4, -1.0: 0.6})
    assert math.isinf(hopeless.odds_for_expected_value(0.03))


def test_r6_the_cancellation_condition_uses_the_exact_threshold() -> None:
    matrix = ScoreMatrix.from_rates(1.6, 1.1)
    priced = price_catalogue(
        standard_catalogue(), matrix, quotes={"DNB:H": 2.30, "1X2:H": 2.60, "1X2:D": 3.60}
    )
    criteria = RankingCriteria(min_expected_value=0.03)
    decision = select_best(priced, criteria=criteria)
    if decision.main is not None:
        exact = decision.main.priced.profile.odds_for_expected_value(0.03)
        assert f"{exact:.2f}" in decision.cancellation, (
            f"la condition d'annulation doit citer {exact:.2f} : {decision.cancellation}"
        )


# =========================================================================== #
# 7 — Traçabilité jusqu'au rapport
# =========================================================================== #


def test_r7_sport_evidence_reaches_the_sealed_dossier_and_the_report() -> None:
    """Le dossier ne doit pas être scellé avec un champ evidence vide."""
    run = _engine().run("Club A - Club B 14/09/2026 20:45", as_of=AS_OF)
    analysis = run.analyses[0]
    assert analysis.sealed is not None
    evidence = analysis.sealed.dossier.evidence
    assert evidence, "aucune preuve sportive transmise au dossier"

    keys = {item.key for item in evidence}
    cited = {key for finding in analysis.sealed.dossier.findings for key in finding.evidence_keys}
    assert cited <= keys, f"constats citant des clés absentes du registre : {cited - keys}"

    for item in evidence:
        assert item.retrieved_at.tzinfo is not None
        assert item.source.provider
    assert any(item.source.url for item in evidence), "aucune URL conservée"
    assert any(item.fact_date is not None for item in evidence), "aucune date du fait"

    card = render_card(analysis)
    assert "sources décisives" in card.lower()
    assert any(item.key in card for item in evidence)


def test_r7_the_ledger_is_populated_for_every_analysed_match() -> None:
    run = _engine().run(
        "Club A - Club B 14/09/2026 20:45\nClub C - Club D 15/09/2026 18:00", as_of=AS_OF
    )
    for analysis in run.analyses:
        if analysis.analysed:
            assert len(analysis.ledger) > 0, "registre vide pour une rencontre analysée"


# =========================================================================== #
# 8 — Protocole intégré
# =========================================================================== #


def test_r8a_the_protocol_is_stored_in_the_repository() -> None:
    """Le protocole doit être un fichier du dépôt, pas seulement du code."""
    root = Path(__file__).resolve().parent.parent
    document = root / "protocole" / "protocole-22-rubriques.md"
    data = root / "protocole" / "protocole-22-rubriques.json"
    assert document.exists(), f"protocole absent : {document}"
    assert data.exists(), f"protocole lisible par machine absent : {data}"
    text = document.read_text(encoding="utf-8")
    for number in range(1, 23):
        assert f"R{number:02d}" in text, f"rubrique R{number:02d} absente du document"


def test_r8b_the_loader_is_wired_as_engine_configuration() -> None:
    """Charger un JSON doit changer le comportement du moteur, pas seulement une liste."""
    root = Path(__file__).resolve().parent.parent
    engine = _engine(rubrics_path=root / "protocole" / "protocole-22-rubriques.json")
    run = engine.run("Club A - Club B 14/09/2026 20:45", as_of=AS_OF)
    analysis = run.analyses[0]
    assert len(analysis.rubrics) == 22
    numbers = sorted(a.rubric.number for a in analysis.rubrics)
    assert numbers == list(range(1, 23))


def test_r8c_each_rubric_reports_data_treatment_and_effect() -> None:
    run = _engine().run("Club A - Club B 14/09/2026 20:45", as_of=AS_OF)
    analysis = run.analyses[0]
    for assessment in analysis.rubrics:
        assert assessment.implementation is not None, (
            f"R{assessment.rubric.number:02d} ne déclare pas son état d'implémentation"
        )
        if assessment.status.name in ("COVERED", "PARTIAL"):
            assert assessment.data_used, f"R{assessment.rubric.number:02d} : donnée non nommée"
            assert assessment.treatment, f"R{assessment.rubric.number:02d} : traitement non décrit"
            assert assessment.effect, f"R{assessment.rubric.number:02d} : effet non décrit"


def test_r8d_implementation_states_are_distinguished_precisely() -> None:
    """« non développé », « développé mais inaccessible » et « opérationnel »."""
    run = _engine().run("Club A - Club B 14/09/2026 20:45", as_of=AS_OF)
    states = {a.implementation for a in run.analyses[0].rubrics}
    assert RubricImplementation.OPERATIONAL in states
    assert states <= set(RubricImplementation)
    for assessment in run.analyses[0].rubrics:
        if assessment.implementation is RubricImplementation.BUILT_UNREACHABLE:
            assert assessment.blocker, "un adaptateur inaccessible doit nommer son blocage"
        if assessment.implementation is RubricImplementation.NOT_BUILT:
            assert assessment.blocker


def test_r8g_imported_lineups_feed_the_t75_check_and_its_verdict() -> None:
    """Défaut reproduit à la vérification finale : R10 renseignée, R21 « à faire ».

    Une composition importée alimentait la rubrique « gardien » mais n'atteignait
    jamais le plan T−75/T−60 : la même fiche affichait une composition officielle
    relevée **et** « aucune composition relevée à ce jour ».  Le contrôle annoncé
    doit porter sur les compositions réellement fournies, et rendre un verdict de
    réévaluation motivé.
    """
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "compos.csv").write_text(
            "date,equipe,joueur,poste,titulaire,source,statut\n"
            "14/09/2026,Club A,Dupont,gardien,oui,Club A (officiel),officiel\n"
            "14/09/2026,Club A,Bernard,defenseur,oui,Club A (officiel),officiel\n"
            "14/09/2026,Club B,Leroy,gardien,oui,Club B (officiel),officiel\n",
            encoding="utf-8",
        )
        supplements = load_supplements(lineups_csv=root / "compos.csv")
        run = _engine().run(
            "Club A - Club B 14/09/2026 20:45 @ 2.10 3.40 3.60",
            as_of=AS_OF, bookmaker="T", supplements=supplements,
        )
    analysis = run.analyses[0]
    assert analysis.analysed
    plan = analysis.lineup_plan
    assert plan is not None
    assert plan.observations, "les compositions importées n'atteignent pas le plan T−75"
    assert plan.has_official, "une composition « officiel » doit être vue comme officielle"
    assert plan.impact is not None, "aucun verdict de réévaluation rendu"
    assert plan.impact_reason, "un verdict doit être motivé"
    assert "aucune composition relevée" not in plan.state()

    by_number = {a.rubric.number: a for a in analysis.rubrics}
    assert by_number[21].status.name in ("COVERED", "PARTIAL"), by_number[21].blocker
    assert by_number[10].status.name in ("COVERED", "PARTIAL"), by_number[10].blocker
    card = render_card(analysis)
    assert "Dupont" in card, "le gardien relevé doit apparaître dans la fiche"


def test_r8h_unusable_imported_rows_are_named_as_such() -> None:
    """Une donnée fournie mais inexploitable ne doit pas être annoncée « non fournie ».

    Des lignes xG portant sur des matchs absents de l'historique ne peuvent pas
    être appariées.  Avant correction, la grille répondait « fournissez la donnée
    via --xg-csv » alors qu'elle venait de l'être : le message envoyait l'opérateur
    refaire ce qu'il avait déjà fait, au lieu de dire ce qui clochait.
    """
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "xg.csv").write_text(
            "date,home,away,home_xg,away_xg,source,statut\n"
            "01/02/2019,Equipe Inconnue,Autre Inconnue,1.50,1.10,Understat,probable\n",
            encoding="utf-8",
        )
        supplements = load_supplements(xg_csv=root / "xg.csv")
        run = _engine().run(
            "Club A - Club B 14/09/2026 20:45 @ 2.10 3.40 3.60",
            as_of=AS_OF, bookmaker="T", supplements=supplements,
        )
    analysis = run.analyses[0]
    assert analysis.analysed
    seven = next(a for a in analysis.rubrics if a.rubric.number == 7)
    assert seven.status.name == "UNAVAILABLE"
    blocker = seven.blocker
    assert "1 ligne" in blocker, blocker
    assert "aucune" in blocker.lower(), blocker
    assert "fournissez la donnée" not in blocker, (
        "la donnée a été fournie : le message ne doit pas redemander de la fournir"
    )


def test_r8i_the_counter_analysis_stops_claiming_missing_data_once_supplied() -> None:
    """Défaut reproduit à la vérification finale : contre-analyse figée.

    La fiche affichait « les xG […] sont indisponibles ici » et réclamait « la
    composition officielle » dans la même page où la rubrique R07 exposait un
    ratio buts/xG et où R21 citait une composition officielle.  Le mécanisme
    d'invalidation et la donnée qui trancherait doivent suivre ce qui a
    réellement été fourni, sinon la contre-analyse décrit un autre dossier.
    """
    history = _controlled_history()
    recent = [m for m in history if m.involves("Club A")][-3:]
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "xg.csv").write_text(
            "date,home,away,home_xg,away_xg,source,statut\n"
            + "".join(
                f"{m.date.strftime('%d/%m/%Y')},{m.home},{m.away},1.30,1.10,Opta,probable\n"
                for m in recent
            ),
            encoding="utf-8",
        )
        (root / "compos.csv").write_text(
            "date,equipe,joueur,poste,titulaire,source,statut\n"
            "14/09/2026,Club A,Dupont,gardien,oui,Club A (officiel),officiel\n",
            encoding="utf-8",
        )
        supplements = load_supplements(
            xg_csv=root / "xg.csv", lineups_csv=root / "compos.csv"
        )
        run = _engine().run(
            "Club A - Club B 14/09/2026 20:45 @ 2.10 3.40 3.60",
            as_of=AS_OF, bookmaker="T", supplements=supplements,
        )
        bare = _engine().run(
            "Club A - Club B 14/09/2026 20:45 @ 2.10 3.40 3.60",
            as_of=AS_OF, bookmaker="T",
        )

    supplied_text = " ".join(run.analyses[0].sealed.dossier.counter_analysis)  # type: ignore[union-attr]
    bare_text = " ".join(bare.analyses[0].sealed.dossier.counter_analysis)  # type: ignore[union-attr]
    assert "xG, qui le détecteraient, sont indisponibles" in bare_text, (
        "sans donnée fournie, la contre-analyse doit bien signaler le manque"
    )
    assert "indisponibles" not in supplied_text, supplied_text
    assert "composition officielle" not in supplied_text, supplied_text

    assumptions = " ".join(run.analyses[0].sealed.dossier.assumptions)  # type: ignore[union-attr]
    assert "xG, compositions et absences indisponibles" not in assumptions, assumptions


def test_r7d_a_finding_cites_only_the_evidence_it_actually_rests_on() -> None:
    """Défaut reproduit à la vérification finale : preuves attribuées en bloc.

    Chaque constat recevait la **totalité** des clés de preuve du dossier.  Le
    coefficient d'attaque, calculé sur les seuls résultats, se retrouvait ainsi
    « prouvé » par une feuille de composition et par des lignes xG qui n'entrent
    dans aucune variable.  Une traçabilité qui attribue tout à tout n'en est pas
    une : elle empêche de vérifier ce qui a réellement produit le chiffre.
    """
    history = _controlled_history()
    recent = [m for m in history if m.involves("Club A")][-3:]
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "xg.csv").write_text(
            "date,home,away,home_xg,away_xg,source,statut\n"
            + "".join(
                f"{m.date.strftime('%d/%m/%Y')},{m.home},{m.away},1.30,1.10,Opta,probable\n"
                for m in recent
            ),
            encoding="utf-8",
        )
        (root / "absences.csv").write_text(
            "date,equipe,joueur,poste,motif,source,statut\n"
            "12/09/2026,Club A,Martin,gardien,blessure,Club A (officiel),officiel\n",
            encoding="utf-8",
        )
        supplements = load_supplements(
            xg_csv=root / "xg.csv", absences_csv=root / "absences.csv"
        )
        run = _engine().run(
            "Club A - Club B 14/09/2026 20:45 @ 2.10 3.40 3.60",
            as_of=AS_OF, bookmaker="T", supplements=supplements,
        )
    dossier = run.analyses[0].sealed.dossier  # type: ignore[union-attr]
    by_rubric = {f.rubric: f for f in dossier.findings}

    form = by_rubric[4]
    assert form.evidence_keys, "le constat de forme doit citer ses jeux de données"
    for key in form.evidence_keys:
        assert not key.startswith(("xg::", "absence::", "composition::")), (
            f"la forme ne se calcule pas sur {key}"
        )

    xg_finding = by_rubric[7]
    assert all(k.startswith("xg::") for k in xg_finding.evidence_keys), (
        xg_finding.evidence_keys
    )
    absence_finding = by_rubric[11]
    assert all(k.startswith("absence::") for k in absence_finding.evidence_keys), (
        absence_finding.evidence_keys
    )

    # Every key cited must exist in the sealed ledger, in both directions.
    known = {item.key for item in dossier.evidence}
    for finding in dossier.findings:
        for key in finding.evidence_keys:
            assert key in known, f"clé citée sans preuve au dossier : {key}"


def test_r8j_the_card_keeps_the_three_unavailability_states_apart() -> None:
    """La fiche doit distinguer non développé / développé mais inaccessible / à fournir.

    La section « effectif et contexte » annonçait « faute de source accessible »
    pour toutes les rubriques non renseignées, y compris celles pour lesquelles
    aucun adaptateur n'existe.  C'est précisément la confusion que la revue
    demandait de lever : le lecteur ne peut pas savoir s'il doit ouvrir un accès
    réseau, fournir un fichier, ou attendre un développement.
    """
    run = _engine().run("Club A - Club B 14/09/2026 20:45 @ 2.10 3.40 3.60", as_of=AS_OF)
    analysis = run.analyses[0]
    card = render_card(analysis)
    states = {a.implementation for a in analysis.rubrics if a.status.name == "UNAVAILABLE"}
    assert len(states) >= 2, "le jeu de contrôle doit exercer plusieurs états"
    assert "faute de source accessible" not in card, (
        "un motif unique ne peut pas couvrir trois états distincts"
    )
    for label in ("non développé", "à fournir par l'opérateur"):
        assert label in card, f"état absent de la fiche : {label}"


# =========================================================================== #
# 9 — Transparence des tests
# =========================================================================== #


def test_r9_the_zero_dependency_runner_counts_skips_separately() -> None:
    """Un test ignoré ne doit pas être comptabilisé comme réussi."""
    root = Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location("footrunner", root / "run_tests.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Register before executing: `dataclass` resolves string annotations through
    # `sys.modules[cls.__module__]`, which is absent otherwise.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    assert hasattr(module, "Outcome") or hasattr(module, "run_suite"), (
        "le lanceur doit exposer un décompte structuré"
    )
    summary = module.run_suite(root, patterns=["test_regression_review::test_r9_sample"])
    assert summary.skipped >= 1, "l'ignoré n'est pas compté"
    assert summary.passed == 0
    assert summary.failed == 0
    assert "ignoré" in summary.render().lower()


def test_r9_sample() -> None:
    """Échantillon consommé par le test précédent : il s'ignore toujours."""
    raise unittest.SkipTest("échantillon volontairement ignoré")


def test_r9_live_tests_report_skips_rather_than_silent_passes() -> None:
    """Les tests réseau doivent lever SkipTest, pas imprimer puis retourner."""
    source = (Path(__file__).resolve().parent / "test_live_sources.py").read_text("utf-8")
    assert "SkipTest" in source, "les tests réseau ne signalent pas l'ignoré au lanceur"
    assert 'print(f"IGNORÉ' not in source, (
        "imprimer puis retourner fait compter le test comme réussi"
    )


# =========================================================================== #
# 8 (suite) — les imports opérateur annoncés doivent être réels
# =========================================================================== #


def test_r8e_declared_operator_imports_are_actually_implemented() -> None:
    """Une option d'import annoncée dans la grille doit exister dans la CLI.

    Déclarer ``--xg-csv`` dans le protocole sans l'implémenter serait exactement
    la promesse creuse que la revue reproche.
    """
    parser = build_parser()
    known: set[str] = set()
    for action in parser._subparsers._group_actions[0].choices["analyser"]._actions:  # type: ignore[union-attr,index]
        known.update(action.option_strings)
    for rubric in RUBRICS:
        option = rubric.operator_import.split()[0] if rubric.operator_import else ""
        if option.startswith("--"):
            assert option in known, (
                f"R{rubric.number:02d} annonce {option} mais la CLI ne l'expose pas"
            )


def test_r8f_supplied_context_fills_rubrics_and_documents_scenarios() -> None:
    """xG et absences importés remplissent leurs rubriques et créent un scénario sourcé."""
    history = _controlled_history()
    recent = [m for m in history if m.involves("Club A")][-4:]
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "xg.csv").write_text(
            "date,home,away,home_xg,away_xg,source,statut\n"
            + "".join(
                f"{m.date.strftime('%d/%m/%Y')},{m.home},{m.away},0.70,0.70,Understat,probable\n"
                for m in recent
            ),
            encoding="utf-8",
        )
        (root / "absences.csv").write_text(
            "date,equipe,joueur,poste,motif,source,statut\n"
            "12/09/2026,Club A,Martin,gardien,blessure,Club A (officiel),officiel\n",
            encoding="utf-8",
        )
        supplements = load_supplements(
            xg_csv=root / "xg.csv", absences_csv=root / "absences.csv"
        )
        run = _engine().run(
            "Club A - Club B 14/09/2026 20:45 @ 2.10 3.40 3.60",
            as_of=AS_OF, bookmaker="T", supplements=supplements,
        )
    analysis = run.analyses[0]
    assert analysis.analysed

    documented = [s for s in analysis.scenarios if s.kind is ScenarioKind.SPORTING_EVENT]
    assert len(documented) >= 2, [s.name for s in analysis.scenarios]
    for scenario in documented:
        assert scenario.evidence_keys, f"{scenario.name} ne cite aucune source"
        assert scenario.basis

    # One magnitude is measured, the other is declared a hypothesis.
    bases = " ".join(s.basis for s in documented)
    assert "MESURÉE" in bases
    assert "HYPOTHÈSE" in bases

    by_number = {a.rubric.number: a for a in analysis.rubrics}
    assert by_number[7].status.name in ("COVERED", "PARTIAL"), by_number[7].blocker
    assert by_number[11].status.name in ("COVERED", "PARTIAL"), by_number[11].blocker
    assert by_number[7].implementation is RubricImplementation.OPERATOR_SUPPLIED

    # The imported facts must reach the sealed dossier as evidence.
    assert analysis.sealed is not None
    keys = {item.key for item in analysis.sealed.dossier.evidence}
    assert any(k.startswith("xg::") for k in keys), keys
    assert any(k.startswith("absence::") for k in keys), keys
