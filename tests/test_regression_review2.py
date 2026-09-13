"""Régressions issues de la seconde revue indépendante, commit `6ee19c2`.

Chaque test **échoue sur `6ee19c2`** et exerce le comportement utilisateur, pas
seulement la fonction interne : la revue demandait des cas reproduits depuis le
parcours réel.  Les noms suivent la numérotation des consignes (`d1` à `d6`).
"""

from __future__ import annotations

import datetime as dt
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo

from test_acceptance import _COMP, StubProvider, _controlled_history

from foot.analysis.engine import Engine, EngineConfig
from foot.analysis.rubrics import RUBRICS, dump_rubrics
from foot.analysis.xg import XgBalance
from foot.cli import build_parser
from foot.collect.manual import ManualProvider
from foot.collect.registry import Registry
from foot.collect.supplements import load_lineups, load_supplements
from foot.data.csv_source import write_matches
from foot.domain import Fixture, Match, MatchLog, Score
from foot.market.odds import MatchOdds
from foot.markets.selection import RankingCriteria, ScenarioKind
from foot.report.card import render_card
from foot.report.table import render_summary
from foot.report.web import analyse_form, build_page, render_form
from foot.validation.chrono import (
    PRODUCTION_RIDGE_PSEUDO_MATCHES,
    _Variant,
    validate,
)

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


def _write(directory: Path, name: str, text: str) -> Path:
    path = directory / name
    path.write_text(text, encoding="utf-8")
    return path


_XG_HEADER = "date,home,away,home_xg,away_xg,source,statut\n"
_ABS_HEADER = "date,equipe,joueur,poste,motif,source,statut\n"
_LINEUP_HEADER = "date,equipe,joueur,poste,titulaire,source,statut\n"


# =========================================================================== #
# 1 — Un import xG valide ne doit plus faire tomber le lot entier
# =========================================================================== #


def test_d1a_a_goalless_match_with_positive_xg_does_not_crash() -> None:
    """Reproduit sur `6ee19c2` : `ZeroDivisionError` dans `_documented_scenarios()`.

    Le match `2025-08-01 Club A 0-1 Club B` importé avec `home_xg=0.7` donnait un
    ratio buts/xG nul, et `1.0 / balance.ratio` levait une division par zéro. Zéro
    but est une observation parfaitement normale : elle ne peut pas interrompre
    l'analyse.
    """
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        path = _write(
            root,
            "xg.csv",
            _XG_HEADER + "01/08/2025,Club A,Club B,0.70,0.70,Understat,probable\n",
        )
        supplements = load_supplements(xg_csv=path)
        run = _engine().run(
            "Club A - Club B 14/09/2026 20:45 @ 2.10 3.40 3.60\n"
            "Club C - Club D 15/09/2026 20:45 @ 2.00 3.30 4.00",
            as_of=AS_OF,
            bookmaker="T",
        supplements=supplements,
        )
    assert len(run.analyses) == 2, "le second match ne doit pas disparaître"
    assert run.analyses[0].analysed
    assert run.analyses[1].analysed


def test_d1b_the_ratio_is_regularised_rather_than_degenerate() -> None:
    """Un ratio nul ou infini n'est pas une mesure : il faut un estimateur régularisé."""
    scoreless = XgBalance(team="Club A", matches=1, goals=0.0, expected=0.70, keys=())
    assert 0.0 < scoreless.ratio < 1.0, scoreless.ratio
    assert scoreless.reversion_factor > 1.0, "0 but pour 0,7 xG : le rythme doit remonter"

    no_xg = XgBalance(team="Club A", matches=1, goals=2.0, expected=0.0, keys=())
    assert no_xg.ratio > 1.0
    assert 0.0 < no_xg.reversion_factor < 1.0

    empty = XgBalance(team="Club A", matches=0, goals=0.0, expected=0.0, keys=())
    assert empty.ratio == 1.0, "aucun match : aucune information, donc aucun écart"
    assert not empty.material


