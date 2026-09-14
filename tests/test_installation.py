"""L'installation téléphone : parcours par défaut, bookmaker, contrôles, secrets.

Quatre exigences de la neuvième revue, chacune reproduite puis vérifiée :

1. ouvrir la page, saisir deux équipes, appuyer sur **Analyser** doit analyser
   *maintenant* — la date préremplie transformait ce parcours ordinaire en rejeu
   de la minute où la page avait été chargée ;
2. le bookmaker saisi dans le formulaire doit **piloter la sélection des prix**
   jusqu'au fournisseur, et pas seulement décorer la fiche ;
3. un **contrôle des connexions** doit appeler réellement chaque service et dire,
   famille par famille, ce qui revient — y compris quand la rencontre est
   introuvable, puisque la question « ma clé fonctionne-t-elle » ne dépend pas
   d'elle ;
4. **aucune clé** ne doit apparaître : ni à l'écran, ni dans un message d'erreur,
   ni dans le cache écrit sur le disque du serveur.

Aucun réseau : le garde-fou de ``conftest.py`` l'interdit, et chaque adaptateur
reçoit son *fetcher*.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import os
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from test_acceptance import StubProvider, _controlled_history
from test_regression_review8 import Clock, _fetcher

from foot.analysis.diagnostics import Outcome, run_checks, scrub
from foot.analysis.engine import Engine, EngineConfig
from foot.collect import oddsapi
from foot.collect.apifootball import ApiFootballProvider
from foot.collect.base import (
    CollectionError,
    ProviderBlockedError,
    Reachability,
    redact_url,
)
from foot.collect.cache import Cache
from foot.collect.oddsapi import OddsApiProvider, parse_odds
from foot.collect.registry import Registry
from foot.domain import Fixture
from foot.report import web
from foot.report.web import build_page, make_handler

from support import assert_raises

PARIS = ZoneInfo("Europe/Paris")
UTC = dt.timezone.utc
MATCH = dt.date(2026, 9, 14)
LINE = "it.1 | Club A - Club B | 14/09/2026 20:45"
SECRET = "cle-secrete-0123456789abcdef"


# --------------------------------------------------------------------------- #
# Doublures
# --------------------------------------------------------------------------- #


def _odds_payload(home: str = "Club A", away: str = "Club B") -> list[dict[str, Any]]:
    """Two books, the cheaper one being the operator's."""
    return [
        {
            "id": "abc",
            "home_team": home,
            "away_team": away,
            "commence_time": "2026-09-14T18:45:00Z",
            "bookmakers": [
                {
                    "title": "Pinnacle",
                    "markets": [
                        {
                            "key": "h2h",
                            "last_update": "2026-09-13T10:00:00Z",
                            "outcomes": [
                                {"name": home, "price": 1.75},
                                {"name": "Draw", "price": 3.80},
                                {"name": away, "price": 4.20},
                            ],
                        }
                    ],
                },
                {
                    "title": "Betclic",
                    "markets": [
                        {
                            "key": "h2h",
                            "last_update": "2026-09-13T09:30:00Z",
                            "outcomes": [
                                {"name": home, "price": 1.90},
                                {"name": "Draw", "price": 3.70},
                                {"name": away, "price": 4.00},
                            ],
                        }
                    ],
                },
            ],
        }
    ]


@contextmanager
def _odds_service(
    *,
    quota_headers: dict[str, str] | None = None,
    calls: list[str] | None = None,
) -> Iterator[None]:
    """Stand in for The Odds API at the HTTP boundary, key included."""
    def fake_json(url: str, **_kwargs: Any) -> tuple[object, dt.datetime]:
        if calls is not None:
            calls.append(url)
        return (_odds_payload(), dt.datetime(2026, 9, 13, 10, 5, tzinfo=UTC))

    def fake_headers(
        url: str, **_kwargs: Any
    ) -> tuple[object, dt.datetime, dict[str, str]]:
        if calls is not None:
            calls.append(url)
        return (
            [],
            dt.datetime(2026, 9, 13, 10, 5, tzinfo=UTC),
            quota_headers if quota_headers is not None
            else {"x-requests-remaining": "473", "x-requests-used": "27"},
        )

    module = oddsapi

    before = (module.fetch_json, module.fetch_json_headers)
    module.fetch_json = fake_json  # type: ignore[assignment]
    module.fetch_json_headers = fake_headers  # type: ignore[assignment]
    try:
        yield
    finally:
        module.fetch_json, module.fetch_json_headers = before  # type: ignore[assignment]


