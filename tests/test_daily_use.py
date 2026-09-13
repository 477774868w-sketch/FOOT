"""L'usage quotidien : suivi des compositions, journal, compositions collectées.

Trois parcours que l'opérateur suit réellement, et qui n'existaient pas avant :

* le contrôle T−75/T−60 **exécuté**, avec ses nouvelles tentatives jusqu'au coup
  d'envoi — horloge et attente injectées, pour que la boucle entière s'exécute en
  microsecondes plutôt qu'en une heure ;
* le journal des prévisions, en ajout seul : une révision s'ajoute, rien ne se
  réécrit ;
* les feuilles de composition **collectées automatiquement**, qui doivent
  traverser exactement le même filtre de disponibilité que celles collées à la
  main — sinon une source automatique verrait plus loin dans le futur qu'un
  opérateur.

Aucun réseau, aucune clé : les sources sont des doublures qui rendent des
réponses fixes, comme le ferait une réponse enregistrée.
"""

from __future__ import annotations

import datetime as dt
import os
import socketserver
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from zoneinfo import ZoneInfo

from test_acceptance import _COMP, StubProvider, _controlled_history

from foot.analysis.engine import Engine, EngineConfig
from foot.analysis.journey import run_journey
from foot.analysis.ledgerbook import Forecast, ForecastBook, record_run
from foot.analysis.watch import WatchPlan, due_soon, watch_until_kickoff
from foot.collect.base import Capability, LineupSource
from foot.collect.footballdata_org import CREDENTIAL
from foot.collect.registry import Registry
from foot.collect.supplements import LineupRow, SupplementSet
from foot.domain import Fixture
from foot.provenance import Confidence, Evidence, Source, utcnow
from foot.report.card import render_card
from foot.report.web import make_handler, render_form

PARIS = ZoneInfo("Europe/Paris")
KICKOFF = dt.datetime(2026, 9, 14, 20, 45, tzinfo=PARIS)
_LINE = "Club A - Club B 14/09/2026 20:45"


def _fixtures() -> list[Fixture]:
    return [Fixture("Club A", "Club B", dt.date(2026, 9, 14), competition=_COMP)]


class SheetSource:
    """A lineup source that publishes its sheet at a chosen instant.

    Two knobs, because they are the two things that actually go wrong on a match
    day: the sheet is not out yet, and the plan does not serve sheets at all.
    """

    def __init__(
        self,
        *,
        publishes_at: dt.datetime | None,
        clock: Sequence[dt.datetime] | None = None,
        players: int = 11,
    ) -> None:
        self._publishes_at = publishes_at
        self._clock = list(clock or [])
        self._players = players
        self.calls = 0

    @property
    def name(self) -> str:
        return "doublure-compositions"

    @property
    def capabilities(self) -> frozenset[Capability]:
        return frozenset({Capability.LINEUPS})

    def team_sheets(
        self, fixture: Fixture
    ) -> tuple[tuple[LineupRow, ...], tuple[Evidence, ...]]:
        self.calls += 1
        now = self._clock[min(self.calls - 1, len(self._clock) - 1)] if self._clock else None
        published = self._publishes_at
        if published is None or (now is not None and now < published):
            return ((), ())
        rows = tuple(
            LineupRow(
                date=fixture.date,
                published_at=published,
                team=team,
                player=f"{team} joueur {index}",
                role="gardien" if index == 1 else "milieu",
                starting=True,
                opponent=fixture.away if team == fixture.home else fixture.home,
                source="doublure-compositions",
                status=Confidence.CONFIRMED,
            )
            for team in (fixture.home, fixture.away)
            for index in range(1, self._players + 1)
        )
        note = Evidence(
            key="composition::doublure",
            value=f"{len(rows)} joueur(s)",
            source=Source(name="doublure", provider="test", url="", official=False),
            retrieved_at=published,
            status=Confidence.CONFIRMED,
            fact_date=fixture.date,
        )
        return (rows, (note,))