def test_d1c_a_small_sample_is_shrunk_towards_no_effect() -> None:
    """Un seul match ne peut pas produire la même amplitude que vingt."""
    one = XgBalance(team="A", matches=1, goals=4.0, expected=1.0, keys=())
    many = XgBalance(team="A", matches=20, goals=80.0, expected=20.0, keys=())
    assert one.ratio < many.ratio, (one.ratio, many.ratio)
    assert one.reversion_factor > many.reversion_factor
    # The interval must be wider on one match than on twenty.
    assert one.interval_width > many.interval_width


def test_d1d_invalid_and_duplicate_xg_rows_are_reported_not_swallowed() -> None:
    """Valeurs non finies, négatives et doublons : signalés, jamais devinés."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        path = _write(
            root,
            "xg.csv",
            _XG_HEADER
            + "01/08/2025,Club A,Club B,0.70,0.70,Understat,probable\n"
            + "01/08/2025,Club A,Club B,1.10,0.90,Understat,probable\n"
            + "02/08/2025,Club C,Club D,-1.00,0.90,Understat,probable\n"
            + "03/08/2025,Club E,Club F,nan,0.90,Understat,probable\n"
            + "pas-une-date,Club G,Club H,1.00,0.90,Understat,probable\n",
        )
        supplements = load_supplements(xg_csv=path)
    assert len(supplements.xg) == 1, [r.date for r in supplements.xg]
    reasons = " | ".join(supplements.rejected)
    assert len(supplements.rejected) == 4, reasons
    assert "doublon" in reasons.lower(), reasons
    assert "négat" in reasons.lower(), reasons
    assert "date" in reasons.lower(), reasons


# =========================================================================== #
# 2 — La coupure de disponibilité doit valoir pour TOUT ce qui est utilisé
# =========================================================================== #


def _leaky_engine() -> Engine:
    history = list(_controlled_history())
    history.append(
        Match("Club A", "Club B", dt.date(2026, 9, 13), Score(4, 2), competition=_COMP)
    )
    return _engine(StubProvider(MatchLog(history), _fixtures()))


def test_d2a_a_same_day_result_does_not_reach_the_scenarios() -> None:
    """Reproduit sur `6ee19c2` : le modèle excluait le résultat du jour, pas les scénarios.

    Avec `as_of` à minuit le 13/09 et un `Club A 4-2 Club B` daté du 13/09, le
    dossier conservait bien 90 matchs — mais les scénarios affichaient « 4 buts
    pour 1.00 xG ».  Filtrer les preuves du registre ne filtre pas les données
    réellement utilisées.
    """
    midnight = dt.datetime(2026, 9, 13, 0, 0, tzinfo=PARIS)
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        path = _write(
            root,
            "xg.csv",
            _XG_HEADER + "13/09/2026,Club A,Club B,1.00,1.00,Understat,probable\n",
        )
        supplements = load_supplements(xg_csv=path)
        run = _leaky_engine().run(
            "Club A - Club B 14/09/2026 20:45 @ 2.10 3.40 3.60",
            as_of=midnight,
            bookmaker="T",
            supplements=supplements,
        )
    analysis = run.analyses[0]
    assert analysis.sealed is not None
    assert analysis.sealed.dossier.history_size == 90
    for scenario in analysis.scenarios:
        assert "4 buts" not in scenario.basis, scenario.basis
        assert "2 buts" not in scenario.basis, scenario.basis
    assert not [s for s in analysis.scenarios if "niveau xG" in s.name], (
        "aucune donnée disponible à as_of ne soutient un scénario xG"
    )


def test_d2b_unavailable_context_leaves_the_decision_identical() -> None:
    """Modifier une donnée indisponible à `as_of` ne doit rien changer du tout."""
    midnight = dt.datetime(2026, 9, 13, 0, 0, tzinfo=PARIS)
    line = "Club A - Club B 14/09/2026 20:45 @ 2.10 3.40 3.60"

    def decide(xg_home: str) -> tuple[str, str, float]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = _write(
                root,
                "xg.csv",
                _XG_HEADER
                + f"13/09/2026,Club A,Club B,{xg_home},1.00,Understat,probable\n",
            )
            run = _leaky_engine().run(
                line,
                as_of=midnight,
                bookmaker="T",
                supplements=load_supplements(xg_csv=path),
            )
        analysis = run.analyses[0]
        assert analysis.decision is not None
        assert analysis.sealed is not None
        return (
            analysis.decision.status.value,
            analysis.sealed.data_fingerprint,
            analysis.sealed.dossier.expected_goals[0],
        )

    assert decide("1.00") == decide("9.00")


def test_d2c_a_dated_sheet_without_a_time_is_not_assumed_available() -> None:
    """Sans horodatage, l'antériorité d'une ligne du jour n'est pas démontrable."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        path = _write(
            root,
            "c.csv",
            "date,equipe,joueur,poste,titulaire,source,statut,publication\n"
            "13/09/2026,Club A,Dupont,gardien,oui,Club A,officiel,\n"
            "13/09/2026,Club A,Bernard,defenseur,oui,Club A,officiel,13/09/2026 19:30\n",
        )
        rows = load_lineups(path)
    assert len(rows) == 2
    undated, timed = rows[0], rows[1]
    noon = dt.datetime(2026, 9, 13, 12, 0, tzinfo=PARIS)
    evening = dt.datetime(2026, 9, 13, 20, 0, tzinfo=PARIS)
    assert not undated.available_at(noon), "une date nue ne prouve pas l'antériorité"
    assert not undated.available_at(evening)
    assert undated.available_at(dt.datetime(2026, 9, 14, 0, 0, tzinfo=PARIS))
    assert not timed.available_at(noon)
    assert timed.available_at(evening), "un horodatage explicite doit être honoré"
    assert "non démontrable" in undated.availability_rule.lower()


