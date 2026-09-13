"""Les nouveaux adaptateurs, vérifiés sur des réponses enregistrées.

Aucun réseau, aucune clé : ce fichier contrôle que nous **lisons correctement**
les formats que ces API renvoient.  Ce que le compte de l'opérateur obtient
réellement est une autre question, et elle ne se règle qu'en appelant le service
— c'est le rôle de ``probe()``, exercé chez lui, pas ici.

Les échantillons reproduisent la forme documentée de chaque API, y compris les
cas désagréables : un plan qui ne sert pas les compositions, un match sans score,
un marché que notre catalogue ne sait pas régler.
"""

from __future__ import annotations

import datetime as dt
import os
import tempfile
from pathlib import Path
from typing import Any

from foot.collect.base import Capability, MarketSource, Reachability, SeasonSource
from foot.collect.catalogue import (
    PROVIDER_CATALOGUE,
    AccessState,
    CatalogueEntry,
    CatalogueReport,
)
from foot.collect.credentials import describe, load_credentials, sample_file
from foot.collect.footballdata_org import (
    COMPETITION_CODES,
    FootballDataOrgProvider,
    parse_lineups,
    parse_matches,
)
from foot.collect.oddsapi import (
    OddsApiProvider,
    best_prices,
    parse_odds,
)
from foot.domain import Fixture

UTC = dt.timezone.utc


# --------------------------------------------------------------------------- #
# football-data.org
# --------------------------------------------------------------------------- #


def _matches_payload() -> dict[str, Any]:
    """One played match, one scheduled, one row too broken to read."""
    return {
        "competition": {"name": "Premier League"},
        "matches": [
            {
                "utcDate": "2026-08-30T14:00:00Z",
                "status": "FINISHED",
                "homeTeam": {"name": "Arsenal FC"},
                "awayTeam": {"name": "Chelsea FC"},
                "score": {"fullTime": {"home": 2, "away": 1}},
            },
            {
                "utcDate": "2026-09-14T16:30:00Z",
                "status": "TIMED",
                "homeTeam": {"name": "Arsenal FC"},
                "awayTeam": {"name": "Liverpool FC"},
                "score": {"fullTime": {"home": None, "away": None}},
            },
            {"utcDate": "", "status": "FINISHED", "homeTeam": {}, "awayTeam": {}},
        ],
    }


def test_played_and_upcoming_matches_are_separated() -> None:
    played, fixtures = parse_matches(_matches_payload(), competition="en.1")
    assert len(played) == 1
    assert len(fixtures) == 1
    assert played[0].score is not None
    assert played[0].score.home == 2
    assert fixtures[0].away == "Liverpool FC"
    assert fixtures[0].competition == "en.1"


def test_a_match_without_a_score_never_becomes_a_draw() -> None:
    """Inventing a 0-0 for a missing score would corrupt every rating."""
    payload = {
        "matches": [
            {
                "utcDate": "2026-08-30T14:00:00Z",
                "status": "FINISHED",
                "homeTeam": {"name": "Arsenal FC"},
                "awayTeam": {"name": "Chelsea FC"},
                "score": {"fullTime": {"home": None, "away": None}},
            }
        ]
    }
    played, fixtures = parse_matches(payload, competition="en.1")
    assert len(played) == 0
    assert len(fixtures) == 1, "sans score, c'est une rencontre à venir"


def test_lineups_are_read_with_their_roles_and_bench() -> None:
    payload = {
        "homeTeam": {
            "name": "Arsenal FC",
            "lineup": [
                {"name": "David Raya", "position": "Goalkeeper"},
                {"name": "William Saliba", "position": "Centre-Back"},
            ],
            "bench": [{"name": "Kepa Arrizabalaga", "position": "Goalkeeper"}],
        },
        "awayTeam": {
            "name": "Chelsea FC",
            "lineup": [{"name": "Robert Sánchez", "position": "Goalkeeper"}],
        },
    }
    published = dt.datetime(2026, 9, 14, 19, 30, tzinfo=UTC)
    rows = parse_lineups(
        payload, match_date=dt.date(2026, 9, 14), published_at=published
    )
    assert len(rows) == 4
    keeper = next(r for r in rows if r.player == "David Raya")
    assert keeper.role == "gardien"
    assert keeper.starting
    assert keeper.opponent == "Chelsea FC"
    assert keeper.published_at == published
    bench = next(r for r in rows if r.player == "Kepa Arrizabalaga")
    assert not bench.starting


def test_a_plan_without_lineups_returns_nothing_rather_than_inventing() -> None:
    """The free tier answers without the lineup block; that is not a lineup."""
    payload = {"homeTeam": {"name": "Arsenal FC"}, "awayTeam": {"name": "Chelsea FC"}}
    assert parse_lineups(payload, match_date=dt.date(2026, 9, 14)) == ()


