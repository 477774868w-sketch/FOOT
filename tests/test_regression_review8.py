"""Régressions issues de la huitième revue, commit `d16e98f`.

Trois défauts reproduits aux valeurs de la revue, avec le **véritable moteur** et
le **véritable adaptateur**, et une horloge qui avance à chaque réponse :

1. une collecte fraîche s'excluait de sa propre analyse — lancement 12:00:00,
   collecte terminée 12:00:25, une absence et vingt-deux joueurs reçus, aucun
   dans le dossier ;
2. la rencontre « Arsenal FC – Chelsea FC » était reconnue dans des réponses
   « Arsenal – Chelsea », puis ses données perdaient leur équipe : zéro ligne
   xG, une absence rattachée à personne, aucune composition ;
3. « Contexte retenu » comptait des lignes que le dossier venait d'écarter.

Aucun réseau : le garde-fou de ``conftest.py`` l'interdit, et l'adaptateur reçoit
son horloge et son *fetcher*.
"""

from __future__ import annotations

import datetime as dt
import tempfile
from collections.abc import Callable
from typing import Any
from zoneinfo import ZoneInfo

from test_acceptance import StubProvider, _controlled_history

from foot.analysis.engine import Engine, EngineConfig
from foot.analysis.journey import run_journey
from foot.collect.apifootball import ApiFootballProvider, TeamAlignment
from foot.collect.cache import Cache
from foot.collect.registry import Registry
from foot.domain import Fixture
from foot.report.web import analyse_form

PARIS = ZoneInfo("Europe/Paris")
START = dt.datetime(2026, 9, 13, 12, 0, 0, tzinfo=PARIS)
MATCH = dt.date(2026, 9, 14)
LINE = "it.1 | {home} - {away} | 14/09/2026 20:45"


class Clock:
    """A clock that advances one second per response, as a real one would."""

    def __init__(self, start: dt.datetime = START, step: int = 1) -> None:
        self.t = start
        self.step = step

    def now(self) -> dt.datetime:
        return self.t

    def tick(self) -> None:
        self.t += dt.timedelta(seconds=self.step)


def _fetcher(
    clock: Clock, home: str, away: str, calls: list[str] | None = None
) -> Callable[..., tuple[object, dt.datetime]]:
    """API-Football, answering after the clock has moved on."""

    def fetch(url: str, **_kwargs: Any) -> tuple[object, dt.datetime]:
        clock.tick()
        path = url.rsplit("api-sports.io", maxsplit=1)[-1]
        if calls is not None:
            calls.append(path)
        if path.startswith("/fixtures?"):
            return (
                {
                    "errors": [],
                    "response": [
                        {
                            "fixture": {"id": 77, "date": "2026-09-14T18:45:00+00:00"},
                            "teams": {
                                "home": {"name": home},
                                "away": {"name": away},
                            },
                        }
                    ],
                },
                clock.t,
            )
        if path.startswith("/injuries?"):
            return (
                {
                    "errors": [],
                    "response": [
                        {
                            "player": {
                                "name": "Gardien Titulaire",
                                "type": "Missing Fixture",
                                "reason": "Knee",
                                "position": "G",
                            },
                            "team": {"name": home},
                            "fixture": {"date": "2026-09-14T18:45:00+00:00"},
                        }
                    ],
                },
                clock.t,
            )
        if path.startswith("/fixtures/lineups?"):
            def side(team: str) -> dict[str, Any]:
                return {
                    "team": {"name": team},
                    "startXI": [
                        {"player": {"name": f"{team} {i}", "pos": "G" if i == 1 else "D"}}
                        for i in range(1, 12)
                    ],
                    "substitutes": [],
                }

            return ({"errors": [], "response": [side(home), side(away)]}, clock.t)
        if path.startswith("/fixtures/statistics?"):
            return (
                {
                    "errors": [],
                    "response": [
                        {
                            "team": {"name": home},
                            "statistics": [{"type": "expected_goals", "value": "2.10"}],
                        },
                        {
                            "team": {"name": away},
                            "statistics": [{"type": "expected_goals", "value": "0.80"}],
                        },
                    ],
                },
                clock.t,
            )
        return ({"errors": [], "response": []}, clock.t)

    return fetch