def test_d2d_an_old_as_of_reproduces_an_old_analysis() -> None:
    """Une analyse rejouée à une date ancienne ne doit voir que le passé de cette date.

    L'historique de contrôle court du 01/08/2025 au 05/12/2025 : une analyse
    datée du 01/11/2025 doit en voir strictement moins que la totalité, et rien
    au-delà de sa propre date.
    """
    old = dt.datetime(2025, 11, 1, 12, 0, tzinfo=PARIS)
    run = _engine().run(
        "Club A - Club B 14/09/2026 20:45 @ 2.10 3.40 3.60", as_of=old
    )
    analysis = run.analyses[0]
    assert analysis.sealed is not None
    dossier = analysis.sealed.dossier
    assert 0 < dossier.history_size < 90, dossier.history_size
    assert dossier.history_span.endswith("2025-10-31") or "2025-10" in (
        dossier.history_span
    ), dossier.history_span


# =========================================================================== #
# 3 — Compositions : rattachement, version, complétude
# =========================================================================== #


T_MINUS_60 = dt.datetime(2026, 9, 14, 19, 45, tzinfo=PARIS)
"""Instant réel du contrôle : une feuille officielle n'existe pas la veille."""


def _plan_for(csv: str, *, as_of: dt.datetime = AS_OF) -> object:
    with tempfile.TemporaryDirectory() as directory:
        path = _write(Path(directory), "c.csv", csv)
        run = _engine().run(
            "Club A - Club B 14/09/2026 20:45 @ 2.10 3.40 3.60",
            as_of=as_of,
            bookmaker="T",
            supplements=load_supplements(lineups_csv=path),
        )
    return run.analyses[0].lineup_plan


def test_d3a_an_old_sheet_is_not_this_match_s_lineup() -> None:
    """Reproduit : une ligne du 1er août 2025 devenait « composition officielle » du 14/09/2026."""
    plan = _plan_for(
        _LINEUP_HEADER + "01/08/2025,Club A,Dupont,gardien,oui,Club A,officiel\n"
    )
    assert not plan.observations, plan.state()  # type: ignore[attr-defined]
    assert "aucune composition" in plan.state().lower()  # type: ignore[attr-defined]


