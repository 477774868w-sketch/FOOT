"""Régressions issues de la troisième revue indépendante, commit `8ed1584`.

Chaque test **échoue sur `8ed1584`** et exerce le parcours utilisateur réel —
`Engine.run`, `analyse_form` et la commande `analyser` telle qu'un opérateur
l'invoque, pas seulement la fonction interne.
"""

from __future__ import annotations

import datetime as dt
import io
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
from zoneinfo import ZoneInfo

from test_acceptance import _COMP, StubProvider, _controlled_history

from foot.analysis.engine import Engine, EngineConfig
from foot.cli import build_parser, command_analyser
from foot.collect.manual import ManualProvider
from foot.collect.registry import Registry
from foot.collect.supplements import supplements_from_text
from foot.domain import Fixture
from foot.market.odds import MatchOdds
from foot.report.card import render_card
from foot.report.web import analyse_form, render_result

PARIS = ZoneInfo("Europe/Paris")
UTC = dt.timezone.utc
AS_OF = dt.datetime(2026, 9, 13, 12, 0, tzinfo=PARIS)
T_MINUS_60 = dt.datetime(2026, 9, 14, 19, 45, tzinfo=PARIS)

_LINE = "Club A - Club B 14/09/2026 20:45"


def _fixtures() -> list[Fixture]:
    return [
        Fixture("Club A", "Club B", dt.date(2026, 9, 14), competition=_COMP),
        Fixture("Club C", "Club D", dt.date(2026, 9, 15), competition=_COMP),
    ]


def _engine(provider: object | None = None) -> Engine:
    stub = provider or StubProvider(_controlled_history(), _fixtures())
    return Engine(
        Registry([stub]),  # type: ignore[list-item]
        config=EngineConfig(seasons=("2026-27",), min_matches=40),
    )


def _priced_engine(quoted: dt.datetime) -> Engine:
    """An engine whose only source quotes a price observed at ``quoted``."""
    fixtures = _fixtures()
    target = next(f for f in fixtures if f.home == "Club A")
    return _engine(
        ManualProvider(
            results=_controlled_history(),
            fixtures=fixtures,
            odds={target: MatchOdds(2.10, 3.40, 3.60)},
            odds_quoted_at=quoted,
            competition_key=_COMP,
        )
    )


def _write(directory: Path, name: str, text: str) -> Path:
    path = directory / name
    path.write_text(text, encoding="utf-8")
    return path


# =========================================================================== #
# 1 — Une cote importée garde son âge réel, quel que soit le chemin
# =========================================================================== #


def test_e1a_an_imported_price_keeps_its_age_through_the_form() -> None:
    """Reproduit sur `8ed1584` : 48 h d'ancienneté ramenées à zéro par le formulaire.

    `Engine.run` conservait l'ancienneté — cote périmée, « aucun pari » — tandis
    qu'`analyse_form`, qui transmet `quoted_at=as_of`, la remettait à zéro et
    concluait « recommandé ».  Relancer une analyse ne peut pas rajeunir un prix.
    """
    quoted = AS_OF - dt.timedelta(hours=48)

    direct = _priced_engine(quoted).run(_LINE, as_of=AS_OF)
    web = analyse_form(_priced_engine(quoted), matches=_LINE, as_of=AS_OF)

    def state(analysis: object) -> tuple[dt.datetime | None, str]:
        priced = [item for item in analysis.priced if item.has_price]  # type: ignore[attr-defined]
        assert priced, "aucun prix n'a atteint le sélecteur"
        assert analysis.decision is not None  # type: ignore[attr-defined]
        return (priced[0].quoted_at, analysis.decision.status.value)  # type: ignore[attr-defined]

    assert state(web.run.analyses[0]) == state(direct.analyses[0])
    moment, _status = state(web.run.analyses[0])
    assert moment == quoted, moment


