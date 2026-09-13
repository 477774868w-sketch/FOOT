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
    Reachability,
    ResultSet,
)
from foot.collect.cache import Cache, CacheEntry
from foot.collect.footballdata import DIVISIONS, FootballDataProvider
from foot.collect.http import Response, fetch, fetch_json
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
    "Reachability",
    "Registry",
    "RegistryReport",
    "Response",
    "ResultSet",
    "SeasonData",
    "fetch",
    "fetch_json",
    "odds_from_pairs",
    "parse_odds_line",
    "resolve_competition",
]