def test_d3b_a_sheet_dated_after_as_of_is_not_usable() -> None:
    """Reproduit : une ligne du 14/09 entrait dans une observation « vue le 13/09 »."""
    plan = _plan_for(
        _LINEUP_HEADER + "14/09/2026,Club A,Dupont,gardien,oui,Club A,officiel\n"
    )
    assert not plan.observations, plan.state()  # type: ignore[attr-defined]


_PUBLISHED = "14/09/2026 19:30"


def test_d3c_a_probable_away_sheet_does_not_become_official() -> None:
    """Reproduit : `any(CONFIRMED)` étendait le statut d'une ligne à tout le relevé."""
    plan = _plan_for(
        _LINEUP_HEADER.rstrip("\n") + ",publication\n"
        + f"14/09/2026,Club A,Dupont,gardien,oui,Club A,officiel,{_PUBLISHED}\n"
        + f"14/09/2026,Club B,Leroy,gardien,oui,presse,probable,{_PUBLISHED}\n",
        as_of=T_MINUS_60,
    )
    observations = list(plan.observations)  # type: ignore[attr-defined]
    assert len(observations) == 2, "chaque équipe a sa feuille"
    by_team = {o.team: o for o in observations}
    assert by_team["Club A"].official
    assert not by_team["Club B"].official
    assert not plan.complete  # type: ignore[attr-defined]


def test_d3d_a_single_line_is_a_partial_sheet() -> None:
    """Une ligne n'est pas un onze : la complétude doit être dite."""
    plan = _plan_for(
        _LINEUP_HEADER.rstrip("\n") + ",publication\n"
        + f"14/09/2026,Club A,Seul,milieu,oui,Club A,officiel,{_PUBLISHED}\n",
        as_of=T_MINUS_60,
    )
    observations = list(plan.observations)  # type: ignore[attr-defined]
    assert observations
    assert not observations[0].complete
    assert "partielle" in plan.state().lower(), plan.state()  # type: ignore[attr-defined]


def test_d3e_a_goalkeeper_on_the_bench_is_not_the_starting_keeper() -> None:
    """Reproduit : le premier poste contenant « gardien » était retenu, titulaire ou non."""
    plan = _plan_for(
        _LINEUP_HEADER.rstrip("\n") + ",publication\n"
        + f"14/09/2026,Club A,Remplacant,gardien,non,Club A,officiel,{_PUBLISHED}\n"
        + f"14/09/2026,Club A,Titulaire,gardien,oui,Club A,officiel,{_PUBLISHED}\n",
        as_of=T_MINUS_60,
    )
    observations = list(plan.observations)  # type: ignore[attr-defined]
    assert observations[0].goalkeeper == "Titulaire", observations[0].goalkeeper


def test_d3f_an_absence_has_a_validity_window_and_a_confirmed_return() -> None:
    """Une absence ancienne ne vaut pas indéfiniment ; un retour confirmé l'annule."""
    with tempfile.TemporaryDirectory() as directory:
        path = _write(
            Path(directory),
            "a.csv",
            "date,equipe,joueur,poste,motif,source,statut,jusqu_au,retour\n"
            "01/03/2026,Club A,Ancien,gardien,blessure,Club A,officiel,01/04/2026,\n"
            "10/09/2026,Club A,Revenu,buteur,blessure,Club A,officiel,,12/09/2026\n"
            "10/09/2026,Club A,Absent,buteur,blessure,Club A,officiel,,\n",
        )
        supplements = load_supplements(absences_csv=path)
    active = supplements.absences_for(
        "Club A", on_or_before=dt.date(2026, 9, 14), as_of=AS_OF
    )
    names = {row.player for row in active}
    assert names == {"Absent"}, names


# =========================================================================== #
# 4 — Imports résultats et cotes dans le parcours principal
# =========================================================================== #