def test_e1b_typed_and_imported_prices_keep_their_own_timestamps() -> None:
    """Mélanger des cotes saisies et importées ne doit pas aligner leurs heures.

    Chaque prix porte son propre instant de relevé : une cote tapée à l'instant
    reste fraîche, une cote importée de la veille reste vieille, sur la même
    rencontre.
    """
    quoted = AS_OF - dt.timedelta(hours=48)
    run = _priced_engine(quoted).run(
        f"{_LINE} BTTS:oui=1.85", as_of=AS_OF, quoted_at=AS_OF, bookmaker="Saisi"
    )
    analysis = run.analyses[0]
    by_key = {
        item.offer.key: item for item in analysis.priced if item.has_price
    }
    assert "BTTS:yes" in by_key, sorted(by_key)
    assert "1X2:H" in by_key, sorted(by_key)
    assert by_key["BTTS:yes"].quoted_at == AS_OF
    assert by_key["1X2:H"].quoted_at == quoted, by_key["1X2:H"].quoted_at


def test_e1c_each_price_keeps_the_source_that_quoted_it() -> None:
    """La provenance d'un prix voyage avec lui jusqu'au sélecteur."""
    quoted = AS_OF - dt.timedelta(hours=2)
    run = _priced_engine(quoted).run(
        f"{_LINE} BTTS:oui=1.85", as_of=AS_OF, quoted_at=AS_OF, bookmaker="Saisi"
    )
    by_key = {
        item.offer.key: item
        for item in run.analyses[0].priced
        if item.has_price
    }
    assert by_key["BTTS:yes"].offer.bookmaker == "Saisi"
    assert by_key["1X2:H"].offer.bookmaker == "saisie manuelle", (
        by_key["1X2:H"].offer.bookmaker
    )


# =========================================================================== #
# 2 — Une feuille plus récente remplace la précédente, depuis l'import
# =========================================================================== #


def _two_versions(order: str = "chronologique") -> str:
    header = "date,equipe,joueur,poste,titulaire,source,statut,publication\n"
    probable = [
        f"14/09/2026,Club A,J{i},{'gardien' if i == 0 else 'milieu'},oui,"
        f"presse,probable,14/09/2026 18:00\n"
        for i in range(11)
    ]
    official = [
        f"14/09/2026,Club A,J{i},{'gardien' if i == 0 else 'milieu'},oui,"
        f"Club A,officiel,14/09/2026 19:30\n"
        for i in range(11)
    ]
    blocks = (probable, official) if order == "chronologique" else (official, probable)
    return header + "".join(blocks[0]) + "".join(blocks[1])


def test_e2a_an_official_sheet_supersedes_the_probable_one_from_a_csv() -> None:
    """Reproduit : les onze lignes officielles étaient rejetées comme doublons.

    `load_lineups` dédupliquait sur ``(date, équipe, joueur)`` sans distinguer
    les versions, si bien qu'une feuille officielle publiée à 19 h 30 ne pouvait
    jamais remplacer la feuille probable de 18 h.
    """
    supplements = supplements_from_text(lineups=_two_versions(), tzinfo=PARIS)
    assert len(supplements.lineups) == 22, len(supplements.lineups)
    assert not supplements.rejected, supplements.rejected

    run = _engine().run(
        f"{_LINE} @ 2.10 3.40 3.60",
        as_of=T_MINUS_60,
        bookmaker="T",
        supplements=supplements,
    )
    plan = run.analyses[0].lineup_plan
    assert plan is not None
    assert len(plan.observations) == 1, [o.team for o in plan.observations]
    observation = plan.observations[0]
    assert observation.official, "la dernière feuille disponible est officielle"
    assert observation.starters == 11
    assert observation.goalkeeper == "J0"
    assert plan.superseded, "la version remplacée doit rester tracée"


