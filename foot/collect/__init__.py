"""Collection: everything that talks to the outside world.

This package is deliberately the only one that performs I/O.  The modelling
packages receive :class:`~foot.provenance.Evidence` and never a URL, which is
what makes the analysis reproducible from a cache and testable without a
network.
"""

from foot.collect.base import (
    Capability,
    CollectionError,
    Provider,
    ProviderBlockedError,
    ProviderStatus,
    QuotaReport,
    Reachability,
    ResultSet,
    redact_url,
)
from foot.collect.cache import Cache, CacheEntry
from foot.collect.footballdata import DIVISIONS, FootballDataProvider
from foot.collect.http import Response, fetch, fetch_json, fetch_json_headers
from foot.collect.manual import ManualProvider, odds_from_pairs, parse_odds_line
from foot.collect.openfootball import (
    COMPETITIONS,
    OpenFootballProvider,
    SeasonData,
    resolve_competition,
)
from foot.collect.registry import Registry, RegistryReport

__all__ = [
    "COMPETITIONS",
    "DIVISIONS",
    "Cache",
    "CacheEntry",
    "Capability",
    "CollectionError",
    "FootballDataProvider",
    "ManualProvider",
    "OpenFootballProvider",
    "Provider",
    "ProviderBlockedError",
    "ProviderStatus",
    "QuotaReport",
    "Reachability",
    "Registry",
    "RegistryReport",
    "Response",
    "ResultSet",
    "SeasonData",
    "fetch",
    "fetch_json",
    "fetch_json_headers",
    "odds_from_pairs",
    "parse_odds_line",
    "redact_url",
    "resolve_competition",
]
