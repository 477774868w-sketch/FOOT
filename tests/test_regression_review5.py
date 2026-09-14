"""Régressions issues de la cinquième revue indépendante, commit `74d1fa3`.

Chaque test de ce fichier **échoue sur `74d1fa3`** et passe ici. Les reproductions
sont celles de la revue, aux mêmes valeurs :

1. une cote saisie une fois, redatée à 19:30, 19:45 puis 19:50 — trois âges nuls ;
2. une réponse sans composition levant une ``ValueError`` que le moteur avalait ;
3. un suivi qui s'arrête sur deux joueurs, un par équipe, en annonçant
   « composition officielle » — et qui saute alors le contrôle T−60 ;
4. deux rencontres entre les mêmes équipes, les 14 et 21 septembre, réduites à une ;
5. ``measure()`` évaluant une prévision écrite après le match ;
6. ``offer_for_key("OU:4.5:under")`` renvoyant ``OU:4.5:over`` ;
7. ``AH:H:-1.75`` et ``OU:4.5:over`` disparaissant du bilan ;
8. « Manchester United FC » ne trouvant plus le prix de « Manchester United » ;
9. ``closing`` initialisé à ``{}`` et jamais alimenté.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import io
import tempfile
import threading
from contextlib import redirect_stdout
from pathlib import Path
from zoneinfo import ZoneInfo

from test_acceptance import _COMP, StubProvider, _controlled_history
from test_daily_use import SheetSource, _forecast, _played
from test_live_integrations import _odds_payload

from foot.analysis.closing import load_closing_csv
from foot.analysis.engine import Engine, EngineConfig
from foot.analysis.ledgerbook import Forecast, ForecastBook
from foot.analysis.measure import ClosingState, closing_key, measure
from foot.analysis.quotes import offer_for_key
from foot.analysis.supervisor import Supervisor, WatchHandle, WatchStore
from foot.analysis.watch import (
    WATCH_CACHE_TTL,
    WatchPlan,
    WatchReport,
    watch_until_kickoff,
)
from foot.cli import build_parser, command_mesurer
from foot.collect.base import Capability
from foot.collect.cache import Cache
from foot.collect.footballdata_org import FootballDataOrgProvider
from foot.collect.oddsapi import _quotes_for, parse_odds
from foot.collect.registry import Registry
from foot.domain import Fixture
from foot.provenance import Confidence
from foot.report.card import render_rubric_provenance
from foot.report.web import _render_ledger

PARIS = ZoneInfo("Europe/Paris")
KICKOFF = dt.datetime(2026, 9, 14, 20, 45, tzinfo=PARIS)
UTC = dt.timezone.utc
_LINE = "Club A - Club B 14/09/2026 20:45"


def _fixtures() -> list[Fixture]:
    return [Fixture("Club A", "Club B", dt.date(2026, 9, 14), competition=_COMP)]


def _engine(*extra: object) -> Engine:
    return Engine(
        Registry([StubProvider(_controlled_history(), _fixtures()), *extra]),  # type: ignore[list-item]
        config=EngineConfig(seasons=("2026-27",), min_matches=40),
    )


def _watch(
    engine: Engine, *, matches: str, ticks: list[dt.datetime], **kwargs: object
) -> WatchReport:
    state = {"i": 0}

    def clock() -> dt.datetime:
        return ticks[min(state["i"], len(ticks) - 1)]

    def sleep(_seconds: float) -> None:
        state["i"] += 1

    return watch_until_kickoff(
        engine,
        matches=matches,
        plan=WatchPlan(kickoff=KICKOFF, retry_every=dt.timedelta(minutes=5)),
        now=clock,
        sleep=sleep,
        max_attempts=4,
        **kwargs,  # type: ignore[arg-type]
    )


# --------------------------------------------------------------------------- #
# 1. Une cote saisie une fois n'est pas redatée à chaque contrôle
# --------------------------------------------------------------------------- #


def test_g1_a_price_typed_once_keeps_the_hour_it_was_read() -> None:
    """Reproduction : contrôles à 19:30, 19:45 et 19:50, trois âges nuls."""
    ticks = [
        KICKOFF - dt.timedelta(minutes=75),
        KICKOFF - dt.timedelta(minutes=60),
        KICKOFF - dt.timedelta(minutes=55),
    ]
    seen: list[tuple[dt.datetime, dt.datetime | None]] = []

    def record(attempt: object, result: object) -> None:
        for analysis in result.run.analyses:  # type: ignore[attr-defined]
            if analysis.decision and analysis.decision.main:
                seen.append((attempt.at, analysis.decision.main.priced.quoted_at))  # type: ignore[attr-defined]

    _watch(
        _engine(),
        matches=f"{_LINE} @ 2.10 3.40 6.00",
        ticks=ticks,
        on_attempt=record,
    )
    assert len(seen) >= 3, "les trois contrôles doivent produire une décision"
    first = seen[0][1]
    assert first is not None
    for at, quoted in seen:
        assert quoted == first, (
            f"contrôle {at:%H:%M} : la cote a été redatée à {quoted:%H:%M}"
        )
    assert any(at != first for at, _ in seen[1:]), "les contrôles sont bien distincts"


def test_g1b_an_explicit_quoting_hour_survives_the_whole_watch() -> None:
    quoted = KICKOFF - dt.timedelta(hours=3)
    seen: list[dt.datetime | None] = []

    def record(_attempt: object, result: object) -> None:
        for analysis in result.run.analyses:  # type: ignore[attr-defined]
            if analysis.decision and analysis.decision.main:
                seen.append(analysis.decision.main.priced.quoted_at)

    _watch(
        _engine(),
        matches=f"{_LINE} @ 2.10 3.40 6.00",
        ticks=[KICKOFF - dt.timedelta(minutes=75), KICKOFF - dt.timedelta(minutes=60)],
        quoted_at=quoted,
        on_attempt=record,
    )
    assert seen and all(moment == quoted for moment in seen)


# --------------------------------------------------------------------------- #
# 2. Une absence de composition est un état, pas une exception avalée
# --------------------------------------------------------------------------- #


def test_g2_an_empty_lineup_response_is_a_state_not_a_valueerror() -> None:
    provider = FootballDataOrgProvider(token="")
    rows, notes = provider.team_sheets(
        Fixture("A", "B", dt.date(2026, 9, 14), competition="xx.9")
    )
    assert rows == ()
    assert len(notes) == 1
    assert notes[0].status is Confidence.UNAVAILABLE
    assert notes[0].value is None, "un fait indisponible ne porte aucune valeur"
    assert notes[0].note, "et il dit pourquoi"


def test_g2b_a_source_that_raises_is_recorded_rather_than_swallowed() -> None:
    """Une exception avalée se lit comme « aucune composition publiée »."""

    class Broken:
        @property
        def name(self) -> str:
            return "source-cassée"

        @property
        def capabilities(self) -> frozenset:  # type: ignore[type-arg]
            return frozenset({Capability.LINEUPS})

        def team_sheets(self, fixture: Fixture) -> tuple[tuple[()], tuple[()]]:
            raise RuntimeError(f"503 pour {fixture.home}")

    run = _engine(Broken()).run(_LINE, as_of=KICKOFF - dt.timedelta(days=1))
    analysis = run.analyses[0]
    assert analysis.analysed
    notes = [e for e in analysis.ledger.entries if "source-cassée" in e.key]
    assert notes, "l'échec de la source doit apparaître au registre"
    assert "source à réparer" in (notes[0].note or "")


def test_g2c_a_watch_reads_sheets_with_a_freshness_fit_for_a_watch() -> None:
    """Un cache de six heures rendrait le contrôle T−60 inutile."""
    assert WATCH_CACHE_TTL <= 300.0
    with tempfile.TemporaryDirectory() as folder:
        day = Cache(folder)
        watching = day.with_ttl(WATCH_CACHE_TTL)
        assert day.ttl_seconds > 3600.0
        assert watching.ttl_seconds == WATCH_CACHE_TTL
        assert watching.directory == day.directory, "même stockage, autre fraîcheur"

        url = "https://api.football-data.org/v4/matches/1"
        stored = dt.datetime.now(UTC) - dt.timedelta(minutes=20)
        day.store(url, {"homeTeam": {}}, stored)
        assert day.load(url) is not None, "encore frais pour l'usage courant"
        assert watching.load(url) is None, (
            "périmé pour un suivi : le contrôle T−60 doit vraiment redemander"
        )


def test_g2d_the_real_adapter_refetches_between_two_checks() -> None:
    """Le véritable adaptateur, avec son cache : deux contrôles, deux requêtes."""
    with tempfile.TemporaryDirectory() as folder:
        cache = Cache(folder, ttl_seconds=WATCH_CACHE_TTL)
        provider = FootballDataOrgProvider(cache, token="jeton-de-test")
        url = "https://api.football-data.org/v4/matches/77"
        cache.store(url, {"homeTeam": {}, "awayTeam": {}}, dt.datetime.now(UTC))
        assert cache.load(url) is not None
        # A response stored before the watch window has already expired for it.
        cache.store(
            url,
            {"homeTeam": {}, "awayTeam": {}},
            dt.datetime.now(UTC) - dt.timedelta(seconds=WATCH_CACHE_TTL + 60),
        )
        assert cache.load(url) is None, "le suivi doit refaire la requête"
        assert provider.configured


# --------------------------------------------------------------------------- #
# 3. Deux feuilles complètes, et le contrôle T−60 préservé
# --------------------------------------------------------------------------- #


def test_g3_one_player_per_team_is_not_an_official_lineup() -> None:
    source = SheetSource(publishes_at=KICKOFF - dt.timedelta(minutes=80), players=1)
    report = _watch(
        _engine(source),
        matches=_LINE,
        ticks=[
            KICKOFF - dt.timedelta(minutes=75),
            KICKOFF - dt.timedelta(minutes=60),
            KICKOFF - dt.timedelta(minutes=55),
        ],
    )
    assert len(report.attempts) > 1, "le suivi ne doit pas s'arrêter sur deux joueurs"
    assert "officielle" not in report.stopped_because
    assert all(not a.found_official for a in report.attempts)
    assert all(a.complete_sheets == 0 for a in report.attempts)


def test_g3b_the_t60_check_happens_even_when_sheets_arrive_at_t75() -> None:
    """C'est entre T−75 et T−60 que les compositions changent."""
    source = SheetSource(publishes_at=KICKOFF - dt.timedelta(minutes=80), players=11)
    report = _watch(
        _engine(source),
        matches=_LINE,
        ticks=[KICKOFF - dt.timedelta(minutes=75), KICKOFF - dt.timedelta(minutes=60)],
    )
    assert len(report.attempts) == 2, "T−75 puis T−60, tous deux exécutés"
    assert report.attempts[0].found_official
    assert report.attempts[-1].complete_sheets == 2
    assert "T−60" in report.stopped_because