def test_e2b_the_result_does_not_depend_on_the_row_order() -> None:
    """Le même fichier, écrit à l'envers, doit donner exactement le même relevé."""
    forward = supplements_from_text(lineups=_two_versions(), tzinfo=PARIS)
    backward = supplements_from_text(lineups=_two_versions("inverse"), tzinfo=PARIS)

    def observed(supplements: object) -> tuple[bool, int, str | None]:
        run = _engine().run(
            f"{_LINE} @ 2.10 3.40 3.60",
            as_of=T_MINUS_60,
            bookmaker="T",
            supplements=supplements,  # type: ignore[arg-type]
        )
        plan = run.analyses[0].lineup_plan
        assert plan is not None
        item = plan.observations[0]
        return (item.official, item.starters, item.goalkeeper)

    assert observed(forward) == observed(backward)


def test_e2c_a_version_published_after_as_of_is_not_used_yet() -> None:
    """À 18 h 30, la feuille officielle de 19 h 30 n'existe pas encore."""
    supplements = supplements_from_text(lineups=_two_versions(), tzinfo=PARIS)
    run = _engine().run(
        f"{_LINE} @ 2.10 3.40 3.60",
        as_of=dt.datetime(2026, 9, 14, 18, 30, tzinfo=PARIS),
        bookmaker="T",
        supplements=supplements,
    )
    plan = run.analyses[0].lineup_plan
    assert plan is not None
    assert len(plan.observations) == 1
    assert not plan.observations[0].official, "seule la version de 18 h est connue"
    assert not plan.superseded, "rien n'a encore été remplacé"


# =========================================================================== #
# 3 — L'état d'un joueur se résout chronologiquement
# =========================================================================== #


_ABS_HEADER = "date,equipe,joueur,poste,motif,source,statut,publication\n"


def test_e3a_a_confirmed_return_cancels_an_earlier_absence() -> None:
    """Reproduit : la blessure du 12 restait active malgré le retour du 13.

    Les deux déclarations étaient connues avant l'analyse ; `absences_for`
    conservait pourtant la première.
    """
    csv = (
        _ABS_HEADER
        + "12/09/2026,Club A,Martin,gardien,blessure,club,officiel,12/09/2026 10:00\n"
        + "13/09/2026,Club A,Martin,gardien,retour,club,officiel,13/09/2026 09:00\n"
    )
    supplements = supplements_from_text(absences=csv, tzinfo=PARIS)
    assert len(supplements.absences) == 2, supplements.rejected
    active = supplements.absences_for(
        "Club A", on_or_before=dt.date(2026, 9, 14), as_of=AS_OF
    )
    assert [row.player for row in active] == [], [row.reason for row in active]


def test_e3b_a_later_injury_reinstates_the_absence() -> None:
    """Après un retour, une nouvelle blessure doit rétablir l'absence."""
    csv = (
        _ABS_HEADER
        + "10/09/2026,Club A,Martin,gardien,blessure,club,officiel,10/09/2026 10:00\n"
        + "11/09/2026,Club A,Martin,gardien,retour,club,officiel,11/09/2026 09:00\n"
        + "12/09/2026,Club A,Martin,gardien,rechute,club,officiel,12/09/2026 18:00\n"
    )
    supplements = supplements_from_text(absences=csv, tzinfo=PARIS)
    active = supplements.absences_for(
        "Club A", on_or_before=dt.date(2026, 9, 14), as_of=AS_OF
    )
    assert [row.player for row in active] == ["Martin"]
    assert active[0].reason == "rechute", active[0].reason


def test_e3c_a_return_published_after_as_of_does_not_apply_yet() -> None:
    """Un retour annoncé après l'analyse ne peut pas rétroagir sur elle."""
    csv = (
        _ABS_HEADER
        + "12/09/2026,Club A,Martin,gardien,blessure,club,officiel,12/09/2026 10:00\n"
        + "13/09/2026,Club A,Martin,gardien,retour,club,officiel,13/09/2026 18:00\n"
    )
    supplements = supplements_from_text(absences=csv, tzinfo=PARIS)
    active = supplements.absences_for(
        "Club A", on_or_before=dt.date(2026, 9, 14), as_of=AS_OF
    )
    assert [row.player for row in active] == ["Martin"]


