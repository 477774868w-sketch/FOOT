"""Régressions issues de la quatrième revue indépendante, commit `627f447`.

Deux problèmes temporels : un prix dont l'heure de relevé est inconnue ou
postérieure à l'analyse, et une déclaration future qui entrait dans le dossier
sportif scellé.  Chaque test **échoue sur `627f447`** et passe par les entrées
réelles — formulaire et ligne de commande.
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
from foot.domain import Fixture
from foot.market.odds import MatchOdds
from foot.report.card import render_card
from foot.report.web import analyse_form, render_result

PARIS = ZoneInfo("Europe/Paris")
AS_OF = dt.datetime(2026, 9, 13, 12, 0, tzinfo=PARIS)
_LINE = "Club A - Club B 14/09/2026 20:45"
_ABS_HEADER = "date,equipe,joueur,poste,motif,source,statut,publication\n"


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


def _priced_engine(quoted: dt.datetime | None) -> Engine:
    """The controlled provider, quoting 2.10 / 3.40 / 3.60 observed at ``quoted``."""
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


def _by_key(analysis: object) -> dict[str, object]:
    return {item.offer.key: item for item in analysis.priced}  # type: ignore[attr-defined]


# =========================================================================== #
# 1 — Une cote sans heure, ou venue du futur
# =========================================================================== #


def test_f1a_a_price_without_a_quoting_hour_is_not_dated_by_the_run() -> None:
    """Reproduit sur `627f447` : l'âge devenait zéro et la décision « recommandé ».

    Dans `price_catalogue`, une `Quote` dont `quoted_at` vaut ``None`` reprenait
    encore l'horodatage global du run. Une cote dont personne ne sait quand elle
    a été relevée n'a pas l'âge de l'analyse : elle a un âge **inconnu**.
    """
    result = analyse_form(_priced_engine(None), matches=_LINE, as_of=AS_OF)
    analysis = result.run.analyses[0]
    item = _by_key(analysis)["1X2:H"]
    assert item.quoted_at is None, item.quoted_at  # type: ignore[attr-defined]
    assert item.price_age is None  # type: ignore[attr-defined]
    assert not item.age_known  # type: ignore[attr-defined]


def test_f1b_an_undated_price_requires_confirmation_before_recommending() -> None:
    """Un âge inconnu ne peut pas produire une recommandation ferme."""
    result = analyse_form(_priced_engine(None), matches=_LINE, as_of=AS_OF)
    analysis = result.run.analyses[0]
    decision = analysis.decision
    assert decision is not None
    assert decision.status.value != "recommandé", decision.status
    rejected = {item.offer.key: item.rejection for item in decision.rejected}
    assert "1X2:H" in rejected, rejected
    assert "ancienneté inconnue" in rejected["1X2:H"], rejected["1X2:H"]

    card = render_card(analysis)
    assert "ancienneté inconnue" in card
    assert "heure de relevé" in card


def test_f1c_a_price_quoted_after_as_of_is_excluded() -> None:
    """Reproduit : âge −24 h, `is_stale()` False, décision « recommandé ».

    Le sélecteur contrôlait les prix trop vieux, pas ceux venus du futur. Une
    cote relevée après l'instant d'analyse n'existait pas à cet instant : elle
    est exclue de **cette** analyse.
    """
    future = AS_OF + dt.timedelta(hours=24)
    result = analyse_form(_priced_engine(future), matches=_LINE, as_of=AS_OF)
    analysis = result.run.analyses[0]
    item = _by_key(analysis)["1X2:H"]
    assert not item.has_price, "un prix postérieur à l'analyse ne peut pas être coté"  # type: ignore[attr-defined]
    assert "postérieure" in item.exclusion, item.exclusion  # type: ignore[attr-defined]

    decision = analysis.decision
    assert decision is not None
    assert decision.status.value != "recommandé", decision.status


def test_f1d_a_valid_price_survives_beside_an_excluded_one() -> None:
    """Exclure une cote ne doit pas coûter les autres marchés valides."""
    future = AS_OF + dt.timedelta(hours=24)
    result = analyse_form(
        _priced_engine(future),
        matches=f"{_LINE} BTTS:oui=1.85 TOTAL:+2.5=1.95",
        as_of=AS_OF,
        bookmaker="Saisi",
    )
    analysis = result.run.analyses[0]
    keyed = _by_key(analysis)
    assert not keyed["1X2:H"].has_price  # type: ignore[attr-defined]
    assert keyed["BTTS:yes"].has_price  # type: ignore[attr-defined]
    assert keyed["OU:2.5:over"].has_price  # type: ignore[attr-defined]
    assert keyed["BTTS:yes"].quoted_at == AS_OF  # type: ignore[attr-defined]

    decision = analysis.decision
    assert decision is not None
    assert decision.compared, "les marchés valides restent comparés"
    assert not any("1X2" in label for label in decision.compared), decision.compared


def test_f1e_the_cli_excludes_a_future_price_too() -> None:
    """Le terminal applique la même règle, et la dit."""
    future = AS_OF + dt.timedelta(hours=24)
    args = build_parser().parse_args(
        [
            "analyser",
            _LINE,
            "--date",
            "2026-09-13T12:00",
            "--no-cache",
        ]
    )
    captured: list[object] = []
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        command_analyser(
            args, engine=_priced_engine(future), on_result=captured.append
        )
    assert captured
    analysis = captured[0].run.analyses[0]  # type: ignore[attr-defined]
    assert not _by_key(analysis)["1X2:H"].has_price  # type: ignore[attr-defined]
    output = buffer.getvalue()
    assert "postérieure" in output, output[-400:]


def test_f1f_the_exclusions_are_explained_market_by_market() -> None:
    """Chaque exclusion nomme son marché et son motif, pas un total agrégé."""
    result = analyse_form(_priced_engine(None), matches=_LINE, as_of=AS_OF)
    card = render_card(result.run.analyses[0])
    assert "Victoire domicile (1)" in card
    assert "ancienneté inconnue" in card


# =========================================================================== #
# 2 — Une déclaration future ne touche pas le dossier scellé
# =========================================================================== #


_BASE = _ABS_HEADER + (
    "12/09/2026,Club A,Martin,gardien,blessure,club,officiel,12/09/2026 10:00\n"
)
_WITH_FUTURE = _BASE + (
    "13/09/2026,Club A,Martin,gardien,retour,club,officiel,13/09/2026 18:00\n"
)


def _analyse(csv: str) -> object:
    return analyse_form(
        _engine(),
        matches=f"{_LINE} @ 2.10 3.40 3.60",
        as_of=AS_OF,
        bookmaker="T",
        pasted={"absences": csv},
    )


def test_f2a_a_future_declaration_leaves_the_sealed_dossier_untouched() -> None:
    """Reproduit : l'empreinte changeait quand on ajoutait une ligne future.

    `pending_absence_updates` réintroduisait la déclaration dans les constats du
    dossier scellé, avec le type FACT. Un dossier historique ne peut pas dépendre
    d'une information qui n'existait pas à sa date.
    """
    without = _analyse(_BASE).run.analyses[0]  # type: ignore[attr-defined]
    with_future = _analyse(_WITH_FUTURE).run.analyses[0]  # type: ignore[attr-defined]
    assert without.sealed is not None
    assert with_future.sealed is not None

    assert with_future.sealed.data_fingerprint == without.sealed.data_fingerprint
    assert len(with_future.sealed.dossier.findings) == len(
        without.sealed.dossier.findings
    )
    assert with_future.sealed.dossier.expected_goals == (
        without.sealed.dossier.expected_goals
    )
    assert [s.name for s in with_future.scenarios] == [
        s.name for s in without.scenarios
    ]
    assert with_future.decision is not None
    assert without.decision is not None
    assert with_future.decision.status is without.decision.status


def test_f2b_no_finding_of_the_sealed_dossier_mentions_a_future_line() -> None:
    """Le dossier scellé ne contient plus d'avertissement d'import."""
    analysis = _analyse(_WITH_FUTURE).run.analyses[0]  # type: ignore[attr-defined]
    assert analysis.sealed is not None
    for finding in analysis.sealed.dossier.findings:
        assert "non encore exploitable" not in finding.statement, finding.statement
        assert "exploitable" not in finding.statement, finding.statement


def test_f2c_the_exclusion_is_reported_in_the_import_summary() -> None:
    """L'avertissement existe toujours — dans le compte rendu d'import."""
    result = _analyse(_WITH_FUTURE)
    notes = " ".join(result.notes)  # type: ignore[attr-defined]
    assert "Martin" in notes, notes
    assert "postérieure" in notes, notes
    summary = result.render_context()  # type: ignore[attr-defined]
    assert "Martin" in summary
    page = render_result(result, budget=None, combine=False)  # type: ignore[arg-type]
    assert "Martin" in page
    assert "écartées de cette analyse" in page
    assert "postérieure" in page


def test_f2d_a_future_publication_is_not_described_as_missing_an_hour() -> None:
    """Reproduit : le message réclamait une heure de publication qui existait.

    Deux causes distinctes d'exclusion, deux messages : une publication datée
    du futur n'est pas une publication sans heure, et dire à l'opérateur
    d'ajouter une heure qu'il a déjà fournie l'envoie corriger un fichier
    correct.
    """
    timed = " ".join(_analyse(_WITH_FUTURE).notes)  # type: ignore[attr-defined]
    assert "postérieure à l'heure d'analyse" in timed, timed
    assert "ajoutez l'heure" not in timed.lower(), timed

    undated = _ABS_HEADER.replace(",publication", "") + (
        "12/09/2026,Club A,Martin,gardien,blessure,club,officiel\n"
        "13/09/2026,Club A,Martin,gardien,retour,club,officiel\n"
    )
    text = " ".join(_analyse(undated).notes)  # type: ignore[attr-defined]
    assert "sans heure de publication" in text, text
    assert "postérieure" not in text, text


def test_f2e_the_context_summary_counts_what_was_actually_used() -> None:
    """Reproduit : « 2 absences » alors qu'une seule était exploitable."""
    result = _analyse(_WITH_FUTURE)
    assert result.used == ("1 absence(s)",), result.used  # type: ignore[attr-defined]
    assert "1 absence(s)" in result.render_context()  # type: ignore[attr-defined]


def test_f2f_the_cli_reports_the_same_exclusion() -> None:
    """Le terminal dit la même chose que le navigateur."""
    with tempfile.TemporaryDirectory() as directory:
        path = _write(Path(directory), "absences.csv", _WITH_FUTURE)
        args = build_parser().parse_args(
            [
                "analyser",
                f"{_LINE} @ 2.10 3.40 3.60",
                "--date",
                "2026-09-13T12:00",
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
    assert captured
    web = _analyse(_WITH_FUTURE)
    assert captured[0].notes == web.notes  # type: ignore[attr-defined]
    assert captured[0].used == web.used  # type: ignore[attr-defined]
    output = buffer.getvalue()
    assert "Martin" in output
    assert "postérieure" in output
