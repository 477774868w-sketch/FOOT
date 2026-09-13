"""The provider registry: what is available, measured rather than declared.

The engine never asks "is there an odds provider?" and hopes.  It asks the
registry, which probes and answers with a :class:`ProviderStatus` per provider.
A capability with no reachable provider is reported as unavailable, and that
unavailability travels all the way into the match report.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field

from foot.collect.base import Capability, Provider, ProviderStatus, Reachability
from foot.provenance import utcnow

__all__ = ["Registry", "RegistryReport"]


@dataclass(frozen=True, slots=True)
class RegistryReport:
    """The measured state of every registered provider."""

    statuses: tuple[ProviderStatus, ...]
    probed_at: dt.datetime = field(default_factory=utcnow)

    def usable(self) -> tuple[ProviderStatus, ...]:
        return tuple(s for s in self.statuses if s.usable)

    def blocked(self) -> tuple[ProviderStatus, ...]:
        return tuple(s for s in self.statuses if s.reachability is Reachability.BLOCKED)

    def providers_for(self, capability: Capability) -> tuple[str, ...]:
        return tuple(
            s.provider for s in self.statuses if s.usable and capability in s.capabilities
        )

    def missing_capabilities(self) -> tuple[Capability, ...]:
        """Capabilities no reachable provider can supply."""
        served = {c for s in self.statuses if s.usable for c in s.capabilities}
        return tuple(c for c in Capability if c not in served)

    def render(self) -> str:
        lines = [
            f"Fournisseurs sondés le {self.probed_at.strftime('%Y-%m-%d %H:%M %Z')}",
            "-" * 78,
        ]
        lines.extend(status.render() for status in self.statuses)
        missing = self.missing_capabilities()
        if missing:
            lines.append("-" * 78)
            lines.append(
                "Capacités sans fournisseur accessible : "
                + ", ".join(c.value for c in missing)
            )
            lines.append(
                "  → les rubriques correspondantes seront marquées indisponibles, "
                "jamais comblées par une valeur inventée."
            )
        return "\n".join(lines)

    def __str__(self) -> str:
        return self.render()


class Registry:
    """A collection of providers that can be probed as a group."""

    __slots__ = ("_providers",)

    def __init__(self, providers: Sequence[Provider] = ()) -> None:
        self._providers: list[Provider] = list(providers)

    def add(self, provider: Provider) -> Provider:
        self._providers.append(provider)
        return provider

    def __iter__(self) -> Iterator[Provider]:
        return iter(self._providers)

    def __len__(self) -> int:
        return len(self._providers)

    def by_name(self, name: str) -> Provider | None:
        for provider in self._providers:
            if provider.name == name:
                return provider
        return None

    def claiming(self, capability: Capability) -> list[Provider]:
        """Providers that *claim* the capability, before any probe."""
        return [p for p in self._providers if capability in p.capabilities]

    def probe(self) -> RegistryReport:
        """Probe every provider; a provider that raises is reported, not fatal."""
        statuses: list[ProviderStatus] = []
        for provider in self._providers:
            try:
                statuses.append(provider.probe())
            except Exception as error:
                statuses.append(
                    ProviderStatus(
                        provider=provider.name,
                        reachability=Reachability.ERROR,
                        capabilities=provider.capabilities,
                        checked_at=utcnow(),
                        detail=f"{type(error).__name__} : {error}",
                    )
                )
        return RegistryReport(statuses=tuple(statuses))

    def first_usable(
        self, capability: Capability, report: RegistryReport
    ) -> Provider | None:
        """The first probed-usable provider offering ``capability``."""
        names = set(report.providers_for(capability))
        for provider in self._providers:
            if provider.name in names:
                return provider
        return None