def test_e3d_the_resolved_state_drives_the_scenarios() -> None:
    """Le scénario sportif suit l'état résolu, pas la première déclaration lue."""
    csv = (
        _ABS_HEADER
        + "12/09/2026,Club A,Martin,gardien,blessure,club,officiel,12/09/2026 10:00\n"
        + "13/09/2026,Club A,Martin,gardien,retour,club,officiel,13/09/2026 09:00\n"
    )
    run = _engine().run(
        f"{_LINE} @ 2.10 3.40 3.60",
        as_of=AS_OF,
        bookmaker="T",
        supplements=supplements_from_text(absences=csv, tzinfo=PARIS),
    )
    named = [s.name for s in run.analyses[0].scenarios if "absence" in s.name.lower()]
    assert named == [], named


# =========================================================================== #
# 4 — La CLI et le formulaire lisent les mêmes entrées de la même façon
# =========================================================================== #


def test_e4a_the_cli_reads_publication_times_in_the_chosen_timezone() -> None:
    """Reproduit : la CLI lisait « 13:00 » en Europe/Paris même en fuseau UTC.

    Une absence publiée à 13 h UTC entrait alors dans une analyse datée de 12 h
    UTC, parce que `command_analyser` appelait le chargeur sans lui transmettre
    le fuseau choisi.
    """
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        absences = _write(
            root,
            "absences.csv",
            _ABS_HEADER
            + "13/09/2026,Club A,Martin,gardien,blessure,club,officiel,"
            "13/09/2026 13:00\n",
        )
        parser = build_parser()
        args = parser.parse_args(
            [
                "analyser",
                f"{_LINE} @ 2.10 3.40 3.60",
                "--date",
                "2026-09-13T12:00",
                "--fuseau",
                "UTC",
                "--absences-csv",
                str(absences),
                "--no-cache",
            ]
        )
        captured: list[object] = []
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = command_analyser(args, engine=_engine(), on_result=captured.append)
    assert code == 0
    assert captured
    analysis = captured[0].run.analyses[0]  # type: ignore[attr-defined]
    assert analysis.sealed is not None

    # The absence must not enter the dossier…
    keys = {item.key for item in analysis.sealed.dossier.evidence}
    assert not [k for k in keys if k.startswith("absence::")], keys
    assert not [s for s in analysis.scenarios if "absence" in s.name.lower()]

    # …but it must be named as refused, not dropped in silence.
    output = buffer.getvalue()
    assert "non encore exploitables" in output or "Martin" not in output, output[-500:]


def test_e4b_cli_and_form_agree_outside_europe_paris() -> None:
    """Mêmes données, même fuseau : les deux surfaces doivent décider pareil."""
    csv = (
        _ABS_HEADER
        + "12/09/2026,Club A,Martin,gardien,blessure,club,officiel,12/09/2026 10:00\n"
    )
    as_of = dt.datetime(2026, 9, 13, 12, 0, tzinfo=UTC)

    web = analyse_form(
        _engine(),
        matches=f"{_LINE} @ 2.10 3.40 3.60",
        as_of=as_of,
        timezone="UTC",
        bookmaker="T",
        pasted={"absences": csv},
    )

    with tempfile.TemporaryDirectory() as directory:
        path = _write(Path(directory), "absences.csv", csv)
        parser = build_parser()
        args = parser.parse_args(
            [
                "analyser",
                f"{_LINE} @ 2.10 3.40 3.60",
                "--date",
                "2026-09-13T12:00",
                "--fuseau",
                "UTC",
                "--bookmaker",
                "T",
                "--absences-csv",
                str(path),
                "--no-cache",
            ]
        )
        captured: list[object] = []
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            command_analyser(args, engine=_engine(), on_result=captured.append)
    assert captured, "la commande doit exposer son résultat"
    cli = captured[0].run.analyses[0]  # type: ignore[attr-defined]
    browser = web.run.analyses[0]
    assert cli.sealed is not None
    assert browser.sealed is not None
    assert cli.sealed.data_fingerprint == browser.sealed.data_fingerprint
    assert cli.decision is not None
    assert browser.decision is not None
    assert cli.decision.status is browser.decision.status