def test_g3c_a_re_check_that_changes_nothing_is_not_announced_as_a_change() -> None:
    source = SheetSource(publishes_at=KICKOFF - dt.timedelta(minutes=80), players=11)
    report = _watch(
        _engine(source),
        matches=_LINE,
        ticks=[KICKOFF - dt.timedelta(minutes=75), KICKOFF - dt.timedelta(minutes=60)],
    )
    assert not report.decision_changed
    assert "DÉCISION MODIFIÉE" not in report.render()


# --------------------------------------------------------------------------- #
# 4-5. Identité de rencontre et intégrité chronologique de la mesure
# --------------------------------------------------------------------------- #


def _book(*forecasts: Forecast) -> ForecastBook:
    book = ForecastBook(Path(tempfile.mkdtemp()) / "journal.jsonl")
    for forecast in forecasts:
        book.append(forecast)
    return book


def test_g4_two_meetings_of_the_same_clubs_are_two_fixtures() -> None:
    """14 et 21 septembre : deux rencontres, deux mesures."""
    book = _book(
        _forecast("Club A", "Club B", date="2026-09-14", fingerprint="f1"),
        _forecast("Club A", "Club B", date="2026-09-21", fingerprint="f2"),
    )
    report = measure(
        book,
        results=[
            _played("Club A", "Club B", (1, 0), day=14),
            _played("Club A", "Club B", (0, 3), day=21),
        ],
    )
    assert len(report.resolved) == 2
    assert {r.forecast.fingerprint for r in report.resolved} == {"f1", "f2"}
    assert report.revised == 0, "la seconde n'est pas une révision de la première"