@contextmanager
def _environment(**values: str) -> Iterator[None]:
    """Set credentials for the duration of one test, then put them back."""
    before = {name: os.environ.get(name) for name in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for name, old in before.items():
            if old is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = old


def _status_payload(plan: str = "Pro", used: int = 128) -> dict[str, Any]:
    """The shape ``/status`` really answers with, for an active account."""
    return {
        "errors": [],
        "response": {
            "account": {"firstname": "Opérateur"},
            "subscription": {"plan": plan, "end": "2027-09-14", "active": True},
            "requests": {"current": used, "limit_day": 7500},
        },
    }


def _api_football(clock: Clock, fetch: Callable[..., Any] | None = None) -> Any:
    """The adapter, answering from recorded payloads — ``/status`` included."""
    inner = fetch or _fetcher(clock, "Club A", "Club B")

    def answering(url: str, **kwargs: Any) -> tuple[object, dt.datetime]:
        if "/status" in url and fetch is None:
            return (_status_payload(), clock.t)
        return inner(url, **kwargs)  # type: ignore[no-any-return]

    return ApiFootballProvider(None, token=SECRET, now=clock.now, fetch=answering)


def _engine(clock: Clock, *, with_api: bool = True, odds: bool = False) -> Engine:
    fixtures = [Fixture("Club A", "Club B", MATCH, competition="it.1")]
    providers: list[Any] = [StubProvider(_controlled_history(), fixtures)]
    if with_api:
        providers.append(_api_football(clock))
    if odds:
        providers.append(OddsApiProvider(None, key=SECRET))
    return Engine(
        Registry(providers),
        config=EngineConfig(seasons=("2026-27",), min_matches=40),
    )


def _post(handler_class: type, form: dict[str, str]) -> str:
    """Drive one POST through the real handler, without a socket."""
    body = "&".join(f"{k}={v.replace(' ', '+')}" for k, v in form.items())
    captured: list[str] = []

    class Fake(handler_class):  # type: ignore[misc, valid-type]
        def __init__(self) -> None:
            self.headers = {"Content-Length": str(len(body.encode("utf-8")))}
            self.path = "/"

        def _send(
            self, page: str, status: int = 200, *, cookie: str = ""
        ) -> None:
            _ = (status, cookie)  # la vraie signature, sans la socket
            captured.append(page)

    fake = Fake()
    fake.rfile = io.BytesIO(body.encode("utf-8"))  # type: ignore[attr-defined]
    fake.do_POST()
    return captured[0]


# --------------------------------------------------------------------------- #
# 1. Le parcours par défaut du navigateur est « maintenant »
# --------------------------------------------------------------------------- #


def test_i1_the_opening_page_leaves_the_date_empty() -> None:
    """Une date préremplie rejoue ; le parcours ordinaire doit analyser."""
    page = build_page()
    field = [line for line in page.splitlines() if 'id="date"' in line]
    assert field, "le champ date doit exister"
    assert 'value=""' in field[0], f"date préremplie : {field[0]!r}"


def test_i1b_an_empty_date_field_runs_live_and_a_filled_one_replays() -> None:
    """Le même formulaire, deux intentions, et elles ne se confondent plus."""
    seen: list[bool] = []
    clock = Clock()

    def spy(*_args: Any, live: bool = False, **_kwargs: Any) -> Any:
        seen.append(live)
        raise ValueError("arrêt volontaire après l'appel")

    handler = make_handler(_engine(clock, with_api=False))
    before = web.analyse_form
    web.analyse_form = spy  # type: ignore[assignment]
    try:
        _post(handler, {"matchs": "Club A - Club B", "date": "", "action": "analyser"})
        _post(
            handler,
            {
                "matchs": "Club A - Club B",
                "date": "2026-09-13T12:00",
                "action": "analyser",
            },
        )
    finally:
        web.analyse_form = before  # type: ignore[assignment]
    assert seen == [True, False], seen


def test_i1c_the_page_offers_the_four_actions() -> None:
    page = build_page()
    for action in ("analyser", "suivre", "bilan", "controle"):
        assert f'value="{action}"' in page, f"bouton {action} absent"


# --------------------------------------------------------------------------- #
# 2. Le bookmaker du formulaire pilote la sélection des prix
# --------------------------------------------------------------------------- #


def test_i2_the_form_bookmaker_reaches_the_provider_and_decides() -> None:
    """Betclic est plus cher : sans transmission, c'est lui qui sortirait."""
    fixture = Fixture("Club A", "Club B", MATCH, competition="it.1")
    provider = OddsApiProvider(None, key=SECRET)  # aucun bookmaker à la construction
    with _odds_service():
        chosen = provider.market_prices(fixture, prefer="Pinnacle")
    price, _hour, book = chosen["1X2:H"]
    assert book == "Pinnacle", f"le bookmaker demandé n'a pas décidé : {book}"
    assert price == 1.75, price

    with _odds_service():
        free = provider.market_prices(fixture)
    assert free["1X2:H"][2] == "Betclic", "sans préférence, le meilleur prix gagne"


def test_i2b_the_engine_passes_the_run_bookmaker_to_the_market_source() -> None:
    """Le moteur construit une fois, sert plusieurs opérateurs."""
    seen: list[str] = []

    class Spy:
        name = "espion"
        upstream = "espion"

        def market_prices(
            self, fixture: Fixture, *, prefer: str = ""
        ) -> dict[str, tuple[float, dt.datetime | None, str]]:
            _ = fixture  # la signature du protocole, entière
            seen.append(prefer)
            return {}

    clock = Clock()
    engine = _engine(clock, with_api=False)
    engine._market_sources = (Spy(),)  # type: ignore[attr-defined]
    engine.run(LINE, as_of=dt.datetime(2026, 9, 13, 12, 0, tzinfo=PARIS),
               bookmaker="Pinnacle", live=False)
    assert seen and all(book == "Pinnacle" for book in seen), seen


# --------------------------------------------------------------------------- #
# 3. Le contrôle des connexions appelle vraiment
# --------------------------------------------------------------------------- #


def _named(report: Any, name: str) -> Any:
    return next(check for check in report.checks if check.name == name)


def test_i3_the_control_calls_each_family_and_reports_what_came_back() -> None:
    clock = Clock()
    with _environment(API_FOOTBALL_KEY=SECRET, ODDS_API_KEY=SECRET), _odds_service():
        report = run_checks(
            _engine(clock, odds=True),
            fixture_line=LINE,
            bookmaker="Pinnacle",
            timezone="Europe/Paris",
        )
    assert _named(report, "Parcours Analyser").outcome is Outcome.OK
    assert _named(report, "Cotes (The Odds API)").outcome is Outcome.OK
    book = _named(report, "Bookmaker du formulaire")
    assert book.outcome is Outcome.OK, book.render()
    assert "Pinnacle" in book.detail
    assert any("The Odds API" in line for line in report.quotas), report.quotas
    assert "473" in " ".join(report.quotas), "le quota mesuré doit être affiché"
    assert "Pro" in " ".join(report.quotas), "la formule mesurée doit être affichée"
    assert "7372 restantes" in " ".join(report.quotas)
    assert report.coverage, "la couverture champ par champ doit être mesurée"
    assert report.ready, report.render()


def test_i3b_a_family_the_service_does_not_serve_is_not_called_a_failure() -> None:
    """« Répondu, rien à servir » et « refusé » sont deux verdicts distincts."""
    clock = Clock()

    def empty(url: str, **_kwargs: Any) -> tuple[object, dt.datetime]:
        clock.tick()
        if "/fixtures?" in url:
            return (
                {
                    "errors": [],
                    "response": [
                        {
                            "fixture": {"id": 77, "date": "2026-09-14T18:45:00+00:00"},
                            "teams": {
                                "home": {"name": "Club A"},
                                "away": {"name": "Club B"},
                            },
                        }
                    ],
                },
                clock.t,
            )
        return ({"errors": [], "response": []}, clock.t)

    engine = Engine(
        Registry([
            StubProvider(
                _controlled_history(),
                [Fixture("Club A", "Club B", MATCH, competition="it.1")],
            ),
            _api_football(clock, empty),
        ]),
        config=EngineConfig(seasons=("2026-27",), min_matches=40),
    )
    with _environment(API_FOOTBALL_KEY=SECRET, ODDS_API_KEY=""):
        report = run_checks(engine, fixture_line=LINE, cache=None)
    assert _named(report, "Absences").outcome is Outcome.EMPTY
    assert _named(report, "Compositions").outcome is Outcome.EMPTY
    assert _named(report, "Cotes (The Odds API)").outcome is Outcome.NOT_CONFIGURED
    assert not report.ready, "un ◐ n'est pas un feu vert"


def test_i3c_the_keys_are_checked_even_when_the_fixture_is_unknown() -> None:
    """« Ma clé marche-t-elle » ne dépend pas du calendrier chargé."""
    clock = Clock()
    with _environment(API_FOOTBALL_KEY="", ODDS_API_KEY=""):
        report = run_checks(
            _engine(clock, with_api=False),
            fixture_line="Équipe Inconnue - Autre Inconnue 20/09/2026 17:30",
        )
    assert _named(report, "Clé API-Football").outcome is Outcome.NOT_CONFIGURED
    assert _named(report, "Clé The Odds API").outcome is Outcome.NOT_CONFIGURED
    assert _named(report, "Parcours Analyser").outcome is Outcome.EMPTY


def test_i3d_the_control_screen_is_reachable_from_the_phone() -> None:
    clock = Clock()
    with _environment(API_FOOTBALL_KEY="", ODDS_API_KEY=""):
        page = _post(
            make_handler(_engine(clock, with_api=False)),
            {"matchs": "Club A - Club B 14/09/2026 20:45", "action": "controle"},
        )
    assert "Contrôle des connexions" in page
    assert "Clé API-Football" in page


def test_i3e_the_control_screen_asks_for_a_fixture_rather_than_pretending() -> None:
    clock = Clock()
    page = _post(
        make_handler(_engine(clock, with_api=False)),
        {"matchs": "", "action": "controle"},
    )
    assert "Saisissez une rencontre" in page


# --------------------------------------------------------------------------- #
# 4. Aucune clé, nulle part
# --------------------------------------------------------------------------- #


def test_i4_a_key_never_survives_a_provider_error_message() -> None:
    """urllib met l'URL — donc la clé — dans l'exception qu'il lève."""
    url = f"https://api.the-odds-api.com/v4/sports/soccer_epl/odds?apiKey={SECRET}&regions=eu"
    assert SECRET not in redact_url(url)
    assert "regions=eu" in redact_url(url), "seule la clé est masquée"
    with _environment(ODDS_API_KEY=SECRET):
        assert SECRET not in scrub(f"échec sur {url}")
        assert SECRET not in scrub(f"clé refusée : {SECRET}")


def test_i4b_the_cache_on_the_server_disk_holds_no_key() -> None:
    """Le disque persistant de l'hébergeur est sauvegardé : il ne doit rien porter."""
    directory = tempfile.mkdtemp()
    cache = Cache(directory, ttl_seconds=600)
    url = f"https://v3.football.api-sports.io/status?apiKey={SECRET}"
    cache.store(url, {"ok": True}, dt.datetime.now(UTC))
    written = "\n".join(
        path.read_text(encoding="utf-8") for path in Path(directory).glob("*.json")
    )
    assert written, "le cache doit avoir écrit quelque chose"
    assert SECRET not in written, "une clé a été écrite sur le disque du serveur"
    assert cache.load(url) is not None, "la clé de lecture reste l'URL complète"
    assert json.loads(written)["url"].endswith("[masquée]")


def test_i4c_no_check_report_ever_prints_a_key() -> None:
    clock = Clock()

    def refusing(url: str, **_kwargs: Any) -> tuple[object, dt.datetime]:
        clock.tick()
        raise ProviderBlockedError(f"{url} : HTTP 401 — accès refusé")

    engine = Engine(
        Registry([
            StubProvider(
                _controlled_history(),
                [Fixture("Club A", "Club B", MATCH, competition="it.1")],
            ),
            _api_football(clock, refusing),
        ]),
        config=EngineConfig(seasons=("2026-27",), min_matches=40),
    )
    with _environment(API_FOOTBALL_KEY=SECRET, ODDS_API_KEY=SECRET), _odds_service():
        report = run_checks(engine, fixture_line=LINE, bookmaker="Pinnacle")
    rendered = report.render()
    assert SECRET not in rendered, "une clé est apparue dans le contrôle"
    assert "masquée" in rendered or "refusé" in rendered


def test_i4d_no_key_is_written_in_the_repository() -> None:
    """Les clés vivent dans l'hébergeur, jamais dans le dépôt."""
    root = Path(__file__).resolve().parent.parent
    tracked = [
        path
        for path in root.rglob("*")
        if path.is_file()
        and ".git" not in path.parts
        and path.suffix in {".py", ".yaml", ".yml", ".md", ".json", ".toml"}
    ]
    assert tracked, "rien à inspecter"
    for path in tracked:
        if path.name == Path(__file__).name:
            continue  # ce fichier montre le motif exprès, pour le vérifier
        text = path.read_text(encoding="utf-8", errors="replace")
        for name in ("API_FOOTBALL_KEY", "ODDS_API_KEY"):
            for line in text.splitlines():
                if f"{name}=" not in line:
                    continue
                value = line.split(f"{name}=", 1)[1].strip().strip("\"'`,)")
                # Only an opaque, key-shaped token counts: « ODDS_API_KEY= »
                # written in prose, in a placeholder or in an assertion is not a
                # leaked key, and flagging it would train the reader to ignore
                # this test.
                looks_like_a_key = (
                    len(value) >= 16
                    and " " not in value
                    and all(c.isalnum() or c in "_-" for c in value)
                    and not value.isupper()
                )
                assert not looks_like_a_key, (
                    f"{path.name} : {name} semble porter une valeur"
                )


# --------------------------------------------------------------------------- #
# 5. Les quotas rapportés viennent du service
# --------------------------------------------------------------------------- #


def test_i5_the_odds_quota_is_read_from_the_service_headers() -> None:
    with _odds_service(quota_headers={"x-requests-remaining": "12", "x-requests-used": "488"}):
        quota = OddsApiProvider(None, key=SECRET).quota()
    assert quota.remaining == 12
    assert quota.limit == 500
    assert "12" in quota.render()


def test_i5b_a_service_that_publishes_no_quota_says_so_instead_of_zero() -> None:
    with _odds_service(quota_headers={}):
        quota = OddsApiProvider(None, key=SECRET).quota()
    assert quota.remaining is None and quota.used is None
    assert not quota.measured
    assert "pas renvoyé" in quota.render()


def test_i5c_api_football_reports_the_plan_the_account_actually_holds() -> None:
    clock = Clock()

    def status(url: str, **_kwargs: Any) -> tuple[object, dt.datetime]:
        assert "/status" in url
        return (
            {
                "errors": [],
                "response": {
                    "subscription": {"plan": "Pro", "end": "2027-09-14", "active": True},
                    "requests": {"current": 128, "limit_day": 7500},
                },
            },
            clock.t,
        )

    quota = _api_football(clock, status).quota()
    assert quota.plan == "Pro"
    assert quota.used == 128 and quota.limit == 7500
    assert "7372 restantes" in quota.render()


def test_i5d_an_expired_subscription_is_not_reported_as_working() -> None:
    clock = Clock()

    def status(_url: str, **_kwargs: Any) -> tuple[object, dt.datetime]:
        return (
            {
                "errors": [],
                "response": {
                    "subscription": {"plan": "Free", "active": False},
                    "requests": {"current": 100, "limit_day": 100},
                },
            },
            clock.t,
        )

    quota = _api_football(clock, status).quota()
    assert quota.reachability is Reachability.BLOCKED
    assert "inactif" in quota.render()


def test_i5e_a_refused_key_is_a_refusal_not_an_empty_quota() -> None:
    clock = Clock()

    def refusing(_url: str, **_kwargs: Any) -> tuple[object, dt.datetime]:
        raise ProviderBlockedError("API-Football a refusé la requête — token invalide")

    quota = _api_football(clock, refusing).quota()
    assert not quota.measured
    assert "refusé" in quota.render()


def test_i5f_an_unreadable_counter_is_never_turned_into_a_number() -> None:
    clock = Clock()

    def status(_url: str, **_kwargs: Any) -> tuple[object, dt.datetime]:
        return (
            {
                "errors": [],
                "response": {"requests": {"current": None, "limit_day": "beaucoup"}},
            },
            clock.t,
        )

    quota = _api_football(clock, status).quota()
    assert quota.used is None and quota.limit is None
    assert "pas renvoyé de compteur" in quota.render()


def test_i5g_a_closed_network_is_reported_as_unreachable_not_as_refused() -> None:
    clock = Clock()

    def down(_url: str, **_kwargs: Any) -> tuple[object, dt.datetime]:
        raise CollectionError("connexion impossible")

    quota = _api_football(clock, down).quota()
    assert quota.reachability.name == "ERROR"


def test_i5h_parse_odds_still_reads_what_the_service_sends() -> None:
    """Garde-fou : la doublure doit rester fidèle au vrai format."""
    quotes = parse_odds(_odds_payload(), competition="it.1")
    assert {q.bookmaker for q in quotes} == {"Pinnacle", "Betclic"}
    with assert_raises(CollectionError):
        parse_odds({"pas": "une liste"}, competition="it.1")
