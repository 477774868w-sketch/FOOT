"""Le raccordement complet : réponse API → contexte retenu → dossier → rapport.

Ce fichier ne teste ni le parsing ni les capacités déclarées. Il lance le
**vrai parcours** — ``foot analyser`` et son équivalent navigateur — sans aucun
CSV, avec le **véritable adaptateur** API-Football branché sur des réponses
réseau simulées, et vérifie ce que la revue demande :

* les points d'accès attendus sont réellement appelés ;
* les données admissibles arrivent jusqu'au dossier scellé ;
* les données postérieures à l'analyse en restent exclues ;
* une absence importante alimente le scénario sportif prévu pour elle ;
* terminal et navigateur donnent le même résultat.

Aucune recommandation n'est forcée pour « prouver » l'intégration : ce qui est
prouvé, c'est **quelles données ont réellement servi**.
"""

from __future__ import annotations

import datetime as dt
import io
from contextlib import redirect_stdout
from typing import Any
from zoneinfo import ZoneInfo

from test_acceptance import StubProvider, _controlled_history

from foot.analysis.engine import RECENT_MATCHES_PER_TEAM, Engine, EngineConfig
from foot.analysis.journey import run_journey
from foot.cli import build_parser, command_analyser
from foot.collect.apifootball import ApiFootballProvider, season_of
from foot.collect.registry import Registry
from foot.domain import Fixture
from foot.report.card import render_rubric_provenance
from foot.report.web import analyse_form

PARIS = ZoneInfo("Europe/Paris")
KICKOFF = dt.datetime(2026, 9, 14, 20, 45, tzinfo=PARIS)
AS_OF = dt.datetime(2026, 9, 13, 12, 0, tzinfo=PARIS)
LINE = "it.1 | Club A - Club B | 14/09/2026 20:45"

_MISSING_KEEPER = "Gardien Titulaire"


class FakeApiFootball:
    """API-Football, as far as the adapter can tell.

    Serves a fixture index, injuries, lineups and per-match statistics, and
    records every path it was asked for — which is how the test proves the
    expected endpoints were really called rather than assumed.
    """

    def __init__(
        self,
        *,
        injuries_published: dt.datetime,
        xg_by_date: dict[dt.date, tuple[float, float]] | None = None,
        pairings: dict[dt.date, list[tuple[str, str]]] | None = None,
    ) -> None:
        self.injuries_published = injuries_published
        self.xg_by_date = xg_by_date or {}
        # The real service answers a date with the match actually played that
        # day. Answering every date with the same pairing would let the adapter
        # fail to match — and hide the very call the test means to observe.
        self.pairings = pairings or {}
        self.paths: list[str] = []
        self._ids: dict[str, int] = {}

    def fetch(
        self, url: str, *, timeout: float = 20.0, headers: Any = None
    ) -> tuple[object, dt.datetime]:
        del timeout, headers
        path = url.replace("https://v3.football.api-sports.io", "")
        self.paths.append(path)
        now = dt.datetime(2026, 9, 13, 12, 0, tzinfo=dt.timezone.utc)
        if path.startswith("/fixtures?"):
            return (self._fixtures(path), now)
        if path.startswith("/injuries?"):
            return (self._injuries(), self.injuries_published)
        if path.startswith("/fixtures/lineups?"):
            return ({"errors": [], "response": []}, now)
        if path.startswith("/fixtures/statistics?"):
            return (self._statistics(path), now)
        return ({"errors": [], "response": []}, now)

    # -- endpoints ---------------------------------------------------------
    def _fixtures(self, path: str) -> dict[str, Any]:
        date = path.rsplit("date=", maxsplit=1)[-1] if "date=" in path else ""
        if not date:
            return {"errors": [], "response": []}
        day = dt.date.fromisoformat(date)
        played = self.pairings.get(day) or [("Club A", "Club B")]
        response = []
        for home, away in played:
            identifier = self._ids.setdefault(
                f"{date}|{home}|{away}", 1000 + len(self._ids)
            )
            response.append(
                {
                    "fixture": {"id": identifier, "date": f"{date}T18:45:00+00:00"},
                    "teams": {"home": {"name": home}, "away": {"name": away}},
                }
            )
        return {"errors": [], "response": response}

    def _injuries(self) -> dict[str, Any]:
        return {
            "errors": [],
            "response": [
                {
                    "player": {
                        "name": _MISSING_KEEPER,
                        "type": "Missing Fixture",
                        "reason": "Knee Injury",
                        "position": "G",
                    },
                    "team": {"name": "Club A"},
                    "fixture": {"date": "2026-09-14T18:45:00+00:00"},
                }
            ],
        }

    def _statistics(self, path: str) -> dict[str, Any]:
        identifier = int(path.rsplit("fixture=", maxsplit=1)[-1])
        date = next((d for d, i in self._ids.items() if i == identifier), "")
        key = next((k for k, i in self._ids.items() if i == identifier), "")
        if not key:
            return {"errors": [], "response": []}
        date, home_team, away_team = key.split("|")
        pair = self.xg_by_date.get(dt.date.fromisoformat(date))
        if pair is None:
            return {"errors": [], "response": []}
        home, away = pair
        return {
            "errors": [],
            "response": [
                {
                    "team": {"name": home_team},
                    "statistics": [{"type": "expected_goals", "value": str(home)}],
                },
                {
                    "team": {"name": away_team},
                    "statistics": [{"type": "expected_goals", "value": str(away)}],
                },
            ],
        }

    def called(self, prefix: str) -> list[str]:
        return [p for p in self.paths if p.startswith(prefix)]

    def statistics_dates(self) -> set[dt.date]:
        """The match dates whose statistics were actually requested."""
        by_id = {identifier: key for key, identifier in self._ids.items()}
        dates: set[dt.date] = set()
        for path in self.called("/fixtures/statistics?"):
            identifier = int(path.split("fixture=")[-1])
            key = by_id.get(identifier, "")
            if key:
                dates.add(dt.date.fromisoformat(key.split("|")[0]))
        return dates


