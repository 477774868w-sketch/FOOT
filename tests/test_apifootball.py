"""L'adaptateur API-Football, éprouvé sur des réponses enregistrées.

Aucune clé, aucun réseau : les réponses sont celles que le service renvoie, et
l'adaptateur réel les lit — son parsing, son cache, sa lecture des erreurs.

Ce que ces tests défendent, au-delà du parsing :

* **zéro n'est pas une absence.** Une équipe qui a tiré zéro fois a tiré zéro
  fois ; un champ que le plan ne sert pas est ``None`` ;
* un indicateur générique ne vaut pas confirmation : la couverture est établie
  **champ par champ**, par championnat et par saison ;
* rien n'est dérivé de ce qui n'est pas servi — pas de xG à onze contre onze
  fabriqué à partir de totaux ;
* une réponse d'erreur à 200 (clé invalide, quota épuisé) est un refus, pas une
  ligue vide.
"""

from __future__ import annotations

import datetime as dt
import tempfile
from collections.abc import Callable
from typing import Any

from foot.analysis.rubrics import RUBRICS
from foot.collect.apifootball import (
    COMPETITION_IDS,
    CREDENTIAL,
    ApiFootballProvider,
    CoverageMatrix,
    FieldState,
    MatchStatistics,
    field_coverage,
    parse_injuries,
    parse_lineups,
    parse_statistics,
)
from foot.collect.base import (
    Capability,
    LineupSource,
    ProviderBlockedError,
    Reachability,
)
from foot.collect.cache import Cache
from foot.domain import Fixture
from foot.provenance import Confidence

UTC = dt.timezone.utc
MATCH_DAY = dt.date(2026, 9, 13)


def _statistics_payload(*, with_xg: bool = True, zero_shots: bool = False) -> Any:
    """A ``/fixtures/statistics`` response, as the service shapes it."""

    def block(team: str, xg: str | None, shots: int) -> dict[str, Any]:
        entries: list[dict[str, Any]] = [
            {"type": "Total Shots", "value": shots},
            {"type": "Shots on Goal", "value": 0 if zero_shots else 4},
            {"type": "Red Cards", "value": None},
            {"type": "Yellow Cards", "value": 2},
        ]
        if with_xg:
            entries.append({"type": "expected_goals", "value": xg})
        return {"team": {"name": team}, "statistics": entries}

    return {
        "errors": [],
        "response": [
            block("SSC Napoli", "2.31", 0 if zero_shots else 14),
            block("Bologna FC 1909", "0.74", 0 if zero_shots else 7),
        ],
    }


def _injuries_payload() -> Any:
    return {
        "errors": [],
        "response": [
            {
                "player": {
                    "name": "Alex Meret",
                    "type": "Missing Fixture",
                    "reason": "Knee Injury",
                    "position": "G",
                },
                "team": {"name": "SSC Napoli"},
                "fixture": {"date": "2026-09-13T18:45:00+00:00"},
            },
            {
                "player": {
                    "name": "Riccardo Orsolini",
                    "type": "Questionable",
                    "reason": "Knock",
                    "position": "F",
                },
                "team": {"name": "Bologna FC 1909"},
                "fixture": {"date": "2026-09-13T18:45:00+00:00"},
            },
        ],
    }


def _lineups_payload() -> Any:
    def eleven(team: str) -> dict[str, Any]:
        return {
            "team": {"name": team},
            "startXI": [
                {"player": {"name": f"{team} {i}", "pos": "G" if i == 1 else "D"}}
                for i in range(1, 12)
            ],
            "substitutes": [
                {"player": {"name": f"{team} banc {i}", "pos": "M"}} for i in range(1, 4)
            ],
        }

    return {"errors": [], "response": [eleven("SSC Napoli"), eleven("Bologna FC 1909")]}


# --------------------------------------------------------------------------- #
# Statistiques : zéro, absent, et rien d'inventé
# --------------------------------------------------------------------------- #


def test_a_served_statistic_is_read_with_its_value() -> None:
    rows = parse_statistics(
        _statistics_payload(), match_date=MATCH_DAY, home_team="SSC Napoli"
    )
    assert len(rows) == 2
    home = next(r for r in rows if r.team == "SSC Napoli")
    assert home.xg == 2.31
    assert home.shots == 14
    assert home.home is True
    assert home.opponent == "Bologna FC 1909"


