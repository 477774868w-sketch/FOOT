"""Régressions issues de la sixième revue indépendante, commit `b27d03c`.

Les reproductions de la revue, aux mêmes valeurs, et le chemin réellement
emprunté par le bouton **Suivre** — pas ``Cache.with_ttl`` pris isolément.

Le réseau est simulé au niveau de :func:`foot.collect.http.fetch_json`, ce qui
laisse le **véritable adaptateur** faire son travail : son cache, son parsing,
ses en-têtes. Une doublure d'adaptateur aurait laissé passer exactement le défaut
que cette revue a trouvé.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from zoneinfo import ZoneInfo

from test_acceptance import _COMP, StubProvider, _controlled_history
from test_daily_use import _forecast, _played

from foot.analysis.closing import load_closing_csv, load_closing_from_sources
from foot.analysis.engine import Engine, EngineConfig
from foot.analysis.ledgerbook import ForecastBook
from foot.analysis.measure import ClosingState, closing_key, measure
from foot.analysis.supervisor import LedgerJob, Supervisor, WatchHandle
from foot.analysis.watch import (
    WATCH_CACHE_TTL,
    WatchPlan,
    WatchReport,
    watch_until_kickoff,
)
from foot.cli import _watch_engine, build_parser
from foot.collect.cache import Cache
from foot.collect.footballdata import (
    COMPETITION_KEYS,
    DIVISION_FOR_KEY,
    _closing_columns,
)
from foot.collect.footballdata_org import FootballDataOrgProvider
from foot.collect.registry import Registry
from foot.domain import Fixture
from foot.market.odds import MatchOdds
from foot.provenance import Evidence
from foot.report.web import _render_ledger

PARIS = ZoneInfo("Europe/Paris")
KICKOFF = dt.datetime(2026, 9, 14, 20, 45, tzinfo=PARIS)
# The competition is named because the fake API serves the same pairing in
# every league it maps; without it the engine rightly calls the line ambiguous.
_LINE = "it.1 | Club A - Club B | 14/09/2026 20:45"
_CACHE = "_cache"


def _match_row(identifier: int) -> dict[str, object]:
    return {
        "id": identifier,
        "utcDate": "2026-09-14T18:45:00Z",
        "status": "TIMED",
        "homeTeam": {"name": "Club A"},
        "awayTeam": {"name": "Club B"},
        "score": {"fullTime": {"home": None, "away": None}},
    }


def _sheet(published: bool) -> dict[str, object]:
    """One ``/matches/{id}`` response, with or without the team sheets."""
    if not published:
        return {"homeTeam": {"name": "Club A"}, "awayTeam": {"name": "Club B"}}
    eleven = [
        {"name": f"Joueur {i}", "position": "Centre-Back" if i > 1 else "Goalkeeper"}
        for i in range(1, 12)
    ]
    return {
        "homeTeam": {"name": "Club A", "lineup": eleven, "bench": []},
        "awayTeam": {"name": "Club B", "lineup": eleven, "bench": []},
    }


class FakeApi:
    """A football-data.org that publishes its sheets at a chosen moment."""

    def __init__(self, *, publishes_at: dt.datetime, clock: dict[str, dt.datetime]):
        self.publishes_at = publishes_at
        self.clock = clock
        self.calls: list[tuple[dt.datetime, str]] = []

    def fetch(
        self, url: str, *, timeout: float = 20.0, headers: object = None
    ) -> tuple[object, dt.datetime]:
        del timeout, headers
        now = self.clock["t"]
        self.calls.append((now, url))
        if "/matches/" in url and "competitions" not in url:
            return (_sheet(now >= self.publishes_at), now)
        return ({"matches": [_match_row(77)], "competition": {"name": "Test"}}, now)

    def sheet_calls(self) -> list[dt.datetime]:
        return [at for at, url in self.calls if "/matches/77" in url]


def _engine(
    cache: Cache,
    clock: dict[str, dt.datetime] | None = None,
    api: FakeApi | None = None,
) -> Engine:
    """The stub for history, the **real** adapter for team sheets.

    The adapter is given the simulated clock so its cache expires on the watch's
    timeline rather than on the wall clock — otherwise three checks fifteen
    simulated minutes apart happen within one real millisecond, and no TTL could
    ever tell them apart.
    """
    # "it.1" because the real adapter only serves the competitions it maps;
    # a made-up key would have it refuse before any cache was consulted.
    fixtures = [Fixture("Club A", "Club B", dt.date(2026, 9, 14), competition="it.1")]
    providers: list[object] = [
        StubProvider(_controlled_history(), fixtures),
        FootballDataOrgProvider(
            cache,
            token="jeton-de-test",
            now=(lambda: clock["t"]) if clock is not None else None,
            fetch=api.fetch if api is not None else None,
        ),
    ]
    return Engine(
        Registry(providers),  # type: ignore[arg-type]
        config=EngineConfig(seasons=("2026-27",), min_matches=40),
    )


def _wait_for(predicate: Callable[[], bool], *, timeout: float = 5.0) -> bool:
    """Wait for a background thread to reach a state, without spinning.

    A bare ``for _ in range(500)`` never yields the GIL long enough for the other
    thread to finish, so it tests the scheduler rather than the code.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