def _history_pairings() -> dict[dt.date, list[tuple[str, str]]]:
    """Every match played on each day of the controlled history.

    A list, not one pairing: several matches share a date, and answering a date
    with only one of them made the adapter fail to find the others — hiding the
    very call this file exists to observe.
    """
    played: dict[dt.date, list[tuple[str, str]]] = {}
    for match in _controlled_history():
        played.setdefault(match.date, []).append((match.home, match.away))
    return played


def _engine(api: FakeApiFootball) -> Engine:
    fixtures = [Fixture("Club A", "Club B", dt.date(2026, 9, 14), competition="it.1")]
    providers: list[object] = [
        StubProvider(_controlled_history(), fixtures),
        ApiFootballProvider(token="jeton-de-test", fetch=api.fetch),
    ]
    return Engine(
        Registry(providers),  # type: ignore[arg-type]
        config=EngineConfig(seasons=("2026-27",), min_matches=40),
    )


def _fake(**kwargs: Any) -> FakeApiFootball:
    published = kwargs.pop("injuries_published", AS_OF - dt.timedelta(hours=6))
    pairings = _history_pairings()
    return FakeApiFootball(
        injuries_published=published,
        xg_by_date=dict.fromkeys(pairings, (1.8, 0.9)),
        pairings=pairings,
        **kwargs,
    )


# --------------------------------------------------------------------------- #
# 1. Les points d'accès attendus sont réellement appelés
# --------------------------------------------------------------------------- #


def test_i1_the_expected_endpoints_are_really_called() -> None:
    """Ajouter le fournisseur au registre ne suffisait pas : rien ne l'appelait."""
    api = _fake()
    run = _engine(api).run(LINE, as_of=AS_OF)
    assert run.analyses[0].analysed

    assert api.called("/injuries?"), "les absences n'ont pas été demandées"
    assert api.called("/fixtures/lineups?"), "les compositions non plus"
    assert api.called("/fixtures/statistics?"), "ni les statistiques"
    # The fixture index is consulted to translate our identities into ids.
    assert api.called("/fixtures?")


def test_i1b_statistics_are_asked_for_past_matches_never_for_the_upcoming_one() -> None:
    """Les statistiques d'un match non joué n'existent pas."""
    api = _fake()
    _engine(api).run(LINE, as_of=AS_OF)
    asked = api.statistics_dates()
    assert asked, "des rencontres passées doivent être interrogées"
    assert dt.date(2026, 9, 14) not in asked, (
        "la rencontre à venir n'a pas de statistiques : elle n'est pas jouée"
    )
    assert all(date < AS_OF.date() for date in asked), sorted(asked)
    # Bounded: a season-wide sweep would spend a quota on data nothing reads.
    assert len(asked) <= 2 * RECENT_MATCHES_PER_TEAM


def test_i1c_the_season_asked_for_matches_the_european_calendar() -> None:
    """Février 2026 appartient à la saison 2025-26, pas à 2026."""
    api = _fake()
    _engine(api).run(LINE, as_of=AS_OF)
    for path in api.called("/fixtures?"):
        if "date=" not in path:
            continue
        date = dt.date.fromisoformat(path.split("date=")[-1])
        season = int(path.split("season=")[-1].split("&")[0])
        assert season == season_of(date), f"{date} demandée en saison {season}"


# --------------------------------------------------------------------------- #
# 2. Les données admissibles arrivent au dossier
# --------------------------------------------------------------------------- #