def _engine(*extra: object) -> Engine:
    stub = StubProvider(_controlled_history(), _fixtures())
    return Engine(
        Registry([stub, *extra]),  # type: ignore[list-item]
        config=EngineConfig(seasons=("2026-27",), min_matches=40),
    )


# --------------------------------------------------------------------------- #
# 1. Le contrôle T−75/T−60 est réellement exécuté
# --------------------------------------------------------------------------- #


def test_the_watch_retries_until_the_sheet_is_published() -> None:
    """Une feuille absente à T−75 doit être retrouvée quelques minutes plus tard."""
    published = KICKOFF - dt.timedelta(minutes=52)
    source = SheetSource(publishes_at=published)
    plan = WatchPlan(kickoff=KICKOFF, retry_every=dt.timedelta(minutes=5))
    ticks = iter(plan.due_times())
    current = {"now": plan.due_times()[0]}

    def clock() -> dt.datetime:
        return current["now"]

    def sleep(_seconds: float) -> None:
        current["now"] = next(ticks, KICKOFF)

    # The first tick is consumed by the initial position of the clock.
    next(ticks)
    report = watch_until_kickoff(
        _engine(source),
        matches=_LINE,
        plan=plan,
        now=clock,
        sleep=sleep,
    )
    assert source.calls >= 3, "la boucle doit réessayer après un T−75 infructueux"
    success = report.last_success
    assert success is not None, "la feuille publiée à T−52 doit finir par être vue"
    assert success.sheets == 22
    assert success.at >= published
    assert "DERNIÈRE VÉRIFICATION RÉUSSIE" in report.render()
    assert report.stopped_because


def test_a_watch_that_never_finds_a_sheet_says_so_instead_of_inventing_one() -> None:
    """« aucune vérification réussie » est une information, pas un silence."""
    plan = WatchPlan(kickoff=KICKOFF, retry_every=dt.timedelta(minutes=20))
    current = {"now": KICKOFF - dt.timedelta(minutes=75)}

    def clock() -> dt.datetime:
        return current["now"]

    def sleep(seconds: float) -> None:
        current["now"] = current["now"] + dt.timedelta(seconds=max(seconds, 60))

    report = watch_until_kickoff(
        _engine(SheetSource(publishes_at=None)),
        matches=_LINE,
        plan=plan,
        now=clock,
        sleep=sleep,
    )
    assert report.last_success is None
    rendered = report.render()
    assert "DERNIÈRE VÉRIFICATION RÉUSSIE : aucune" in rendered
    assert "n'ont pas été publiées" in rendered
    assert report.attempts, "les tentatives infructueuses restent inscrites"


def test_the_watch_stops_at_kickoff() -> None:
    """Une lecture prématch ne doit jamais continuer après le coup d'envoi."""
    plan = WatchPlan(kickoff=KICKOFF)
    report = watch_until_kickoff(
        _engine(),
        matches=_LINE,
        plan=plan,
        now=lambda: KICKOFF + dt.timedelta(minutes=1),
        sleep=lambda _s: None,
    )
    assert report.attempts == []
    assert report.stopped_because == "coup d'envoi passé"


def test_the_due_times_cover_the_whole_window_without_overrunning_it() -> None:
    plan = WatchPlan(kickoff=KICKOFF, retry_every=dt.timedelta(minutes=10))
    moments = plan.due_times()
    assert moments[0] == KICKOFF - dt.timedelta(minutes=75)
    assert moments[1] == KICKOFF - dt.timedelta(minutes=60)
    assert all(moment < KICKOFF for moment in moments)
    assert due_soon(plan, now=KICKOFF - dt.timedelta(minutes=76))
    assert not due_soon(plan, now=KICKOFF - dt.timedelta(hours=4))