def _engine(
    clock: Clock,
    *,
    api_home: str = "Club A",
    api_away: str = "Club B",
    cache: Cache | None = None,
    calls: list[str] | None = None,
) -> Engine:
    fixtures = [Fixture("Club A", "Club B", MATCH, competition="it.1")]
    providers: list[object] = [
        StubProvider(_controlled_history(), fixtures),
        ApiFootballProvider(
            cache,
            token="jeton-de-test",
            now=clock.now,
            fetch=_fetcher(clock, api_home, api_away, calls),
        ),
    ]
    return Engine(
        Registry(providers),  # type: ignore[arg-type]
        config=EngineConfig(seasons=("2026-27",), min_matches=40),
    )


def _absences_in(analysis: Any) -> int:
    findings = analysis.sealed.dossier.findings if analysis.sealed else ()
    return sum(1 for f in findings if "absence" in f.statement.lower())


def _sheet_players(analysis: Any) -> int:
    plan = analysis.lineup_plan
    return sum(o.starters + o.bench for o in plan.observations) if plan else 0


# --------------------------------------------------------------------------- #
# 1. Une collecte fraîche ne s'exclut plus de sa propre analyse
# --------------------------------------------------------------------------- #


def test_j1_a_live_analysis_uses_what_its_own_collection_brought_back() -> None:
    """Lancement 12:00:00, réponses reçues quelques secondes plus tard."""
    clock = Clock()
    run = _engine(clock).run(
        LINE.format(home="Club A", away="Club B"), as_of=START, live=True
    )
    analysis = run.analyses[0]
    assert clock.t > START, "la collecte a bien pris du temps"
    assert _absences_in(analysis) > 0, (
        "l'absence reçue à "
        f"{clock.t:%H:%M:%S} doit entrer dans une analyse lancée à {START:%H:%M:%S}"
    )
    assert _sheet_players(analysis) == 22


def test_j1b_a_replay_keeps_the_date_it_was_asked_about() -> None:
    """La date d'un rejeu est la question posée : la déplacer en pose une autre."""
    clock = Clock()
    run = _engine(clock).run(
        LINE.format(home="Club A", away="Club B"), as_of=START, live=False
    )
    analysis = run.analyses[0]
    assert _absences_in(analysis) == 0, (
        "une donnée reçue après l'instant rejoué n'existait pas à cet instant"
    )
    assert _sheet_players(analysis) == 0
    assert analysis.sealed is not None
    assert analysis.sealed.dossier.as_of == START


def test_j1c_publication_hours_are_never_back_dated() -> None:
    """On avance la coupure ; on ne fabrique pas une publication antérieure."""
    clock = Clock()
    provider = ApiFootballProvider(
        token="jeton-de-test", now=clock.now, fetch=_fetcher(clock, "Club A", "Club B")
    )
    rows, _evidence = provider.absences(
        Fixture("Club A", "Club B", MATCH, competition="it.1")
    )
    assert rows and rows[0].published_at is not None
    assert rows[0].published_at > START, (
        "la ligne garde l'heure à laquelle elle a réellement été observée"
    )


def test_j1d_the_first_run_without_cache_and_the_next_after_expiry() -> None:
    """Premier lancement sans cache, puis suivi une fois le cache périmé."""
    with tempfile.TemporaryDirectory() as folder:
        clock = Clock()
        calls: list[str] = []
        cache = Cache(folder, ttl_seconds=120)
        first = _engine(clock, cache=cache, calls=calls).run(
            LINE.format(home="Club A", away="Club B"), as_of=clock.now(), live=True
        )
        assert _absences_in(first.analyses[0]) > 0
        made = len(calls)
        assert made > 0

        # Same engine, same cache, a few seconds later: served from the cache.
        cached = _engine(clock, cache=cache, calls=calls).run(
            LINE.format(home="Club A", away="Club B"), as_of=clock.now(), live=True
        )
        assert _absences_in(cached.analyses[0]) > 0
        assert len(calls) < made * 2, "le cache doit éviter des requêtes"

        # Past the TTL: fetched again, and still admissible.
        clock.t += dt.timedelta(seconds=300)
        after = _engine(clock, cache=cache, calls=calls).run(
            LINE.format(home="Club A", away="Club B"), as_of=clock.now(), live=True
        )
        assert _absences_in(after.analyses[0]) > 0, (
            "après expiration, la donnée refetchée doit rester utilisable"
        )
        assert _sheet_players(after.analyses[0]) == 22


# --------------------------------------------------------------------------- #
# 2. Les identités d'équipe traversent la collecte
# --------------------------------------------------------------------------- #


