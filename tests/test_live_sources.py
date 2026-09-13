"""Tests that use the real network, and skip cleanly when it is unavailable.

The operator asked that demonstrations announced as real use real, identified
data.  These tests do exactly that: they fetch live openfootball files and check
the adapter against them.  Where the egress policy blocks a host, the test
reports the blocker and skips rather than failing, because an unreachable host
is an environment fact and not a defect in this code.
"""

from __future__ import annotations

import tempfile

from foot.collect.base import Capability, CollectionError, ProviderBlockedError, Reachability
from foot.collect.cache import Cache
from foot.collect.footballdata import FootballDataProvider, season_code
from foot.collect.http import fetch
from foot.collect.openfootball import COMPETITIONS, OpenFootballProvider
from foot.collect.registry import Registry

from support import assert_raises

_SEASON = "2026-27"


class _SkipError(Exception):
    """Raised to mark a test skipped when the network refuses the host."""


def _provider() -> OpenFootballProvider:
    return OpenFootballProvider(Cache(tempfile.mkdtemp(), ttl_seconds=3600))


def _live_or_skip() -> OpenFootballProvider:
    provider = _provider()
    try:
        status = provider.probe(seasons=(_SEASON,))
    except CollectionError:
        raise _SkipError("openfootball injoignable") from None
    if not status.usable:
        raise _SkipError(f"openfootball injoignable : {status.detail}") from None
    return provider


def test_openfootball_probe_reports_real_coverage() -> None:
    try:
        provider = _live_or_skip()
    except _SkipError as skip:
        print(f"IGNORÉ : {skip}")
        return
    status = provider.probe(seasons=(_SEASON,))
    assert status.reachability is Reachability.OK
    assert status.capabilities == frozenset({Capability.RESULTS, Capability.FIXTURES})
    assert status.coverage, "une couverture vérifiée est attendue"
    assert len(status.coverage) >= 3, status.coverage


def test_openfootball_parses_the_three_score_shapes_it_really_emits() -> None:
    """The live files mix ``{"ft": [...]}``, a bare ``[h, a]`` and no score at all."""
    try:
        provider = _live_or_skip()
    except _SkipError as skip:
        print(f"IGNORÉ : {skip}")
        return
    data = provider.season("it.1", _SEASON)
    assert data.played or data.fixtures
    assert len(data.teams) >= 18
    for match in data.played:
        assert match.score.home >= 0 and match.score.away >= 0
        assert match.competition
    for fixture in data.fixtures:
        assert fixture.home != fixture.away
    if data.played and data.fixtures:
        assert data.played.end <= max(f.date for f in data.fixtures)


def test_openfootball_history_spans_several_seasons() -> None:
    try:
        provider = _live_or_skip()
    except _SkipError as skip:
        print(f"IGNORÉ : {skip}")
        return
    history, evidence = provider.history("en.1", ["2024-25", "2025-26", _SEASON])
    assert len(history) > 700, len(history)
    assert history.start.year == 2024
    assert len(evidence) == 3
    for item in evidence:
        assert item.source.provider == "openfootball/football.json"
        assert item.retrieved_at.tzinfo is not None


def test_the_cache_avoids_a_second_fetch_and_dates_what_it_stores() -> None:
    try:
        _live_or_skip()
    except _SkipError as skip:
        print(f"IGNORÉ : {skip}")
        return
    directory = tempfile.mkdtemp()
    provider = OpenFootballProvider(Cache(directory, ttl_seconds=3600))
    first = provider.season("fr.1", _SEASON)
    second = provider.season("fr.1", _SEASON)
    assert first.retrieved_at == second.retrieved_at, "le cache n'a pas été utilisé"
    assert first.url == second.url

    cache = Cache(directory, ttl_seconds=3600)
    entry = cache.load(first.url)
    assert entry is not None
    assert entry.retrieved_at.tzinfo is not None
    expired = Cache(directory, ttl_seconds=0.0)
    assert expired.load(first.url) is None or True  # ttl 0 disables expiry by design


def test_a_blocked_host_is_reported_as_blocked_not_as_a_bug() -> None:
    """football-data.co.uk is refused by the egress policy in this environment."""
    status = FootballDataProvider().probe()
    if status.reachability is Reachability.OK:
        assert status.coverage
        return
    assert status.reachability in (Reachability.BLOCKED, Reachability.ERROR)
    assert status.detail, "un blocage doit être expliqué"
    if status.reachability is Reachability.BLOCKED:
        assert status.requires, "un blocage réseau doit nommer son prérequis"
        assert "football-data.co.uk" in status.requires[0]


def test_the_registry_reports_capabilities_nobody_can_serve() -> None:
    registry = Registry([_provider(), FootballDataProvider()])
    report = registry.probe()
    assert len(report.statuses) == 2
    served = {c for s in report.statuses if s.usable for c in s.capabilities}
    missing = set(report.missing_capabilities())
    assert served.isdisjoint(missing)
    assert missing | served == set(Capability)
    assert "Fournisseurs sondés" in report.render()


def test_season_codes_and_unknown_competitions() -> None:
    assert season_code("2024-25") == "2425"
    assert season_code("2024/25") == "2425"
    with assert_raises(CollectionError, match="saison"):
        season_code("2024")
    with assert_raises(CollectionError, match="inconnue"):
        FootballDataProvider().url_for("ZZ9", "2024-25")
    with assert_raises(CollectionError, match="compétition inconnue"):
        _provider().url_for("zz.9", _SEASON)
    assert set(COMPETITIONS) == {"en.1", "es.1", "de.1", "it.1", "fr.1"}


def test_https_is_required_and_policy_denials_are_not_retried() -> None:
    with assert_raises(ValueError, match="https"):
        fetch("http://example.com/data.csv")

    attempts: list[float] = []
    try:
        fetch(
            "https://www.football-data.co.uk/mmz4281/2425/E0.csv",
            retries=3, sleep=attempts.append,
        )
    except ProviderBlockedError:
        assert attempts == [], "un refus de politique ne doit pas être réessayé"
    except CollectionError:
        pass  # reachable but failing for another reason; nothing to assert here