def test_a_watched_run_says_a_check_is_running_and_a_one_shot_does_not() -> None:
    """« suivi en cours » ne doit apparaître que pendant un suivi réel."""
    engine = _engine(SheetSource(publishes_at=None))
    single = engine.run(_LINE, as_of=KICKOFF - dt.timedelta(days=1))
    plan = single.analyses[0].lineup_plan
    assert plan is not None
    assert "ne lance pas le suivi" in plan.mechanism_line()

    watched = engine.run(
        _LINE, as_of=KICKOFF - dt.timedelta(minutes=70), watching=True
    )
    live = watched.analyses[0].lineup_plan
    assert live is not None
    assert "suivi en cours" in live.mechanism_line()


def test_without_a_sheet_source_the_report_refuses_to_promise_a_check() -> None:
    run = _engine().run(_LINE, as_of=KICKOFF - dt.timedelta(days=1))
    plan = run.analyses[0].lineup_plan
    assert plan is not None
    line = plan.mechanism_line()
    assert "AUCUN automatisme actif" in line
    assert "--compositions-csv" in line


# --------------------------------------------------------------------------- #
# 2. Les compositions collectées passent par la même porte que les collées
# --------------------------------------------------------------------------- #


def test_a_collected_sheet_reaches_the_lineup_plan() -> None:
    source = SheetSource(publishes_at=KICKOFF - dt.timedelta(minutes=70))
    run = _engine(source).run(_LINE, as_of=KICKOFF - dt.timedelta(minutes=65))
    plan = run.analyses[0].lineup_plan
    assert plan is not None
    assert source.calls == 1
    assert sum(o.starters for o in plan.observations) == 22
    assert "À FAIRE" not in plan.state()


def test_a_collected_sheet_published_after_the_analysis_is_not_used() -> None:
    """Une source automatique ne doit pas voir plus loin qu'un opérateur."""
    source = SheetSource(publishes_at=KICKOFF - dt.timedelta(minutes=70))
    run = _engine(source).run(_LINE, as_of=KICKOFF - dt.timedelta(hours=6))
    plan = run.analyses[0].lineup_plan
    assert plan is not None
    assert source.calls == 1, "la source est bien interrogée"
    assert not plan.observations, "mais sa feuille est postérieure à l'analyse"
    assert "À FAIRE" in plan.state()


def test_a_source_that_raises_does_not_stop_the_analysis() -> None:
    class Broken:
        @property
        def name(self) -> str:
            return "cassée"

        @property
        def capabilities(self) -> frozenset[Capability]:
            return frozenset({Capability.LINEUPS})

        def team_sheets(self, fixture: Fixture) -> tuple[tuple[()], tuple[()]]:
            raise RuntimeError(f"503 du fournisseur pour {fixture.home}")

    run = _engine(Broken()).run(_LINE, as_of=KICKOFF - dt.timedelta(days=1))
    assert run.analyses[0].analysed, "une source cassée ne condamne pas la rencontre"


def test_an_operator_sheet_keeps_the_tie_against_a_collected_one() -> None:
    """Une feuille saisie à la main est un acte délibéré : à heure égale, elle tient."""
    published = KICKOFF - dt.timedelta(minutes=70)
    typed = LineupRow(
        date=dt.date(2026, 9, 14),
        published_at=published,
        team="Club A",
        player="Club A joueur 1",
        role="gardien",
        starting=True,
        opponent="Club B",
        source="saisie opérateur",
        status=Confidence.CONFIRMED,
    )
    source = SheetSource(publishes_at=published)
    run = _engine(source).run(
        _LINE,
        as_of=KICKOFF - dt.timedelta(minutes=65),
        supplements=SupplementSet(lineups=(typed,)),
    )
    plan = run.analyses[0].lineup_plan
    assert plan is not None
    home = next(o for o in plan.observations if o.team == "Club A")
    assert home.source == "saisie opérateur", (
        "à heure de publication égale, la feuille de l'opérateur n'est pas remplacée"
    )
    assert home.starters == 1, "et elle n'est pas complétée par une autre source"
    away = next(o for o in plan.observations if o.team == "Club B")
    assert away.starters == 11, "l'autre équipe, elle, est bien collectée"