def test_zero_is_a_value_and_absence_is_not_zero() -> None:
    """Une équipe qui a tiré zéro fois a tiré zéro fois."""
    rows = parse_statistics(
        _statistics_payload(zero_shots=True), match_date=MATCH_DAY
    )
    home = rows[0]
    assert home.shots == 0, "zéro tir est un fait, pas une donnée manquante"
    assert home.shots_on_target == 0
    # Red Cards is served as null: the key exists, the value does not.
    assert home.red_cards is None, "un champ nul reste nul, il ne devient pas zéro"
    assert "shots" in home.served()
    assert "red_cards" not in home.served()


def test_a_field_the_plan_does_not_serve_stays_none() -> None:
    rows = parse_statistics(_statistics_payload(with_xg=False), match_date=MATCH_DAY)
    assert all(row.xg is None for row in rows)
    assert all("xg" not in row.served() for row in rows)
    assert all(row.shots is not None for row in rows), "le reste est bien lu"


def test_a_statistics_row_with_nothing_served_says_so_without_a_value() -> None:
    empty = MatchStatistics(
        team="SSC Napoli", opponent="Bologna FC 1909", date=MATCH_DAY, home=True
    )
    evidence = empty.evidence(dt.datetime.now(UTC))
    assert evidence.status is Confidence.UNAVAILABLE
    assert evidence.value is None, "un fait indisponible ne porte aucune valeur"
    assert "aucun champ statistique" in (evidence.note or "")


def test_no_xg_row_is_produced_from_a_half_served_match() -> None:
    """Une ligne xG dont un côté serait inventé vaut moins que pas de ligne."""
    provider = ApiFootballProvider(
        token="clé-de-test",
        fetch=_recorded(
            {
                "fixtures": {"response": [{"fixture": {"id": 5}, "teams": {
                    "home": {"name": "SSC Napoli"},
                    "away": {"name": "Bologna FC 1909"}}}]},
                "statistics": {
                    "errors": [],
                    "response": [
                        {
                            "team": {"name": "SSC Napoli"},
                            "statistics": [{"type": "expected_goals", "value": "2.31"}],
                        },
                        {"team": {"name": "Bologna FC 1909"}, "statistics": []},
                    ],
                },
            }
        ),
    )
    fixture = Fixture("SSC Napoli", "Bologna FC 1909", MATCH_DAY, competition="it.1")
    assert provider.xg_rows(fixture) == ()


# --------------------------------------------------------------------------- #
# Couverture champ par champ
# --------------------------------------------------------------------------- #


def test_coverage_is_established_field_by_field_not_by_a_generic_indicator() -> None:
    """« Des statistiques sont disponibles » ne confirme pas les xG."""
    responses = {
        ("it.1", "2026-27"): _statistics_payload(with_xg=True),
        ("en.1", "2026-27"): _statistics_payload(with_xg=False),
    }
    found = field_coverage(responses)
    by_key = {(e.competition, e.field): e for e in found}
    assert by_key[("it.1", "xg")].state is FieldState.SERVED
    assert by_key[("en.1", "xg")].state is FieldState.ABSENT, (
        "la même réponse sert les tirs sans servir les xG"
    )
    assert by_key[("en.1", "shots")].state is FieldState.SERVED
    # Red Cards is present with null values everywhere: a third, distinct state.
    assert by_key[("it.1", "red_cards")].state is FieldState.PRESENT_BUT_NULL
    assert by_key[("it.1", "npxg")].state is FieldState.ABSENT
    assert all(entry.sampled == 2 for entry in found), "le verdict dit sur quoi il porte"


def test_the_coverage_table_names_what_the_account_does_not_get() -> None:
    matrix = CoverageMatrix(
        entries=field_coverage({("it.1", "2026-27"): _statistics_payload()})
    )
    rendered = matrix.render()
    assert "COUVERTURE VÉRIFIÉE CHAMP PAR CHAMP" in rendered
    assert "champs non servis à ce compte" in rendered
    assert "npxg" in rendered
    assert "big_chances" in rendered


def test_without_a_key_no_coverage_is_claimed() -> None:
    matrix = ApiFootballProvider(token="").coverage(["it.1"], ["2026-27"])
    assert matrix.entries == ()
    assert CREDENTIAL in matrix.render()
    assert "rien n'a été demandé" in matrix.render()


# --------------------------------------------------------------------------- #
# Absences : le doute n'est pas une absence
# --------------------------------------------------------------------------- #