def test_d4a_manual_odds_reach_the_selector() -> None:
    """Reproduit : `ManualProvider.odds()` servait 3 prix, 0 atteignait le sélecteur."""
    history = _controlled_history()
    fixtures = _fixtures()
    target = next(f for f in fixtures if f.home == "Club A")
    provider = ManualProvider(
        results=history,
        fixtures=fixtures,
        odds={target: MatchOdds(2.10, 3.40, 3.60)},
        competition_key=_COMP,
    )
    run = _engine(provider).run("Club A - Club B 14/09/2026 20:45", as_of=AS_OF)
    analysis = run.analyses[0]
    priced = [item for item in analysis.priced if item.has_price]
    assert priced, "aucun prix importé n'a atteint le sélecteur"
    assert analysis.decision is not None
    assert analysis.decision.status.value != "angle sportif, sous condition de prix"


def test_d4b_a_90_match_import_stays_90_matches() -> None:
    """Reproduit : 90 matchs importés donnaient 270 au dossier avec les saisons par défaut."""
    history = _controlled_history()
    provider = ManualProvider(
        results=history, fixtures=_fixtures(), competition_key=_COMP
    )
    engine = Engine(Registry([provider]), config=EngineConfig(min_matches=40))
    run = engine.run(
        "Club A - Club B 14/09/2026 20:45 @ 2.10 3.40 3.60", as_of=AS_OF, bookmaker="T"
    )
    analysis = run.analyses[0]
    assert analysis.sealed is not None
    assert analysis.sealed.dossier.history_size == len(history), (
        f"{analysis.sealed.dossier.history_size} au dossier pour {len(history)} importés"
    )


def test_d4c_the_cli_exposes_a_manual_calendar_and_odds_route() -> None:
    """La voie manuelle doit être atteignable depuis les arguments réels de la CLI."""
    parser = build_parser()
    analyser = parser._subparsers._group_actions[0].choices["analyser"]  # type: ignore[union-attr,index]
    options = {s for action in analyser._actions for s in action.option_strings}
    for expected in ("--resultats-csv", "--calendrier-csv", "--cotes-csv"):
        assert expected in options, f"option absente de la CLI : {expected}"


def test_d4d_imported_odds_keep_their_own_observation_time() -> None:
    """L'heure d'observation d'un prix importé ne doit pas être écrasée par l'heure de lancement."""
    history = _controlled_history()
    fixtures = _fixtures()
    target = next(f for f in fixtures if f.home == "Club A")
    quoted = AS_OF - dt.timedelta(hours=5)
    provider = ManualProvider(
        results=history,
        fixtures=fixtures,
        odds={target: MatchOdds(2.10, 3.40, 3.60)},
        odds_quoted_at=quoted,
        competition_key=_COMP,
    )
    run = _engine(provider).run("Club A - Club B 14/09/2026 20:45", as_of=AS_OF)
    priced = [item for item in run.analyses[0].priced if item.has_price]
    assert priced
    assert priced[0].quoted_at == quoted, priced[0].quoted_at


def test_d4e_the_report_names_which_datasets_actually_fed_the_analysis() -> None:
    """L'opérateur doit voir quelles données ont réellement alimenté l'analyse."""
    history = _controlled_history()
    provider = ManualProvider(
        results=history, fixtures=_fixtures(), competition_key=_COMP
    )
    run = _engine(provider).run(
        "Club A - Club B 14/09/2026 20:45 @ 2.10 3.40 3.60", as_of=AS_OF, bookmaker="T"
    )
    card = render_card(run.analyses[0])
    assert "saisie manuelle" in card.lower(), "la source utilisée doit être nommée"
    assert str(len(history)) in card


# =========================================================================== #
# 5 — Signification sportive et statistique des scénarios
# =========================================================================== #


def _absence_scenario(poste: str, *, titulaire_remplacant: str = "") -> object:
    with tempfile.TemporaryDirectory() as directory:
        path = _write(
            Path(directory),
            "a.csv",
            "date,equipe,joueur,poste,motif,source,statut,remplacant\n"
            f"12/09/2026,Club A,Martin,{poste},blessure,Club A,officiel,"
            f"{titulaire_remplacant}\n",
        )
        run = _engine().run(
            "Club A - Club B 14/09/2026 20:45 @ 2.10 3.40 3.60",
            as_of=AS_OF,
            bookmaker="T",
            supplements=load_supplements(absences_csv=path),
        )
    return next(s for s in run.analyses[0].scenarios if "absence" in s.name.lower())