def _run_watch(
    engine: Engine, clock: dict[str, dt.datetime], ticks: list[dt.datetime]
) -> WatchReport:
    state = {"i": 0}

    def now() -> dt.datetime:
        clock["t"] = ticks[min(state["i"], len(ticks) - 1)]
        return clock["t"]

    def sleep(_seconds: float) -> None:
        state["i"] += 1

    return watch_until_kickoff(
        engine,
        matches=_LINE,
        plan=WatchPlan(kickoff=KICKOFF, retry_every=dt.timedelta(minutes=5)),
        now=now,
        sleep=sleep,
        max_attempts=3,
    )


# --------------------------------------------------------------------------- #
# 1. La fraîcheur du suivi, sur le chemin réellement emprunté
# --------------------------------------------------------------------------- #


def _watch_with_ttl(ttl: float) -> tuple[list[int], int]:
    """Run the scenario once at one cache freshness; return sheets and calls."""
    ticks = [
        KICKOFF - dt.timedelta(minutes=75),
        KICKOFF - dt.timedelta(minutes=60),
        KICKOFF - dt.timedelta(minutes=55),
    ]
    clock = {"t": ticks[0]}
    api = FakeApi(publishes_at=KICKOFF - dt.timedelta(minutes=60), clock=clock)
    with tempfile.TemporaryDirectory() as folder:
        engine = _engine(Cache(folder, ttl_seconds=ttl), clock, api)
        report = _run_watch(engine, clock, ticks)
    return ([attempt.sheets for attempt in report.attempts], len(api.sheet_calls()))


def test_h1_the_watch_only_sees_a_late_sheet_with_a_watch_freshness() -> None:
    """Compositions publiées à 19:45 : lues à 19:45 seulement si le cache suit.

    La même scène, aux deux fraîcheurs, dans le même test : c'est la comparaison
    qui prouve que le réglage est la cause, et non le hasard du scénario.
    """
    stale, stale_calls = _watch_with_ttl(6 * 3600.0)
    assert max(stale) == 0, (
        f"défaut reproduit : le cache de six heures ressert la réponse vide "
        f"({stale})"
    )
    assert stale_calls == 1, "une seule requête pour trois contrôles"

    fresh, fresh_calls = _watch_with_ttl(WATCH_CACHE_TTL)
    assert fresh[0] == 0, "rien n'est publié à 19:30"
    assert max(fresh) == 22, f"la feuille de 19:45 doit être vue : {fresh}"
    assert fresh_calls >= 2, "chaque contrôle interroge vraiment"