def test_a_doubt_is_recorded_as_probable_and_an_absence_as_confirmed() -> None:
    rows = parse_injuries(_injuries_payload(), as_of=dt.datetime.now(UTC))
    assert len(rows) == 2
    meret = next(r for r in rows if r.player == "Alex Meret")
    orsolini = next(r for r in rows if r.player == "Riccardo Orsolini")
    assert meret.status is Confidence.CONFIRMED
    assert meret.role == "gardien", "le poste surveillé est reconnu"
    assert orsolini.status is Confidence.PROBABLE, (
        "traiter un doute comme une absence fabriquerait un scénario documenté"
    )


def test_an_absence_carries_its_publication_instant() -> None:
    published = dt.datetime(2026, 9, 13, 10, 0, tzinfo=UTC)
    rows = parse_injuries(_injuries_payload(), as_of=published)
    assert all(row.published_at == published for row in rows)
    assert rows[0].available_at(published + dt.timedelta(minutes=1))
    assert not rows[0].available_at(published - dt.timedelta(hours=2)), (
        "une absence publiée à 10:00 n'est pas connue d'une analyse de 08:00"
    )


def test_no_declared_absence_is_not_proof_of_a_full_squad() -> None:
    provider = ApiFootballProvider(
        token="clé-de-test",
        fetch=_recorded(
            {
                "fixtures": {"response": [{"fixture": {"id": 5}, "teams": {
                    "home": {"name": "SSC Napoli"},
                    "away": {"name": "Bologna FC 1909"}}}]},
                "injuries": {"errors": [], "response": []},
            }
        ),
    )
    fixture = Fixture("SSC Napoli", "Bologna FC 1909", MATCH_DAY, competition="it.1")
    rows, evidence = provider.absences(fixture)
    assert rows == ()
    assert evidence[0].status is Confidence.UNAVAILABLE
    assert evidence[0].value is None
    assert "n'est pas la preuve" in (evidence[0].note or "")


# --------------------------------------------------------------------------- #
# Compositions
# --------------------------------------------------------------------------- #


def test_a_team_sheet_is_read_with_its_starters_and_its_bench() -> None:
    rows = parse_lineups(_lineups_payload(), match_date=MATCH_DAY)
    assert len(rows) == 28
    starters = [r for r in rows if r.starting]
    assert len(starters) == 22
    keeper = next(r for r in starters if r.role == "gardien")
    assert keeper.player.endswith(" 1")
    assert {r.team for r in rows} == {"SSC Napoli", "Bologna FC 1909"}
    napoli = next(r for r in rows if r.team == "SSC Napoli")
    assert napoli.opponent == "Bologna FC 1909"


def test_an_unknown_position_keeps_its_own_text() -> None:
    payload = {
        "response": [
            {
                "team": {"name": "SSC Napoli"},
                "startXI": [{"player": {"name": "X", "pos": "SW"}}],
            }
        ]
    }
    rows = parse_lineups(payload, match_date=MATCH_DAY)
    assert rows[0].role == "sw", (
        "forcer un poste inconnu dans un rôle surveillé créerait ou supprimerait "
        "un scénario d'absence décisive"
    )


def test_the_adapter_satisfies_the_lineup_contract() -> None:
    assert isinstance(ApiFootballProvider(token="clé-de-test"), LineupSource)
    assert Capability.INJURIES in ApiFootballProvider().capabilities
    assert Capability.ADVANCED_STATS in ApiFootballProvider().capabilities


# --------------------------------------------------------------------------- #
# Clé, quota, refus
# --------------------------------------------------------------------------- #


def test_without_a_key_the_probe_says_so_and_calls_nothing() -> None:
    calls: list[str] = []

    def never(url: str, **_kwargs: object) -> tuple[object, dt.datetime]:
        calls.append(url)
        raise AssertionError("aucune requête ne doit partir sans clé")

    status = ApiFootballProvider(token="", fetch=never).probe()
    assert status.reachability is Reachability.BLOCKED
    assert CREDENTIAL in status.detail
    assert calls == []


def test_an_error_object_returned_with_http_200_is_a_refusal() -> None:
    """Clé invalide ou quota épuisé : refus explicite, pas « ligue vide »."""
    provider = ApiFootballProvider(
        token="clé-expirée",
        fetch=_recorded({"fixtures": {"errors": {"token": "invalid api key"}}}),
    )
    status = provider.probe()
    assert status.reachability is Reachability.BLOCKED
    assert "invalid api key" in status.detail