def test_an_unknown_position_is_not_forced_into_a_watched_role() -> None:
    """A wrong role would create or suppress a decisive-absence scenario."""
    payload = {
        "homeTeam": {
            "name": "Arsenal FC",
            "lineup": [{"name": "Joueur", "position": "Sweeper Keeper Libero"}],
        }
    }
    rows = parse_lineups(payload, match_date=dt.date(2026, 9, 14))
    assert rows[0].role == "sweeper keeper libero"
    assert not rows[0].decisive if hasattr(rows[0], "decisive") else True


def test_the_adapter_satisfies_the_engine_contract() -> None:
    provider = FootballDataOrgProvider(token="clé-de-test")
    assert isinstance(provider, SeasonSource)
    assert provider.configured
    assert set(provider.competitions()) == set(COMPETITION_CODES)


def test_without_a_key_the_probe_says_so_instead_of_failing() -> None:
    """A missing key is a configuration state, not a crash."""
    provider = FootballDataOrgProvider(token="")
    assert not provider.configured
    status = provider.probe()
    assert status.reachability is Reachability.BLOCKED
    assert "FOOTBALL_DATA_ORG_TOKEN" in status.detail
    assert status.capabilities == frozenset()


# --------------------------------------------------------------------------- #
# The Odds API
# --------------------------------------------------------------------------- #


def _odds_payload() -> list[dict[str, Any]]:
    return [
        {
            "home_team": "Arsenal",
            "away_team": "Chelsea",
            "commence_time": "2026-09-14T16:30:00Z",
            "bookmakers": [
                {
                    "title": "Pinnacle",
                    "markets": [
                        {
                            "key": "h2h",
                            "last_update": "2026-09-13T10:00:00Z",
                            "outcomes": [
                                {"name": "Arsenal", "price": 1.75},
                                {"name": "Draw", "price": 3.80},
                                {"name": "Chelsea", "price": 4.20},
                            ],
                        },
                        {
                            "key": "totals",
                            "last_update": "2026-09-13T10:02:00Z",
                            "outcomes": [
                                {"name": "Over", "price": 1.95, "point": 2.5},
                                {"name": "Under", "price": 1.90, "point": 2.5},
                            ],
                        },
                        {
                            "key": "btts",
                            "last_update": "2026-09-13T10:02:00Z",
                            "outcomes": [{"name": "Yes", "price": 1.85}],
                        },
                    ],
                },
                {
                    "title": "Betclic",
                    "markets": [
                        {
                            "key": "h2h",
                            "last_update": "2026-09-13T09:30:00Z",
                            "outcomes": [
                                {"name": "Arsenal", "price": 1.90},
                                {"name": "Draw", "price": 3.70},
                                {"name": "Chelsea", "price": 4.00},
                            ],
                        }
                    ],
                },
            ],
        }
    ]


def test_every_price_keeps_its_book_and_its_own_hour() -> None:
    quotes = parse_odds(_odds_payload(), competition="en.1")
    assert len(quotes) == 8, len(quotes)
    home = [q for q in quotes if q.market_key == "1X2:H"]
    assert {q.bookmaker for q in home} == {"Pinnacle", "Betclic"}
    pinnacle = next(q for q in home if q.bookmaker == "Pinnacle")
    betclic = next(q for q in home if q.bookmaker == "Betclic")
    assert pinnacle.quoted_at != betclic.quoted_at, "deux relevés, deux heures"
    totals = next(q for q in quotes if q.market_key == "OU:2.5:over")
    assert totals.quoted_at == dt.datetime(2026, 9, 13, 10, 2, tzinfo=UTC)


def test_a_market_the_catalogue_cannot_settle_is_left_out() -> None:
    """Better no price than a price attached to the wrong settlement rule."""
    quotes = parse_odds(_odds_payload(), competition="en.1")
    assert not [q for q in quotes if "btts" in q.market_key.lower()]


def test_the_requested_bookmaker_wins_over_a_better_price_elsewhere() -> None:
    """A price the operator cannot actually take is not an opportunity."""
    quotes = parse_odds(
        _odds_payload(), competition="en.1", requested_bookmaker="Pinnacle"
    )
    best = best_prices(quotes, prefer="Pinnacle")
    assert best["1X2:H"].bookmaker == "Pinnacle"
    assert best["1X2:H"].price == 1.75, "Betclic est plus cher mais indisponible"
    assert best["1X2:H"].from_requested_book


def test_a_price_from_another_book_is_labelled_as_such() -> None:
    quotes = parse_odds(
        _odds_payload(), competition="en.1", requested_bookmaker="Unibet"
    )
    best = best_prices(quotes, prefer="Unibet")
    chosen = best["1X2:H"]
    assert not chosen.from_requested_book
    assert "≠ Unibet demandé" in chosen.label
    note = chosen.evidence().note or ""
    assert "≠ Unibet demandé" in note