def test_i2_collected_data_reaches_the_sealed_dossier() -> None:
    api = _fake()
    run = _engine(api).run(LINE, as_of=AS_OF)
    analysis = run.analyses[0]
    assert analysis.sealed is not None

    keys = [entry.key for entry in analysis.ledger.entries]
    assert any(key.startswith("absence::") for key in keys), keys
    assert any(key.startswith("stats::") for key in keys), keys

    covered = {a.rubric.number for a in analysis.rubrics if a.status.name != "UNAVAILABLE"}
    assert 11 in covered, "R11 (absences) doit cesser d'être indisponible"
    assert 7 in covered, "R07 (xG) aussi"


def test_i2b_the_report_names_the_data_that_actually_served() -> None:
    api = _fake()
    run = _engine(api).run(LINE, as_of=AS_OF)
    text = render_rubric_provenance(run.analyses[0])
    assert "API-Football" in text, "la provenance doit être citée"
    assert "relevé il y a" in text, "avec sa fraîcheur"


# --------------------------------------------------------------------------- #
# 3. Les données postérieures restent exclues
# --------------------------------------------------------------------------- #


def test_i3_an_absence_published_after_the_analysis_is_excluded() -> None:
    """Une source automatique ne voit pas plus loin qu'un opérateur."""
    late = _fake(injuries_published=AS_OF + dt.timedelta(hours=4))
    run = _engine(late).run(LINE, as_of=AS_OF)
    analysis = run.analyses[0]
    assert analysis.sealed is not None
    assert api_absences_in(analysis) == 0, (
        "publiée après l'analyse : elle n'existait pas encore"
    )

    early = _fake(injuries_published=AS_OF - dt.timedelta(hours=6))
    seen = _engine(early).run(LINE, as_of=AS_OF).analyses[0]
    assert api_absences_in(seen) == 1, "publiée avant : elle compte"


def api_absences_in(analysis: object) -> int:
    """How many collected absences the sealed input actually kept."""
    plan = getattr(analysis, "lineup_plan", None)
    del plan
    findings = getattr(analysis, "sealed").dossier.findings  # noqa: B009
    return sum(1 for f in findings if _MISSING_KEEPER in f.statement)


# --------------------------------------------------------------------------- #
# 4. Une absence importante alimente le scénario prévu
# --------------------------------------------------------------------------- #


def test_i4_a_decisive_absence_feeds_the_documented_sporting_scenario() -> None:
    api = _fake()
    analysis = _engine(api).run(LINE, as_of=AS_OF).analyses[0]
    assert analysis.sealed is not None
    kinds = {s.kind.name for s in analysis.scenarios}
    assert "SPORTING_EVENT" in kinds, (
        "l'absence d'un gardien titulaire doit produire un événement sportif "
        f"documenté, pas seulement une ligne de texte : {kinds}"
    )
    documented = [s for s in analysis.scenarios if s.kind.name == "SPORTING_EVENT"]
    assert any(
        _MISSING_KEEPER in scenario.name or _MISSING_KEEPER in scenario.basis
        for scenario in documented
    ), [scenario.name for scenario in documented]
    # A documented sporting event must cite its source; the dataclass enforces
    # it, and here the citation must be the collected absence, not a guess.
    assert all(scenario.evidence_keys for scenario in documented)


# --------------------------------------------------------------------------- #
# 5. Terminal et navigateur, même résultat
# --------------------------------------------------------------------------- #


def test_i5_terminal_and_browser_agree_without_any_csv() -> None:
    """Le vrai parcours des deux côtés, sans le moindre fichier fourni."""
    terminal_api, browser_api = _fake(), _fake()

    args = build_parser().parse_args(
        ["analyser", LINE, "--date", "2026-09-13T12:00", "--court", "--no-cache"]
    )
    captured: list[object] = []
    printed = io.StringIO()
    with redirect_stdout(printed):
        code = command_analyser(
            args, engine=_engine(terminal_api), on_result=captured.append
        )
    assert code == 0 and captured

    browser = analyse_form(
        _engine(browser_api),
        matches=LINE,
        as_of=AS_OF,
        timezone="Europe/Paris",
    )

    left = captured[0].run.analyses[0]  # type: ignore[attr-defined]
    right = browser.run.analyses[0]
    assert left.sealed is not None and right.sealed is not None
    assert left.sealed.data_fingerprint == right.sealed.data_fingerprint, (
        "mêmes entrées, même dossier scellé"
    )
    assert (left.decision is None) == (right.decision is None)
    if left.decision and right.decision:
        assert left.decision.status is right.decision.status

    assert terminal_api.called("/injuries?"), "le terminal a bien collecté"
    assert browser_api.called("/injuries?"), "le navigateur aussi"


def test_i5b_the_journey_records_what_the_collection_actually_supplied() -> None:
    api = _fake()
    result = run_journey(_engine(api), matches=LINE, as_of=AS_OF)
    context = result.render_context()
    assert "Contexte retenu" in context or result.used, (
        "ce qui a servi doit être énuméré, pas supposé"
    )