def test_the_protocol_contract_is_what_the_engine_depends_on() -> None:
    assert isinstance(SheetSource(publishes_at=None), LineupSource)


# --------------------------------------------------------------------------- #
# 3. Le journal des prévisions
# --------------------------------------------------------------------------- #


def test_a_forecast_survives_a_round_trip_through_the_journal() -> None:
    with tempfile.TemporaryDirectory() as folder:
        book = ForecastBook(Path(folder) / "journal.jsonl")
        as_of = KICKOFF - dt.timedelta(days=1)
        result = run_journey(_engine(), matches=_LINE, as_of=as_of)
        written = record_run(result.run.analyses, book=book, as_of=as_of)
        assert len(written) == 1
        (back,) = list(book)
        assert back.home == "Club A"
        assert back.fingerprint == written[0].fingerprint
        assert abs(sum(back.probabilities) - 1.0) < 1e-9
        assert back.model_version


def test_a_revision_is_appended_and_never_replaces_the_first_forecast() -> None:
    """Mesurer une prévision exige qu'elle ne bouge plus après coup."""
    with tempfile.TemporaryDirectory() as folder:
        book = ForecastBook(Path(folder) / "journal.jsonl")
        first_at = KICKOFF - dt.timedelta(days=1)
        first = run_journey(_engine(), matches=_LINE, as_of=first_at)
        record_run(first.run.analyses, book=book, as_of=first_at, reason="J−1")

        later_at = KICKOFF - dt.timedelta(minutes=65)
        second = run_journey(
            _engine(), matches=f"{_LINE} @ 2.10 3.40 6.00", as_of=later_at
        )
        record_run(second.run.analyses, book=book, as_of=later_at, reason="cotes")

        history = book.history_for((_COMP, "Club A", "Club B"))
        assert len(history) == 2, "la révision s'ajoute, elle ne remplace pas"
        assert history[0].reason == "J−1"
        assert history[1].reason == "cotes"
        assert history[1].supersedes == history[0].fingerprint
        assert not history[1].confirms
        assert history[0].fingerprint != history[1].fingerprint
        latest = book.latest_for((_COMP, "Club A", "Club B"))
        assert latest is not None and latest.reason == "cotes"


def test_an_unchanged_call_is_recorded_as_confirmed_not_as_a_revision() -> None:
    """Un dossier est daté : son empreinte bouge à chaque relecture.

    Appeler cela « révision » ferait passer un contrôle de routine à T−60 pour un
    changement d'avis, et rendrait le journal illisible là où il compte le plus.
    """
    with tempfile.TemporaryDirectory() as folder:
        book = ForecastBook(Path(folder) / "journal.jsonl")
        line = f"{_LINE} @ 2.10 3.40 6.00"
        first_at = KICKOFF - dt.timedelta(minutes=75)
        second_at = KICKOFF - dt.timedelta(minutes=60)
        for moment, why in ((first_at, "T−75"), (second_at, "T−60")):
            result = run_journey(_engine(), matches=line, as_of=moment)
            record_run(result.run.analyses, book=book, as_of=moment, reason=why)

        history = book.history_for((_COMP, "Club A", "Club B"))
        assert len(history) == 2
        assert history[0].fingerprint != history[1].fingerprint, (
            "l'empreinte du dossier daté bouge, c'est attendu"
        )
        assert history[1].confirms == history[0].fingerprint
        assert not history[1].supersedes
        assert "(inchangé)" in book.render()
        assert "(révision)" not in book.render()


def test_the_journal_has_no_way_to_edit_or_delete_a_line() -> None:
    """L'absence d'écriture arrière est une propriété, pas un oubli."""
    for forbidden in ("update", "delete", "replace", "edit", "remove"):
        assert not hasattr(ForecastBook, forbidden)