def test_e4c_the_cli_reports_refused_context_lines() -> None:
    """Une ligne refusée doit être dite à l'opérateur, en terminal comme au navigateur."""
    with tempfile.TemporaryDirectory() as directory:
        path = _write(
            Path(directory),
            "absences.csv",
            _ABS_HEADER + "pas-une-date,Club A,Martin,gardien,blessure,club,officiel,\n",
        )
        parser = build_parser()
        args = parser.parse_args(
            [
                "analyser",
                f"{_LINE} @ 2.10 3.40 3.60",
                "--date",
                "2026-09-13T12:00",
                "--absences-csv",
                str(path),
                "--no-cache",
            ]
        )
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            command_analyser(args, engine=_engine())
    output = buffer.getvalue()
    assert "pas-une-date" in output, output[-400:]


def test_e3e_a_pending_declaration_is_named_rather_than_ignored() -> None:
    """Le refus d'une ligne non encore publique doit être dit, pas silencieux.

    Sans horodatage, une blessure de la veille est admise et le retour du jour
    ne l'est pas : on retient l'information défavorable et on écarte la favorable,
    du même émetteur. La règle de disponibilité est juste — l'antériorité n'est
    pas démontrable — mais la fiche ne peut pas affirmer une absence que sa
    propre source a levée sans le signaler.
    """
    csv = (
        "date,equipe,joueur,poste,motif,source,statut\n"
        "12/09/2026,Club A,Martin,gardien,blessure,club,officiel\n"
        "13/09/2026,Club A,Martin,gardien,retour,club,officiel\n"
    )
    run = _engine().run(
        f"{_LINE} @ 2.10 3.40 3.60",
        as_of=AS_OF,
        bookmaker="T",
        supplements=supplements_from_text(absences=csv, tzinfo=PARIS),
    )
    analysis = run.analyses[0]
    assert analysis.sealed is not None
    statements = " ".join(f.statement for f in analysis.sealed.dossier.findings)
    assert "non encore exploitables" in statements, statements
    assert "Martin" in statements

    card = render_card(analysis)
    assert "non encore exploitables" in card
    assert "horodatage de publication" in card


def test_e3f_a_timestamped_return_needs_no_warning() -> None:
    """Quand l'opérateur donne l'heure, il n'y a plus rien en attente."""
    csv = (
        _ABS_HEADER
        + "12/09/2026,Club A,Martin,gardien,blessure,club,officiel,12/09/2026 10:00\n"
        + "13/09/2026,Club A,Martin,gardien,retour,club,officiel,13/09/2026 09:00\n"
    )
    run = _engine().run(
        f"{_LINE} @ 2.10 3.40 3.60",
        as_of=AS_OF,
        bookmaker="T",
        supplements=supplements_from_text(absences=csv, tzinfo=PARIS),
    )
    analysis = run.analyses[0]
    assert analysis.sealed is not None
    statements = " ".join(f.statement for f in analysis.sealed.dossier.findings)
    assert "non encore exploitables" not in statements, statements


def test_e4d_both_surfaces_word_the_context_summary_identically() -> None:
    """Le même résumé de contexte, au terminal et au navigateur.

    Deux formulations pour un même fait obligent l'opérateur à vérifier deux
    fois qu'il lit bien la même chose.
    """
    csv = (
        _ABS_HEADER
        + "12/09/2026,Club A,Martin,gardien,blessure,club,officiel,12/09/2026 10:00\n"
    )
    result = analyse_form(
        _engine(),
        matches=f"{_LINE} @ 2.10 3.40 3.60",
        as_of=AS_OF,
        bookmaker="T",
        pasted={"absences": csv},
    )
    assert result.used == ("1 absence(s)",), result.used
    terminal = result.render_context()
    page = render_result(result, budget=None, combine=False)
    assert "Contexte retenu" in terminal
    assert "Contexte retenu" in page
    assert "1 absence(s)" in terminal
    assert "1 absence(s)" in page