def _provider(clock: Clock, home: str, away: str) -> ApiFootballProvider:
    return ApiFootballProvider(
        token="jeton-de-test", now=clock.now, fetch=_fetcher(clock, home, away)
    )


def test_j2_statistics_absences_and_sheets_keep_our_team_names() -> None:
    """« Arsenal » dans la réponse, « Arsenal FC » chez nous : une seule équipe."""
    fixture = Fixture("Arsenal FC", "Chelsea FC", MATCH, competition="it.1")
    provider = _provider(Clock(), "Arsenal", "Chelsea")

    rows, _ = provider.xg_rows([fixture])
    assert len(rows) == 1, "deux statistiques reçues, une ligne xG attendue"
    assert (rows[0].home, rows[0].away) == ("Arsenal FC", "Chelsea FC")

    absences, _ = provider.absences(fixture)
    assert absences and absences[0].team == "Arsenal FC"

    sheets, _ = provider.team_sheets(fixture)
    by_team = {team: sum(1 for r in sheets if r.team == team) for team in
               {r.team for r in sheets}}
    assert by_team == {"Arsenal FC": 11, "Chelsea FC": 11}


def test_j2b_the_provider_s_own_spelling_is_kept_for_traceability() -> None:
    fixture = Fixture("Arsenal FC", "Chelsea FC", MATCH, competition="it.1")
    absences, _ = _provider(Clock(), "Arsenal", "Chelsea").absences(fixture)
    assert absences
    assert "Arsenal" in absences[0].source, absences[0].source
    assert "reçu sous" in absences[0].source


def test_j2c_an_ambiguous_alignment_is_refused_rather_than_guessed() -> None:
    fixture = Fixture("Arsenal FC", "Arsenal", MATCH, competition="it.1")
    alignment = TeamAlignment.build(fixture, ["Arsenal", "Arsenal FC"])
    assert alignment.internal == {}, (
        "deux côtés qui se replient l'un sur l'autre : aucun n'est rattaché"
    )

    fine = TeamAlignment.build(
        Fixture("Arsenal FC", "Chelsea FC", MATCH), ["Arsenal", "Chelsea"]
    )
    assert fine.rename("Arsenal") == "Arsenal FC"
    assert fine.rename("Tottenham") == "Tottenham", "un nom inconnu reste tel quel"


def test_j2d_a_row_for_neither_side_is_dropped_not_attached_to_a_guess() -> None:
    fixture = Fixture("Arsenal FC", "Chelsea FC", MATCH, competition="it.1")
    sheets, _ = _provider(Clock(), "Tottenham", "Fulham").team_sheets(fixture)
    assert sheets == (), "aucune de ces équipes n'est celle de la rencontre"


# --------------------------------------------------------------------------- #
# 3. Reçu, retenu, écarté
# --------------------------------------------------------------------------- #


def test_j3_the_context_paragraph_counts_what_the_dossier_kept() -> None:
    """Le paragraphe ne doit pas contredire l'analyse qu'il introduit."""
    clock = Clock()
    result = run_journey(
        _engine(clock),
        matches=LINE.format(home="Club A", away="Club B"),
        as_of=START,
        live=False,
    )
    analysis = result.run.analyses[0]
    assert _absences_in(analysis) == 0, "le dossier a bien écarté l'absence"
    context = result.render_context()
    assert "1 absence(s) — collectées" not in context, (
        "compter une ligne écartée comme du contexte retenu"
    )
    assert "écartée(s)" in context, "et ce qui a été reçu puis écarté est dit"


def test_j3b_what_is_retained_is_counted_when_it_really_is() -> None:
    clock = Clock()
    result = run_journey(
        _engine(clock),
        matches=LINE.format(home="Club A", away="Club B"),
        as_of=START,
        live=True,
    )
    analysis = result.run.analyses[0]
    assert _absences_in(analysis) > 0
    context = result.render_context()
    assert "1 absence(s)" in context
    assert "22 ligne(s) de composition" in context
    assert "écartée(s)" not in context, "rien n'a été écarté ici"


def test_j3c_the_browser_reports_exactly_what_the_terminal_reports() -> None:
    terminal = run_journey(
        _engine(Clock()),
        matches=LINE.format(home="Club A", away="Club B"),
        as_of=START,
        live=False,
    )
    browser = analyse_form(
        _engine(Clock()),
        matches=LINE.format(home="Club A", away="Club B"),
        as_of=START,
        live=False,
    )
    assert terminal.used == browser.used
    assert terminal.render_context() == browser.render_context()