def test_g4b_an_undated_journal_line_is_not_assigned_a_date() -> None:
    """Un ancien journal reste lisible, sans attribution arbitraire."""
    old = _forecast("Club A", "Club B", date="", fingerprint="ancien")
    fresh = _forecast("Club A", "Club B", date="2026-09-14", fingerprint="daté")
    book = _book(old, fresh)
    assert old.key != fresh.key, "les deux lignes ne fusionnent pas sur une supposition"
    assert old.key[-1] == ""
    report = measure(book, results=[_played("Club A", "Club B", (1, 0), day=14)])
    # The old line still resolves: exactly one played match pairs those clubs,
    # so the fallback is unambiguous — compatibility, not guesswork. But both
    # lines describe the SAME match, and scoring both would count it twice. The
    # dated line wins; the other is reported as excluded rather than dropped.
    assert [r.forecast.fingerprint for r in report.resolved] == ["daté"]
    assert [f.fingerprint for f in report.excluded] == ["ancien"]


def test_g5_a_forecast_written_after_the_match_is_not_a_forecast() -> None:
    kickoff = dt.datetime(2026, 9, 14, 20, 45, tzinfo=UTC)
    before = dataclasses.replace(
        _forecast("Club A", "Club B", probabilities=(0.7, 0.15, 0.15), fingerprint="avant"),
        as_of=dt.datetime(2026, 9, 13, 12, tzinfo=UTC),
        recorded_at=dt.datetime(2026, 9, 13, 12, tzinfo=UTC),
        kickoff_at=kickoff.isoformat(),
    )
    after = dataclasses.replace(
        _forecast("Club A", "Club B", probabilities=(0.1, 0.1, 0.8), fingerprint="apres"),
        as_of=dt.datetime(2026, 9, 15, 12, tzinfo=UTC),
        recorded_at=dt.datetime(2026, 9, 15, 12, tzinfo=UTC),
        kickoff_at=kickoff.isoformat(),
    )
    assert after.retrospective and not before.retrospective
    report = measure(_book(before, after), results=[_played("Club A", "Club B", (1, 0))])
    assert [r.forecast.fingerprint for r in report.resolved] == ["avant"]
    assert [f.fingerprint for f in report.excluded] == ["apres"]
    assert "écartée(s)" in report.render()