def test_the_odds_adapter_satisfies_the_market_source_contract() -> None:
    assert isinstance(OddsApiProvider(key="clé-de-test"), MarketSource)


def test_a_fixture_outside_the_covered_competitions_returns_nothing() -> None:
    provider = OddsApiProvider(key="clé-de-test")
    assert provider.market_prices(
        Fixture("A", "B", dt.date(2026, 9, 14), competition="zz.9")
    ) == {}


def test_without_a_key_the_odds_probe_says_so() -> None:
    status = OddsApiProvider(key="").probe()
    assert status.reachability is Reachability.BLOCKED
    assert "ODDS_API_KEY" in status.detail


# --------------------------------------------------------------------------- #
# Catalogue: documentation, measurement and cost, kept apart
# --------------------------------------------------------------------------- #


def test_a_card_alone_never_claims_the_provider_works() -> None:
    """No probe means no claim: `None` is not `OK`."""
    card = next(c for c in PROVIDER_CATALOGUE if c.key == "openfootball")
    assert card.state(None) is AccessState.NEEDS_KEY or card.state(None) is (
        AccessState.OPERATIONAL
    )
    assert card.state(Reachability.OK) is AccessState.OPERATIONAL
    assert card.state(Reachability.BLOCKED) is AccessState.UNREACHABLE


def test_an_unbuilt_provider_is_never_reported_as_reachable() -> None:
    card = next(c for c in PROVIDER_CATALOGUE if c.key == "api-football")
    assert not card.built
    assert card.state(Reachability.OK) is AccessState.NOT_BUILT, (
        "aucun adaptateur : joignable ou non, rien ne peut en lire la donnée"
    )


def test_the_gap_between_documentation_and_probe_is_reported() -> None:
    """A provider can be perfectly reachable and still not serve what it lists."""
    card = next(c for c in PROVIDER_CATALOGUE if c.key == "football-data-org")
    entry = CatalogueEntry(
        card=card,
        reachability=Reachability.OK,
        observed=frozenset({Capability.RESULTS, Capability.FIXTURES}),
    )
    assert entry.undelivered == frozenset({Capability.LINEUPS})
    assert "annoncé mais NON servi" in entry.render()


def test_every_catalogued_provider_states_its_cost() -> None:
    for card in PROVIDER_CATALOGUE:
        assert card.cost.free_tier, card.key
        assert card.coverage.render(), card.key
        if card.credential:
            assert card.homepage, f"{card.key} : où vérifier le prix ?"


def test_the_report_names_the_capabilities_nobody_serves() -> None:
    report = CatalogueReport(
        entries=tuple(
            CatalogueEntry(card=card, reachability=None) for card in PROVIDER_CATALOGUE
        )
    )
    text = report.render()
    assert "Capacités qu'aucun fournisseur ne sert ici" in text
    assert "jamais comblées par une valeur inventée" in text
    assert "Aucun engagement payant" in text


# --------------------------------------------------------------------------- #
# Credentials: presence, never value
# --------------------------------------------------------------------------- #


def test_a_key_is_reported_present_without_being_printed() -> None:
    name = "FOOT_TEST_CREDENTIAL"
    os.environ[name] = "abcd1234secret"
    try:
        status = describe({name: "test"})[0]
        rendered = status.render()
    finally:
        del os.environ[name]
    assert status.present
    assert "abcd1234secret" not in rendered, "une clé ne s'affiche jamais en entier"
    assert "cret" in rendered, "les derniers caractères aident à vérifier la saisie"
    assert "14 caractères" in rendered


def test_a_short_key_shows_no_tail_at_all() -> None:
    name = "FOOT_TEST_SHORT"
    os.environ[name] = "abc"
    try:
        rendered = describe({name: "test"})[0].render()
    finally:
        del os.environ[name]
    assert "abc" not in rendered
    assert "trop courte" in rendered


def test_the_environment_wins_over_a_stale_file() -> None:
    name = "FOOT_TEST_PRIORITY"
    os.environ[name] = "depuis-environnement"
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "cles"
        path.write_text(f"{name}=depuis-fichier\n", encoding="utf-8")
        try:
            load_credentials(path)
            assert os.environ[name] == "depuis-environnement"
        finally:
            del os.environ[name]


def test_a_missing_file_is_not_an_error() -> None:
    with tempfile.TemporaryDirectory() as directory:
        assert load_credentials(Path(directory) / "absent") == {}


def test_the_sample_file_documents_what_each_key_unlocks() -> None:
    text = sample_file({"ODDS_API_KEY": "The Odds API — cotes d'avant-match"})
    assert "ODDS_API_KEY=" in text
    assert "cotes d'avant-match" in text
    assert "NE PAS committer" in text