def test_a_quota_message_is_a_refusal_too() -> None:
    provider = ApiFootballProvider(
        token="clé-de-test",
        fetch=_recorded(
            {"fixtures": {"errors": {"requests": "You have reached your daily limit"}}}
        ),
    )
    status = provider.probe()
    assert status.reachability is Reachability.BLOCKED
    assert "daily limit" in status.detail


def test_no_key_ever_reaches_a_report() -> None:
    secret = "clé-très-secrète-0123456789"
    provider = ApiFootballProvider(token=secret)
    status = provider.probe()
    assert secret not in status.detail
    assert secret not in status.render()
    assert secret not in provider.coverage(["it.1"], ["2026-27"]).render()


def test_the_cache_is_read_on_the_adapter_s_own_clock() -> None:
    clock = {"t": dt.datetime(2026, 9, 13, 18, 0, tzinfo=UTC)}
    calls: list[str] = []

    def counting(url: str, **_kwargs: object) -> tuple[object, dt.datetime]:
        calls.append(url)
        return (_lineups_payload(), clock["t"])

    with tempfile.TemporaryDirectory() as folder:
        provider = ApiFootballProvider(
            Cache(folder, ttl_seconds=120),
            token="clé-de-test",
            now=lambda: clock["t"],
            fetch=counting,
        )
        provider._get("/fixtures/lineups?fixture=5")
        provider._get("/fixtures/lineups?fixture=5")
        assert len(calls) == 1, "la seconde lecture vient du cache"
        clock["t"] = clock["t"] + dt.timedelta(minutes=5)
        provider._get("/fixtures/lineups?fixture=5")
        assert len(calls) == 2, "après expiration, la requête est refaite"


def test_the_competitions_are_the_ones_the_engine_names() -> None:
    assert set(COMPETITION_IDS) == {"en.1", "es.1", "de.1", "it.1", "fr.1"}
    assert all(isinstance(value, int) for value in COMPETITION_IDS.values())


def _recorded(
    payloads: dict[str, Any]
) -> Callable[..., tuple[object, dt.datetime]]:
    """A fetcher serving one recorded payload per endpoint family."""

    def fetch(url: str, **_kwargs: object) -> tuple[object, dt.datetime]:
        for name, payload in payloads.items():
            if name in url:
                return (payload, dt.datetime(2026, 9, 13, 18, 0, tzinfo=UTC))
        raise ProviderBlockedError(f"réponse non enregistrée pour {url}")

    return fetch


# --------------------------------------------------------------------------- #
# Ce que l'adaptateur refuse de déduire
# --------------------------------------------------------------------------- #


def test_match_totals_are_never_turned_into_eleven_versus_eleven_figures() -> None:
    """R08, R09 et R14 restent non développées : les totaux ne les servent pas.

    L'API donne des totaux de match. Les xG à onze contre onze, la performance
    selon l'état du score et les fins de match demandent la chronologie des
    événements. Fabriquer les seconds à partir des premiers produirait un chiffre
    d'apparence rigoureuse et sans fondement.
    """
    rows = parse_statistics(_statistics_payload(), match_date=MATCH_DAY)
    for row in rows:
        assert not hasattr(row, "xg_eleven_v_eleven")
        assert not hasattr(row, "xg_when_leading")
        assert not hasattr(row, "late_game_xg")

    derived = {8: "Penalties, exclusions", 9: "onze contre onze", 14: "fins de match"}
    for number in derived:
        rubric = next(r for r in RUBRICS if r.number == number)
        assert not rubric.adapter, (
            f"R{number:02d} ne doit être rattachée à aucun adaptateur : rien ne "
            f"la sert, et l'y rattacher annoncerait une donnée déductible"
        )

    for number in (7, 11):
        rubric = next(r for r in RUBRICS if r.number == number)
        assert rubric.adapter == "foot.collect.apifootball", (
            f"R{number:02d} est bien servie par cet adaptateur"
        )


def test_a_complementary_source_can_still_be_plugged_in() -> None:
    """Pour ce qui n'est pas servi, l'opérateur garde une porte d'entrée."""
    for number in (7, 10, 11, 21):
        rubric = next(r for r in RUBRICS if r.number == number)
        assert rubric.operator_import, (
            f"R{number:02d} doit rester renseignable à la main quand l'API ne la sert pas"
        )