def test_g5b_a_report_cut_off_excludes_anything_written_after_it() -> None:
    kickoff = dt.datetime(2026, 9, 21, 20, 45, tzinfo=UTC)
    early = dataclasses.replace(
        _forecast("Club A", "Club B", date="2026-09-21", fingerprint="lundi"),
        as_of=dt.datetime(2026, 9, 20, 9, tzinfo=UTC),
        recorded_at=dt.datetime(2026, 9, 20, 9, tzinfo=UTC),
        kickoff_at=kickoff.isoformat(),
    )
    later = dataclasses.replace(
        _forecast("Club A", "Club B", date="2026-09-21", fingerprint="mardi"),
        as_of=dt.datetime(2026, 9, 21, 9, tzinfo=UTC),
        recorded_at=dt.datetime(2026, 9, 21, 9, tzinfo=UTC),
        kickoff_at=kickoff.isoformat(),
    )
    book = _book(early, later)
    results = [_played("Club A", "Club B", (1, 0), day=21)]

    after_the_match = measure(
        book, results=results, as_of=dt.datetime(2026, 9, 22, 9, tzinfo=UTC)
    )
    assert [r.forecast.fingerprint for r in after_the_match.resolved] == ["mardi"]

    # Same journal, report dated before the second line was written: it is
    # excluded, and the match — not yet played at that instant — stays pending.
    before_it_was_written = measure(
        book, results=results, as_of=dt.datetime(2026, 9, 20, 18, tzinfo=UTC)
    )
    assert not before_it_was_written.resolved
    assert [f.fingerprint for f in before_it_was_written.pending] == ["lundi"]
    assert [f.fingerprint for f in before_it_was_written.excluded] == ["mardi"]


def test_g5d_a_match_not_yet_played_at_the_report_date_stays_pending() -> None:
    """Un résultat futur ne doit pas entrer dans un bilan daté d'avant le match."""
    kickoff = dt.datetime(2026, 9, 21, 20, 45, tzinfo=UTC)
    forecast = dataclasses.replace(
        _forecast(
            "Club A", "Club B", date="2026-09-21", market_key="1X2:H", odds=2.0
        ),
        as_of=dt.datetime(2026, 9, 20, 9, tzinfo=UTC),
        recorded_at=dt.datetime(2026, 9, 20, 9, tzinfo=UTC),
        kickoff_at=kickoff.isoformat(),
    )
    book = _book(forecast)
    results = [_played("Club A", "Club B", (2, 0), day=21)]

    the_day_before = measure(
        book, results=results, as_of=dt.datetime(2026, 9, 20, 18, tzinfo=UTC)
    )
    assert not the_day_before.resolved, (
        "le pari serait gagné dans un rapport écrit alors qu'il n'est pas joué"
    )
    assert len(the_day_before.pending) == 1

    afterwards = measure(
        book, results=results, as_of=dt.datetime(2026, 9, 22, 18, tzinfo=UTC)
    )
    assert len(afterwards.resolved) == 1
    assert afterwards.resolved[0].profit() == 1.0