def _rates(matrix: object) -> tuple[float, float]:
    grid = matrix.grid  # type: ignore[attr-defined]
    home = sum(i * sum(row) for i, row in enumerate(grid))
    away = sum(j * sum(row[j] for row in grid) for j in range(len(grid[0])))
    return (home, away)


def test_d5a_a_missing_keeper_and_a_missing_striker_differ() -> None:
    """Reproduit : les deux donnaient exactement 1.751745 → 1.488983."""
    keeper = _rates(_absence_scenario("gardien").matrix)  # type: ignore[attr-defined]
    striker = _rates(_absence_scenario("buteur").matrix)  # type: ignore[attr-defined]
    assert keeper != striker, (keeper, striker)


def test_d5b_a_missing_keeper_weakens_the_defence_not_the_attack() -> None:
    """Un gardien absent dégrade la défense de son équipe : c'est l'adversaire qui marque plus."""
    base = _engine().run(
        "Club A - Club B 14/09/2026 20:45 @ 2.10 3.40 3.60", as_of=AS_OF, bookmaker="T"
    )
    reference = base.analyses[0].sealed.dossier.expected_goals  # type: ignore[union-attr]
    home, away = _rates(_absence_scenario("gardien").matrix)  # type: ignore[attr-defined]
    assert away > reference[1], (away, reference[1])
    assert abs(home - reference[0]) < 1e-9, "l'attaque de Club A n'est pas en cause"


def test_d5c_a_named_replacement_reduces_the_assumed_impact() -> None:
    """Un remplaçant identifié n'est pas la même situation qu'un poste laissé vide."""
    alone = _absence_scenario("buteur")
    covered = _absence_scenario("buteur", titulaire_remplacant="Doublure")
    assert _rates(alone.matrix) != _rates(covered.matrix)  # type: ignore[attr-defined]
    assert "Doublure" in covered.basis  # type: ignore[attr-defined]


def test_d5d_the_gate_kinds_match_what_the_documentation_claims() -> None:
    """Reproduit : `gate_kinds` incluait SENSITIVITY alors que la doc disait l'inverse."""
    criteria = RankingCriteria()
    assert criteria.gate_kinds == frozenset({ScenarioKind.SPORTING_EVENT}), (
        criteria.gate_kinds
    )
    assert criteria.describe()
    assert "documenté" in criteria.describe().lower()


def test_d5e_a_composite_rubric_is_not_complete_on_one_sub_requirement() -> None:
    """R07 réunit xG, npxG, tirs et grosses occasions : un xG importé ne la remplit pas."""
    history = _controlled_history()
    recent = [m for m in history if m.involves("Club A")][-3:]
    with tempfile.TemporaryDirectory() as directory:
        path = _write(
            Path(directory),
            "xg.csv",
            _XG_HEADER
            + "".join(
                f"{m.date.strftime('%d/%m/%Y')},{m.home},{m.away},1.30,1.10,Opta,probable\n"
                for m in recent
            ),
        )
        run = _engine().run(
            "Club A - Club B 14/09/2026 20:45 @ 2.10 3.40 3.60",
            as_of=AS_OF,
            bookmaker="T",
            supplements=load_supplements(xg_csv=path),
        )
    analysis = run.analyses[0]
    seven = next(a for a in analysis.rubrics if a.rubric.number == 7)
    assert seven.status.name == "PARTIAL", seven.status
    detail = " ".join(seven.detail_lines())
    assert "xG" in detail
    for missing in ("npxG", "tirs"):
        assert missing in detail, detail


# =========================================================================== #
# 6 — Interface : mêmes fonctions que la CLI, même décision
# =========================================================================== #