def test_a_match_that_could_not_be_analysed_is_not_journalled() -> None:
    """Une prévision vide polluerait la calibration : on n'en écrit pas."""
    with tempfile.TemporaryDirectory() as folder:
        book = ForecastBook(Path(folder) / "journal.jsonl")
        as_of = KICKOFF - dt.timedelta(days=1)
        result = run_journey(
            _engine(), matches="Club Inconnu - Autre Inconnu", as_of=as_of
        )
        written = record_run(result.run.analyses, book=book, as_of=as_of)
        assert written == ()
        assert len(book) == 0


def test_the_journal_keeps_the_price_and_its_real_hour() -> None:
    """Mesurer une décision exige de savoir à quel prix elle a été prise."""
    with tempfile.TemporaryDirectory() as folder:
        book = ForecastBook(Path(folder) / "journal.jsonl")
        as_of = KICKOFF - dt.timedelta(hours=6)
        quoted = as_of - dt.timedelta(hours=2)
        result = run_journey(
            _engine(),
            matches=f"{_LINE} @ 2.10 3.40 3.60",
            as_of=as_of,
            bookmaker="Doublure",
            quoted_at=quoted,
        )
        (entry,) = record_run(result.run.analyses, book=book, as_of=as_of)
        if entry.odds is not None:
            assert entry.bookmaker == "Doublure"
            assert entry.quoted_at.startswith(quoted.isoformat()[:16])
        assert entry.decision, "la décision est toujours consignée, même « aucun pari »"


def test_a_corrupt_journal_line_does_not_crash_the_measurement() -> None:
    line = Forecast(
        recorded_at=utcnow(),
        as_of=utcnow(),
        competition=_COMP,
        home="Club A",
        away="Club B",
        kickoff="",
        fingerprint="abc",
        probabilities=(0.4, 0.3, 0.3),
        expected_goals=(1.4, 1.1),
        model_version="x",
    ).to_json()
    assert Forecast.from_json(line).probabilities == (0.4, 0.3, 0.3)
    truncated = line.replace('"probabilities": [0.4, 0.3, 0.3]', '"probabilities": [0.4]')
    assert Forecast.from_json(truncated).probabilities == (0.4, 0.0, 0.0)


# --------------------------------------------------------------------------- #
# 4. L'écran du téléphone : accès privé, sauvegarde, clés côté serveur
# --------------------------------------------------------------------------- #


def _open(url: str, *, data: bytes | None = None, cookie: str = "") -> tuple[int, str, str]:
    """One request against a live local server — status, body, Set-Cookie."""
    request = urllib.request.Request(url, data=data)
    if cookie:
        request.add_header("Cookie", cookie)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return (
                response.status,
                response.read().decode("utf-8"),
                response.headers.get("Set-Cookie", "") or "",
            )
    except urllib.error.HTTPError as error:
        return (error.code, error.read().decode("utf-8"), "")


@contextmanager
def _running(**kwargs: object) -> Iterator[str]:
    """Serve the interface on a free local port for the duration of a test."""
    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    handler = make_handler(_engine(), **kwargs)  # type: ignore[arg-type]
    with Server(("127.0.0.1", 0), handler) as httpd:
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{httpd.server_address[1]}"
        finally:
            httpd.shutdown()
            thread.join(timeout=5)


def test_the_first_screen_is_matches_date_bookmaker_and_one_button() -> None:
    """Le téléphone doit montrer l'essentiel, pas le formulaire entier."""
    page = render_form()
    order = [
        page.index('id="matchs"'),
        page.index('id="date"'),
        page.index('id="book"'),
        page.index('<button type="submit">'),
    ]
    assert order == sorted(order), "l'ordre de l'écran suit le geste de l'opérateur"
    assert page.count('<button type="submit">') == 1
    assert page.index('<button type="submit">') < page.index('id="budget"'), (
        "budget, combiné et contexte sont repliés sous le bouton"
    )