def test_g5c_the_latest_forecast_is_chosen_by_time_not_by_file_order() -> None:
    """Un journal est un fichier ; l'ordre du fichier n'est pas la chronologie."""
    kickoff = dt.datetime(2026, 9, 14, 20, 45, tzinfo=UTC)
    late = dataclasses.replace(
        _forecast("Club A", "Club B", fingerprint="tardive"),
        as_of=dt.datetime(2026, 9, 14, 19, tzinfo=UTC),
        recorded_at=dt.datetime(2026, 9, 14, 19, tzinfo=UTC),
        kickoff_at=kickoff.isoformat(),
    )
    early = dataclasses.replace(
        _forecast("Club A", "Club B", fingerprint="précoce"),
        as_of=dt.datetime(2026, 9, 12, 9, tzinfo=UTC),
        recorded_at=dt.datetime(2026, 9, 12, 9, tzinfo=UTC),
        kickoff_at=kickoff.isoformat(),
    )
    report = measure(
        _book(late, early),  # written out of order on purpose
        results=[_played("Club A", "Club B", (1, 0))],
    )
    assert [r.forecast.fingerprint for r in report.resolved] == ["tardive"]


# --------------------------------------------------------------------------- #
# 6-7. Un résolveur et un règlement partagés
# --------------------------------------------------------------------------- #


def test_g6_a_canonical_key_reads_back_as_itself() -> None:
    """`OU:4.5:under` renvoyait `OU:4.5:over` — le pari inverse, silencieusement."""
    for key in (
        "OU:4.5:under",
        "OU:4.5:over",
        "OU:2.5:under",
        "TT:H:1.5:under",
        "TT:A:0.5:over",
        "AH:H:-1.75",
        "AH:A:+0.25",
        "1X2:H",
        "DC:1N",
        "DNB:H",
        "BTTS:oui",
    ):
        offer = offer_for_key(key)
        assert offer is not None, key
        again = offer_for_key(offer.key)
        assert again is not None and again.key == offer.key, (
            f"{key} → {offer.key} → {again.key if again else None}"
        )


def test_g6b_the_sign_convention_still_works_when_no_word_is_given() -> None:
    over = offer_for_key("TOTAL:+2.5")
    under = offer_for_key("TOTAL:-2.5")
    assert over is not None and under is not None
    assert over.key == "OU:2.5:over"
    assert under.key == "OU:2.5:under"
    assert over.unit_result(2, 1) > 0 and under.unit_result(2, 1) < 0


def test_g7_a_market_the_engine_accepts_is_settled_by_the_evaluation() -> None:
    """AH:H:-1.75 et OU:4.5:over donnaient profit=None et disparaissaient du bilan."""
    cases = (
        ("AH:H:-1.75", 2.00, (3, 0), 1.00),
        ("OU:4.5:over", 3.00, (3, 2), 2.00),
        ("OU:4.5:under", 1.40, (3, 2), -1.00),
        ("AH:A:+0.25", 2.00, (1, 1), 0.50),
    )
    for key, odds, goals, expected in cases:
        book = _book(_forecast("Club A", "Club B", market_key=key, odds=odds))
        report = measure(book, results=[_played("Club A", "Club B", goals)])
        profit = report.resolved[0].profit()
        assert profit is not None, f"{key} exclu du bilan"
        assert abs(profit - expected) < 1e-9, f"{key} {goals} → {profit}"
        assert report.backed(), "et il compte dans le rendement"


# --------------------------------------------------------------------------- #
# 8. Correspondance contrôlée des identités de fournisseur
# --------------------------------------------------------------------------- #


def test_g8_a_club_name_spelt_differently_still_finds_its_price() -> None:
    quotes = parse_odds(_odds_payload(), competition="en.1")
    reference = quotes[0].fixture
    for home in (reference.home, f"{reference.home} FC", reference.home.upper()):
        fixture = Fixture(
            home, f"{reference.away} FC", reference.date, competition="en.1"
        )
        assert _quotes_for(quotes, fixture), f"{home!r} ne trouve plus son prix"


def test_g8b_a_wrong_date_or_an_ambiguous_pairing_is_refused() -> None:
    quotes = parse_odds(_odds_payload(), competition="en.1")
    reference = quotes[0].fixture
    elsewhere = Fixture(
        reference.home,
        reference.away,
        reference.date + dt.timedelta(days=1),
        competition="en.1",
    )
    assert _quotes_for(quotes, elsewhere) == ()

    twin = dataclasses.replace(quotes[0], fixture=dataclasses.replace(
        reference, home=f"{reference.home} FC"
    ))
    ambiguous = [*quotes, twin]
    assert _quotes_for(ambiguous, reference) == (), (
        "deux événements repliés sur la même paire : aucun n'est choisi"
    )