def test_d6a_the_web_form_offers_the_context_imports() -> None:
    """Reproduit : le formulaire ne proposait ni xG, ni absences, ni compositions."""
    form = render_form()
    for field in ("xg", "absences", "compositions"):
        assert f'name="{field}"' in form, f"champ absent du formulaire : {field}"
    page = build_page()
    assert "viewport" in page, "l'interface doit être utilisable sur téléphone"
    assert "@media (max-width:560px)" in page, "aucune mise en page pour téléphone"


def test_d6b_browser_and_cli_produce_the_same_decision() -> None:
    """À entrées identiques, le navigateur et la CLI doivent décider pareil."""
    text = "Club A - Club B 14/09/2026 20:45 @ 2.10 3.40 3.60"
    xg_paste = _XG_HEADER + "01/08/2025,Club A,Club B,0.70,0.70,Understat,probable\n"

    with tempfile.TemporaryDirectory() as directory:
        path = _write(Path(directory), "xg.csv", xg_paste)
        cli_run = _engine().run(
            text, as_of=AS_OF, bookmaker="T", supplements=load_supplements(xg_csv=path)
        )
    web_run = analyse_form(
        _engine(),
        matches=text,
        as_of=AS_OF,
        bookmaker="T",
        pasted={"xg": xg_paste},
    )
    cli = cli_run.analyses[0]
    web = web_run.run.analyses[0]
    assert cli.sealed is not None
    assert web.sealed is not None
    assert web.sealed.data_fingerprint == cli.sealed.data_fingerprint
    assert web.decision is not None
    assert cli.decision is not None
    assert web.decision.status is cli.decision.status


def test_d6c_a_rejected_pasted_line_is_shown_for_correction() -> None:
    """Une ligne rejetée doit être affichée, pas avalée : l'opérateur doit pouvoir la corriger."""
    result = analyse_form(
        _engine(),
        matches="Club A - Club B 14/09/2026 20:45 @ 2.10 3.40 3.60",
        as_of=AS_OF,
        bookmaker="T",
        pasted={"xg": _XG_HEADER + "pas-une-date,Club A,Club B,1,1,X,probable\n"},
    )
    assert result.rejected, "la ligne invalide doit être signalée"
    assert "pas-une-date" in " ".join(result.rejected)


def test_d6d_the_web_route_accepts_every_quoted_market() -> None:
    """Tous les marchés saisissables en CLI doivent l'être depuis le navigateur."""
    result = analyse_form(
        _engine(),
        matches=(
            "Club A - Club B 14/09/2026 20:45 "
            "1=2.10 N=3.40 2=3.60 BTTS:oui=1.85 TOTAL:+2.5=1.95 DC:1N=1.30"
        ),
        as_of=AS_OF,
        bookmaker="T",
    )
    analysis = result.run.analyses[0]
    priced = [item for item in analysis.priced if item.has_price]
    families = {item.offer.family.value for item in priced}
    for expected in ("1-N-2", "les deux marquent", "total de buts", "double chance"):
        assert expected in families, families

def test_d4f_an_unverified_fixture_is_not_reported_as_started() -> None:
    """Défaut vu en rejouant le parcours réel : deux états distincts confondus.

    Une rencontre absente du calendrier chargé s'affichait « hors prématch »,
    ce qui dit que le coup d'envoi est passé.  Il ne l'est pas : la rencontre
    n'a simplement pas été trouvée.  L'opérateur était envoyé chercher une heure
    qui n'a jamais existé au lieu de fournir un calendrier.
    """
    run = _engine().run("Club X - Club Y 14/09/2026 20:45 @ 2.10 3.40 3.60", as_of=AS_OF)
    run2 = _engine().run("Club A - Club B 20/09/2026 20:45 @ 2.10 3.40 3.60", as_of=AS_OF)
    assert not run2.analyses[0].analysed
    assert len(run2.unverified_matches()) == 1
    assert not run2.started_matches(), "rien n'a commencé : la date est future"
    table = render_summary(run2)
    assert "non vérifiée" in table, table
    assert len(run.analyses) == 1