def test_h1b_the_suivre_button_uses_the_watch_freshness() -> None:
    """Le Supervisor du serveur web doit recevoir le moteur du suivi."""
    with tempfile.TemporaryDirectory() as folder:
        ordinary = _engine(Cache(folder, ttl_seconds=6 * 3600.0))
        watching = _engine(Cache(folder, ttl_seconds=WATCH_CACHE_TTL))
        supervisor = Supervisor(ordinary, watch_engine=watching)
        seen: list[Engine] = []

        started = threading.Event()

        def capture(handle: WatchHandle) -> None:
            # Records the engine the supervisor really hands to a watch.
            assert handle.matches == _LINE
            seen.append(supervisor._watch_engine)
            started.set()

        supervisor.start(matches=_LINE, kickoff=KICKOFF, runner=capture)
        assert started.wait(timeout=5)
        assert seen and seen[0] is watching, (
            "le bouton Suivre doit passer par le moteur à fraîcheur de suivi"
        )


def test_h1c_the_cli_and_the_server_build_the_same_watch_engine() -> None:
    """Une politique commune, pas deux réglages qui peuvent diverger."""
    with tempfile.TemporaryDirectory() as folder:
        for argv in (
            ["suivre", "--coup-denvoi", "2026-09-14T20:45", "--cache", folder],
            ["web", "--cache", folder],
        ):
            args = build_parser().parse_args(argv)
            engine = _watch_engine(args)
            built: list[object] = list(engine.providers)
            caches = [
                cache
                for provider in built
                if (cache := getattr(provider, _CACHE, None)) is not None
            ]
            assert caches, argv[0]
            assert all(c.ttl_seconds == WATCH_CACHE_TTL for c in caches), argv[0]


# --------------------------------------------------------------------------- #
# 2. Un suivi armé longtemps à l'avance attend vraiment
# --------------------------------------------------------------------------- #


def test_h2_a_watch_armed_five_hours_early_checks_at_t75_not_an_hour_later() -> None:
    """Lancement 15:45 pour un match à 20:45 : premier contrôle à 19:30."""
    # "it.1" because the real adapter only serves the competitions it maps;
    # a made-up key would have it refuse before any cache was consulted.
    fixtures = [Fixture("Club A", "Club B", dt.date(2026, 9, 14), competition="it.1")]
    engine = Engine(
        Registry([StubProvider(_controlled_history(), fixtures)]),
        config=EngineConfig(seasons=("2026-27",), min_matches=40),
    )
    now = {"t": KICKOFF - dt.timedelta(hours=5)}
    seen: list[tuple[dt.datetime, dt.datetime | None]] = []

    def record(attempt: object, result: object) -> None:
        quoted = next(
            (
                a.decision.main.priced.quoted_at
                for a in result.run.analyses  # type: ignore[attr-defined]
                if a.decision and a.decision.main
            ),
            None,
        )
        seen.append((attempt.at, quoted))  # type: ignore[attr-defined]

    watch_until_kickoff(
        engine,
        matches=f"{_LINE} @ 2.10 3.40 6.00",
        plan=WatchPlan(kickoff=KICKOFF),
        now=lambda: now["t"],
        # The real sleeper caps each wait at an hour; reproduce that exactly.
        sleep=lambda s: now.__setitem__(
            "t", now["t"] + dt.timedelta(seconds=min(s, 3600.0))
        ),
        max_attempts=2,
        on_attempt=record,
    )
    assert seen, "le suivi doit finir par contrôler"
    assert seen[0][0] == KICKOFF - dt.timedelta(minutes=75), (
        f"premier contrôle à {seen[0][0]:%H:%M} au lieu de 19:30"
    )
    launched = KICKOFF - dt.timedelta(hours=5)
    for _at, quoted in seen:
        assert quoted == launched, (
            f"la cote doit garder l'heure de saisie ({launched:%H:%M}), pas {quoted}"
        )


# --------------------------------------------------------------------------- #
# 3. Le bouton Bilan calcule réellement
# --------------------------------------------------------------------------- #