# --------------------------------------------------------------------------- #
# 9. Les cotes de clôture sont réellement collectées
# --------------------------------------------------------------------------- #


def test_g9_the_command_actually_attempts_to_collect_closing_prices() -> None:
    """`closing` était initialisé à {} et jamais alimenté."""
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "clotures.csv"
        path.write_text(
            "date,home,away,marche,cote\n"
            "14/09/2026,Club A,Club B,1X2:H,2.00\n"
            "14/09/2026,Club A,Club B,OU:2.5:over,1.85\n"
            "14/09/2026,Club A,Club B,MARCHÉ:INCONNU,1.50\n",
            encoding="utf-8",
        )
        prices = load_closing_csv(path, bookmaker="Pinnacle")
        assert prices.state is ClosingState.SERVED
        # Two markets, each stored twice: once under the bookmaker that quoted
        # it, once as the market reference — which exists only because a single
        # book quoted it. The unreadable line is refused, never guessed.
        assert len(prices.prices) == 4, prices.prices
        assert "1 ligne(s) illisible(s)" in prices.detail

        key = closing_key(
            _COMP, "Club A", "Club B", dt.date(2026, 9, 14), "1X2:H", "Pinnacle"
        )
        assert prices.get(key) is None, "la compétition compte dans l'identité"
        untagged = closing_key(
            "", "Club A", "Club B", dt.date(2026, 9, 14), "1X2:H", "Pinnacle"
        )
        assert prices.get(untagged) == 2.00, "le bookmaker aussi"


def test_g9b_a_closing_price_belongs_to_a_market_not_only_to_a_match() -> None:
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "clotures.csv"
        path.write_text(
            "date,home,away,marche,cote,competition\n"
            f"14/09/2026,Club A,Club B,1X2:H,2.00,{_COMP}\n"
            f"14/09/2026,Club A,Club B,OU:2.5:over,1.85,{_COMP}\n",
            encoding="utf-8",
        )
        prices = load_closing_csv(path)
        book = _book(
            _forecast("Club A", "Club B", market_key="OU:2.5:over", odds=2.05)
        )
        report = measure(
            book, results=[_played("Club A", "Club B", (2, 1))], closing=prices
        )
        edge = report.resolved[0].closing_edge()
        assert edge is not None
        assert abs(edge - (2.05 / 1.85 - 1.0)) < 1e-9, (
            "la clôture comparée doit être celle du MÊME marché"
        )


def test_g9c_a_missing_closing_file_is_unreachable_not_absent() -> None:
    prices = load_closing_csv(Path(tempfile.mkdtemp()) / "absent.csv")
    assert prices.state is ClosingState.UNREACHABLE
    assert "introuvable" in prices.detail


# --------------------------------------------------------------------------- #
# 10. Qualification des performances
# --------------------------------------------------------------------------- #


def test_g10_the_in_sample_reference_is_labelled_as_computed_afterwards() -> None:
    """Elle connaît le résultat des rencontres qu'elle sert à juger."""
    book = _book(
        *(_forecast(f"Club {i}", f"Adversaire {i}") for i in range(12))
    )
    results = [
        _played(f"Club {i}", f"Adversaire {i}", (2, 0) if i % 2 else (0, 1))
        for i in range(12)
    ]
    report = measure(book, results=results)
    rendered = report.render()
    assert "descriptive, calculée APRÈS COUP" in rendered
    assert "majorant optimiste, à ne pas citer comme performance" in rendered
    assert report.prior_baseline is None
    assert "aucune référence antérieure fournie" in rendered


def test_g10b_a_reference_estimated_on_earlier_data_only_is_offered() -> None:
    book = _book(
        *(_forecast(f"Club {i}", f"Adversaire {i}") for i in range(12))
    )
    results = [
        _played(f"Club {i}", f"Adversaire {i}", (2, 0) if i % 2 else (0, 1))
        for i in range(12)
    ]
    prior = [_played(f"Vieux {i}", "Ancien", (1, 1), day=1) for i in range(20)]
    report = measure(book, results=results, prior=prior)
    assert report.prior_baseline is not None
    assert "estimée sur les seules données antérieures" in report.render()
    assert report.prior_baseline.name != report.baseline.name  # type: ignore[union-attr]