def test_without_a_token_the_interface_stays_open() -> None:
    with _running() as base:
        status, body, _cookie = _open(base + "/")
        assert status == 200
        assert "Rencontres à analyser" in body


def test_a_private_interface_refuses_a_request_without_the_token() -> None:
    with _running(token="jeton-de-test") as base:
        status, body, _ = _open(base + "/")
        assert status == 403
        assert "Accès privé" in body

        status, _body, _ = _open(base + "/?jeton=mauvais")
        assert status == 403


def test_the_token_travels_once_in_the_address_then_in_a_cookie() -> None:
    with _running(token="jeton-de-test") as base:
        status, body, cookie = _open(base + "/?jeton=jeton-de-test")
        assert status == 200
        assert "Rencontres à analyser" in body
        assert "foot_acces=jeton-de-test" in cookie
        assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
        assert "jeton-de-test" not in body, "le jeton n'est jamais rendu dans la page"

        status, _body, _ = _open(base + "/", cookie="foot_acces=jeton-de-test")
        assert status == 200


def test_an_analysis_served_to_the_phone_is_kept_on_the_server() -> None:
    """Le téléphone ne conserve rien : la sauvegarde est côté serveur."""
    with tempfile.TemporaryDirectory() as folder:
        book = ForecastBook(Path(folder) / "journal.jsonl")
        with _running(book=book) as base:
            form = urllib.parse.urlencode(
                {
                    "matchs": _LINE,
                    "date": "2026-09-13T12:00",
                    "tz": "Europe/Paris",
                    "book": "",
                    "budget": "",
                    "combine": "non",
                }
            ).encode()
            status, body, _ = _open(base + "/", data=form)
        assert status == 200
        assert "prévision(s) conservée(s) côté serveur" in body
        assert len(book) == 1


def test_no_api_key_can_reach_a_rendered_page() -> None:
    """Les clés vivent dans l'environnement du serveur, jamais dans le HTML."""
    secret = "clé-secrète-de-test-0123456789"
    previous = os.environ.get(CREDENTIAL)
    os.environ[CREDENTIAL] = secret
    try:
        with _running() as base:
            _status, body, _ = _open(base + "/")
        assert secret not in body
        assert CREDENTIAL not in body
    finally:
        if previous is None:
            os.environ.pop(CREDENTIAL, None)
        else:
            os.environ[CREDENTIAL] = previous


# --------------------------------------------------------------------------- #
# 5. Le prix retenu, son livre et son heure
# --------------------------------------------------------------------------- #


def test_the_chosen_price_carries_its_bookmaker_its_hour_and_its_floor() -> None:
    """Un prix sans livre ni heure ne peut pas être repris — ni mesuré ensuite."""
    as_of = dt.datetime(2026, 9, 13, 12, 0, tzinfo=PARIS)
    result = run_journey(
        _engine(),
        matches=f"{_LINE} @ 2.10 3.40 6.00",
        as_of=as_of,
        bookmaker="Doublure",
    )
    card = render_card(result.run.analyses[0])
    if "PARI PRINCIPAL" in card:
        assert "chez Doublure" in card
        assert "relevée 13/09 12:00" in card
        assert "cote minimale" in card
        floor = next(
            line for line in card.splitlines() if "cote minimale" in line
        )
        assert "ne remplit plus les critères déclarés" in floor


def test_a_price_from_another_book_stays_labelled_all_the_way_to_the_card() -> None:
    """« ≠ demandé » doit survivre jusqu'à l'écran, pas mourir dans l'adaptateur."""
    as_of = dt.datetime(2026, 9, 13, 12, 0, tzinfo=PARIS)
    result = run_journey(
        _engine(),
        matches=f"{_LINE} @ 2.10 3.40 6.00",
        as_of=as_of,
        bookmaker="Pinnacle (≠ Betclic demandé)",
    )
    card = render_card(result.run.analyses[0])
    if "PARI PRINCIPAL" in card:
        assert "≠ Betclic demandé" in card