def test_h3_the_bilan_button_runs_a_measurement_on_the_server() -> None:
    """Afficher le journal et renvoyer vers un terminal n'est pas un bilan."""
    release = threading.Event()
    steps: list[str] = []

    def slow_measurement(job: LedgerJob) -> str:
        with job.lock:
            job.progress = "résultats 1/1 — it.1"
        steps.append("started")
        release.wait(timeout=5)
        return "BILAN : 12 rencontres résolues"

    supervisor = Supervisor(_engine(Cache(tempfile.mkdtemp())))
    job = supervisor.measure_async(slow_measurement)
    assert _wait_for(lambda: bool(steps))
    assert steps, "la mesure doit démarrer sans bloquer la requête"

    finished, progress, _report, _error = job.snapshot()
    assert not finished
    assert "résultats 1/1" in progress, "l'avancement est lisible pendant le calcul"

    release.set()
    assert _wait_for(lambda: job.snapshot()[0])
    finished, _progress, report, error = job.snapshot()
    assert finished and not error
    assert "12 rencontres résolues" in report


def test_h3b_a_second_press_does_not_start_a_second_measurement() -> None:
    release = threading.Event()
    starts: list[int] = []

    def slow(job: LedgerJob) -> str:
        starts.append(id(job))
        release.wait(timeout=5)
        return "fini"

    supervisor = Supervisor(_engine(Cache(tempfile.mkdtemp())))
    first = supervisor.measure_async(slow)
    second = supervisor.measure_async(slow)
    assert first is second, "rappuyer consulte l'avancement, ne relance pas le calcul"
    release.set()
    assert _wait_for(lambda: first.snapshot()[0])
    assert len(starts) == 1


def test_h3c_a_failing_measurement_says_so_instead_of_staying_silent() -> None:
    def explode(job: LedgerJob) -> str:
        raise RuntimeError(f"source injoignable ({job.progress})")

    supervisor = Supervisor(_engine(Cache(tempfile.mkdtemp())))
    job = supervisor.measure_async(explode)
    assert _wait_for(lambda: job.snapshot()[0])
    finished, _progress, _report, error = job.snapshot()
    assert finished and "source injoignable" in error


def test_h3d_the_bilan_screen_reports_the_measurement_state() -> None:
    supervisor = Supervisor(_engine(Cache(tempfile.mkdtemp())))
    with tempfile.TemporaryDirectory() as folder:
        book = ForecastBook(Path(folder) / "journal.jsonl")
        book.append(_forecast("Club A", "Club B", market_key="1X2:H", odds=2.0))
        idle = _render_ledger(book, supervisor)
        assert "Aucune mesure lancée" in idle

        running = supervisor.measure_async(lambda _job: "BILAN : rien à mesurer")
        assert _wait_for(lambda: running.snapshot()[0])
        assert "Bilan mesuré" in _render_ledger(book, supervisor)


# --------------------------------------------------------------------------- #
# 4. Les clôtures, identifiées correctement
# --------------------------------------------------------------------------- #


class ArchiveDouble:
    """A closing-price archive that records exactly what it was asked for."""

    def __init__(self) -> None:
        self.asked: list[tuple[str, str]] = []

    @property
    def name(self) -> str:
        return "archive-doublure"

    def competitions(self) -> tuple[str, ...]:
        return ("E0", "I1", "SP1")

    def odds(
        self, competition: str, season: str
    ) -> tuple[dict[Fixture, MatchOdds], list[Evidence]]:
        self.asked.append((competition, season))
        if competition != "I1":
            return ({}, [])
        fixture = Fixture(
            "Napoli", "Bologna", dt.date(2026, 9, 13), competition="it.1"
        )
        return ({fixture: MatchOdds(1.90, 4.00, 5.50, bookmaker="Betclic")}, [])


def test_h4_the_archive_is_asked_for_the_competition_and_season_requested() -> None:
    """Appeler odds() sans argument laissait l'archive répondre E0 / 2024-25."""
    archive = ArchiveDouble()
    prices = load_closing_from_sources(
        [archive], competitions=["it.1"], seasons=["2026-27"]
    )
    assert archive.asked == [("I1", "2026-27")], archive.asked
    assert prices.state is ClosingState.SERVED
    key = closing_key(
        "it.1", "Napoli", "Bologna", dt.date(2026, 9, 13), "1X2:H", "Betclic"
    )
    assert prices.get(key) == 1.90, "et l'identité correspond à celle du journal"