def test_g10c_an_interval_spanning_zero_forbids_claiming_an_advantage() -> None:
    """Ni 30 rencontres ni un nombre de tests ne démontrent une fiabilité."""
    # Forty distinct fixtures, all on the same day: different clubs, so the
    # identity is distinct and the calendar stays valid.
    book = _book(
        *(
            _forecast(
                f"Club {i}",
                f"Adversaire {i}",
                probabilities=(0.34, 0.33, 0.33),
            )
            for i in range(40)
        )
    )
    results = [
        _played(f"Club {i}", f"Adversaire {i}", [(2, 0), (1, 1), (0, 2)][i % 3])
        for i in range(40)
    ]
    report = measure(book, results=results)
    interval = report.rps_interval()
    assert interval is not None and interval[0] < 0.0 < interval[1]
    rendered = report.render()
    assert "AVANTAGE NON DÉMONTRÉ" in rendered
    assert "Aucune rentabilité n'est promise" in rendered
    assert report.conclusive, "l'effectif suffit ; c'est l'intervalle qui ne suit pas"


def test_g10d_a_replay_is_reported_as_retrospective_not_as_a_forecast() -> None:
    """« Prévisions enregistrées en avril » n'est pas ce qu'est un rejeu."""
    kickoff = dt.datetime(2026, 4, 12, 20, 45, tzinfo=UTC)
    replay = dataclasses.replace(
        _forecast("Club A", "Club B", date="2026-04-12", fingerprint="rejeu"),
        as_of=dt.datetime(2026, 4, 1, 8, tzinfo=UTC),
        recorded_at=dt.datetime(2026, 9, 14, 1, tzinfo=UTC),
        kickoff_at=kickoff.isoformat(),
    )
    assert replay.retrospective, "écrit cinq mois après le coup d'envoi"
    results = [_played("Club A", "Club B", (1, 0), day=12)]
    results[0] = dataclasses.replace(results[0], date=dt.date(2026, 4, 12))
    default = measure(_book(replay), results=results)
    assert not default.resolved and len(default.excluded) == 1
    asked = measure(_book(replay), results=results, include_retrospective=True)
    assert len(asked.resolved) == 1


def test_g9d_the_whole_command_runs_with_a_closing_source_available() -> None:
    """Le parcours complet de « foot mesurer », clôtures comprises."""
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        (root / "resultats.csv").write_text(
            "date,home,away,home_goals,away_goals\n"
            "14/09/2026,Club A,Club B,2,1\n"
            "14/09/2026,Club C,Club D,0,0\n",
            encoding="utf-8",
        )
        (root / "clotures.csv").write_text(
            "date,home,away,marche,cote,competition\n"
            "14/09/2026,Club A,Club B,1X2:H,1.90,rejeu\n"
            "14/09/2026,Club C,Club D,1X2:H,2.40,rejeu\n",
            encoding="utf-8",
        )
        book = ForecastBook(root / "journal.jsonl")
        for home, away in (("Club A", "Club B"), ("Club C", "Club D")):
            entry = dataclasses.replace(
                _forecast(home, away, market_key="1X2:H", odds=2.10),
                competition="rejeu",
                kickoff_at=dt.datetime(2026, 9, 14, 20, 45, tzinfo=UTC).isoformat(),
                as_of=dt.datetime(2026, 9, 13, 9, tzinfo=UTC),
                recorded_at=dt.datetime(2026, 9, 13, 9, tzinfo=UTC),
            )
            book.append(entry)

        args = build_parser().parse_args(
            [
                "mesurer",
                "--fichier", str(root / "journal.jsonl"),
                "--resultats-csv", str(root / "resultats.csv"),
                "--clotures-csv", str(root / "clotures.csv"),
                "--import-competition", "rejeu",
                "--bookmaker", "Doublure",
                "--no-cache",
            ]
        )
        printed = io.StringIO()
        with redirect_stdout(printed):
            code = command_mesurer(args)
        output = printed.getvalue()

    assert code == 0
    assert "2 rencontre(s) résolue(s)" in output
    # The command really collected: a served state, an edge actually computed.
    assert "clôtures.csv" in output or "clotures.csv" in output
    assert "Écart moyen au prix de clôture" in output
    assert "2 recommandation(s) réglée(s)" in output


# --------------------------------------------------------------------------- #
# 11. Le téléphone : trois actions, un suivi qui survit à l'onglet
# --------------------------------------------------------------------------- #