def test_d4g_a_manual_calendar_makes_a_fixture_analysable() -> None:
    """La voie calendrier doit réellement débloquer une rencontre non publiée.

    Sans elle, une compétition dont le calendrier n'est servi par aucune source
    reste définitivement inanalysable, quelle que soit la qualité de son
    historique.
    """
    history = _controlled_history()
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        results = root / "resultats.csv"
        write_matches(results, history)
        calendar = _write(
            root,
            "calendrier.csv",
            "date,home,away\n20/09/2026,Club A,Club B\n",
        )
        provider = ManualProvider(
            results_csv=results, fixtures_csv=calendar, competition_key=_COMP
        )
        run = Engine(
            Registry([provider]), config=EngineConfig(min_matches=40)
        ).run(
            "Club A - Club B 20/09/2026 20:45 @ 2.10 3.40 3.60",
            as_of=AS_OF,
            bookmaker="T",
        )
    analysis = run.analyses[0]
    assert analysis.analysed, analysis.resolved.explain()
    assert analysis.resolved.kickoff_status.allows_prematch


# =========================================================================== #
# 7 — La configuration mesurée doit être la configuration recommandée
# =========================================================================== #


def test_d7a_the_validated_configuration_is_the_recommended_one() -> None:
    """La validation testait un ridge fixe ; le moteur en recommande un adaptatif.

    Publier les chiffres d'une configuration que personne n'exécute revient à
    valider autre chose que ce qui est livré. La règle de production doit donc
    figurer parmi les variantes mesurées, et les deux valeurs ne peuvent pas
    diverger sans que ce test le dise.
    """
    assert EngineConfig().ridge_pseudo_matches == PRODUCTION_RIDGE_PSEUDO_MATCHES

    history = _controlled_history()
    report = validate(history, folds=2, min_train=40, tune=False, ablate=False)
    names = {card.name for card in report.cards}
    assert "configuration de production" in names, names


def test_d7b_the_production_variant_uses_an_adaptive_ridge() -> None:
    """Le ridge de production doit réellement varier avec la taille effective."""
    history = _controlled_history()
    fixed = _Variant("fixe", 240.0, 0.3, True)
    adaptive = _Variant("prod", 240.0, 0.3, True, adaptive_ridge=120.0)
    assert fixed.ridge_for(history) == 0.3
    thin = MatchLog(list(history)[:10])
    assert adaptive.ridge_for(thin) > adaptive.ridge_for(history) > 0.0


def test_d7c_every_section_of_the_original_protocol_is_attached() -> None:
    """Le protocole fourni doit être rattaché, section par section, aux rubriques.

    La numérotation du moteur n'est pas celle du document : les constats portent
    des numéros codés en dur, et les renuméroter les casserait silencieusement.
    La correspondance est donc déclarée, et ce test interdit qu'une section
    reste orpheline.
    """
    stored = Path("protocole/FOOT_Protocole_original_22_rubriques.md")
    assert stored.exists(), "le protocole original doit être stocké dans le dépôt"
    text = stored.read_text(encoding="utf-8")
    headings = {
        int(line.split(".", 1)[0][3:])
        for line in text.splitlines()
        if line.startswith("## ") and line[3:].split(".", 1)[0].isdigit()
    }
    assert headings, "aucune section numérotée trouvée dans le protocole"

    attached = {
        int(section[1:])
        for rubric in RUBRICS
        for section in rubric.protocol_sections
    }
    # §1 is the mission statement, not an analytical requirement.
    missing = {n for n in headings if n > 1} - attached
    assert not missing, f"sections du protocole non rattachées : {sorted(missing)}"


def test_d7d_the_stored_protocol_matches_the_code() -> None:
    """Le protocole publié est généré : s'il diverge du code, il décrit autre chose."""
    stored = Path("protocole/protocole-22-rubriques.json").read_text(encoding="utf-8")
    assert stored == dump_rubrics(), (
        "protocole/protocole-22-rubriques.json est désynchronisé : "
        "régénérez-le avec dump_rubrics()"
    )