def test_h4b_a_competition_the_archive_does_not_serve_is_not_requested() -> None:
    archive = ArchiveDouble()
    load_closing_from_sources([archive], competitions=["nl.1"], seasons=["2026-27"])
    assert archive.asked == [], (
        "demander une compétition non servie ferait répondre la division par défaut"
    )


def test_h4c_two_bookmakers_on_one_market_do_not_overwrite_each_other() -> None:
    """Une clôture Betclic à 1,90 était remplacée par un autre livre à 2,80."""
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "clotures.csv"
        path.write_text(
            "date,home,away,marche,cote,bookmaker,competition\n"
            "13/09/2026,Napoli,Bologna,1X2:H,1.90,Betclic,it.1\n"
            "13/09/2026,Napoli,Bologna,1X2:H,2.80,AutreLivre,it.1\n",
            encoding="utf-8",
        )
        prices = load_closing_csv(path)
        betclic = closing_key(
            "it.1", "Napoli", "Bologna", dt.date(2026, 9, 13), "1X2:H", "Betclic"
        )
        other = closing_key(
            "it.1", "Napoli", "Bologna", dt.date(2026, 9, 13), "1X2:H", "AutreLivre"
        )
        assert prices.get(betclic) == 1.90
        assert prices.get(other) == 2.80
        reference = closing_key(
            "it.1", "Napoli", "Bologna", dt.date(2026, 9, 13), "1X2:H"
        )
        assert prices.get(reference) is None, (
            "deux livres en désaccord ne font pas une référence de marché"
        )


def test_h4d_the_comparison_says_which_book_it_compared_to() -> None:
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "clotures.csv"
        path.write_text(
            f"date,home,away,marche,cote,bookmaker,competition\n"
            f"14/09/2026,Club A,Club B,1X2:H,1.90,Betclic,{_COMP}\n",
            encoding="utf-8",
        )
        prices = load_closing_csv(path)
        same = _book_one(bookmaker="Betclic")
        report = measure(
            same, results=[_played("Club A", "Club B", (1, 0))], closing=prices
        )
        assert report.resolved[0].closing_book.startswith("même bookmaker")
        assert "même bookmaker" in report.render()

        elsewhere = _book_one(bookmaker="Unibet")
        other = measure(
            elsewhere, results=[_played("Club A", "Club B", (1, 0))], closing=prices
        )
        assert other.resolved[0].closing_book.startswith("référence de marché")


def test_h4e_opening_columns_are_never_served_as_closing_prices() -> None:
    """B365H est une cote d'ouverture ; la clôture est B365CH."""
    opening_only = [{"Date": "13/09/26", "B365H": "1.90", "B365D": "4.0", "B365A": "5.5"}]
    assert _closing_columns("B365", opening_only) is None
    recent = [
        {
            "Date": "13/09/26",
            "B365H": "1.90",
            "B365CH": "1.85",
            "B365CD": "4.2",
            "B365CA": "5.8",
        }
    ]
    assert _closing_columns("B365", recent) == ("B365CH", "B365CD", "B365CA")


def test_h4f_the_archive_returns_our_competition_keys() -> None:
    """« Serie A (Italie) » ne s'apparie à aucune clé de journal."""
    assert COMPETITION_KEYS["I1"] == "it.1"
    assert DIVISION_FOR_KEY["it.1"] == "I1"
    for code, key in COMPETITION_KEYS.items():
        assert DIVISION_FOR_KEY[key] == code


def _book_one(*, bookmaker: str) -> ForecastBook:
    """A one-line journal whose forecast names the book it took its price at."""
    book = ForecastBook(Path(tempfile.mkdtemp()) / "journal.jsonl")
    book.append(
        dataclasses.replace(
            _forecast("Club A", "Club B", market_key="1X2:H", odds=2.05),
            bookmaker=bookmaker,
        )
    )
    return book