def test_g11_the_watch_survives_the_tab_that_started_it() -> None:
    """Un suivi lancé depuis le téléphone tourne sur le serveur."""
    done = threading.Event()
    observed: list[str] = []

    def fake_watch(handle: WatchHandle) -> None:
        observed.append(handle.matches)
        with handle.lock:
            handle.report = WatchReport(
                fixture=handle.matches,
                plan=WatchPlan(kickoff=handle.kickoff),
                attempts=[],
                stopped_because="doublure",
            )
            handle.finished = True
        done.set()

    supervisor = Supervisor(_engine())
    handle = supervisor.start(
        matches=_LINE,
        kickoff=KICKOFF,
        runner=fake_watch,
    )
    assert done.wait(timeout=5), "le suivi doit démarrer sans bloquer la requête"
    assert observed == [_LINE]
    assert supervisor.get(handle.identifier) is handle
    assert "doublure" in (handle.report.stopped_because if handle.report else "")
    assert "1 suivi(s) côté serveur" in supervisor.render()


def test_g11b_a_watch_that_fails_says_so_instead_of_looking_idle() -> None:
    done = threading.Event()

    def exploding(handle: WatchHandle) -> None:
        try:
            raise RuntimeError("réseau coupé")
        except RuntimeError as error:
            with handle.lock:
                handle.error = f"{type(error).__name__}: {error}"
                handle.finished = True
        done.set()

    supervisor = Supervisor(_engine())
    handle = supervisor.start(matches=_LINE, kickoff=KICKOFF, runner=exploding)
    assert done.wait(timeout=5)
    assert "ARRÊTÉ" in handle.summary()
    assert "réseau coupé" in handle.summary()


def test_g11c_an_idle_supervisor_states_what_it_does_and_does_not_promise() -> None:
    """La limite du moment doit être écrite, jamais sous-entendue.

    Elle a changé depuis que les suivis peuvent être écrits sur disque : sans
    fichier de suivis, un redémarrage les perd — et l'écran doit le dire, avec
    l'option qui le corrige. Avec un fichier, c'est la reprise qui est annoncée,
    et son exception (« manqué ») avec elle.
    """
    rendered = Supervisor(_engine()).render()
    assert "continue même si vous fermez l'onglet" in rendered
    assert "redémarrage du serveur perd les suivis" in rendered, (
        "la limite doit être écrite, pas sous-entendue"
    )
    assert "--suivis" in rendered, "l'écran doit dire ce qui la lève"

    with tempfile.TemporaryDirectory() as folder:
        store = WatchStore(Path(folder) / "suivis.jsonl")
        durable = Supervisor(_engine(), store=store).render()
    assert "reprend" in durable
    assert "manqué" in durable, "l'exception à la reprise doit être écrite aussi"


def test_g11d_the_bilan_screen_shows_the_journal_and_how_to_restore_it() -> None:
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "journal.jsonl"
        book = ForecastBook(path)
        book.append(_forecast("Club A", "Club B", market_key="1X2:H", odds=2.0))
        body = _render_ledger(book)
        assert "journal.jsonl" in body
        assert "1 ligne(s)" in body
        assert "Sauvegarde" in body and "restaure" in body
        assert "Club A" in body

        # A journal file copied and put back reads identically — that is the
        # whole backup story, and it is worth proving rather than asserting.
        copy = Path(folder) / "copie.jsonl"
        copy.write_bytes(path.read_bytes())
        restored = list(ForecastBook(copy))
        assert [f.to_json() for f in restored] == [f.to_json() for f in book]


def test_g11e_the_bilan_screen_is_honest_when_no_journal_is_kept() -> None:
    body = _render_ledger(None)
    assert "Aucun journal" in body
    assert "--journal" in body


def test_g12_each_rubric_names_its_source_and_its_freshness() -> None:
    """Une rubrique couverte ce matin et une couverte il y a trois semaines
    ne sont pas dans le même état, et une seule mérite de porter une décision."""
    run = _engine().run(_LINE, as_of=KICKOFF - dt.timedelta(days=1))
    text = render_rubric_provenance(run.analyses[0])
    assert "PROVENANCE ET FRAÎCHEUR" in text
    assert "relevé il y a" in text, "chaque preuve citée porte son âge"
    # A rubric with no evidence says so rather than borrowing someone else's.
    assert "aucune preuve citée" in text
    # A blocked rubric states the remedy instead of a source.
    assert "indisponible —" in text
    for number in (1, 7, 22):
        assert f"R{number:02d}" in text
