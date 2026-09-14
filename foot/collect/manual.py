"""Manual import — the documented fallback when no provider can be reached.

Every automated source can fail: a policy blocks the host, a key expires, a
feed changes shape.  When that happens the operator must still be able to run a
real analysis, so the system accepts hand-supplied data through the same
:class:`~foot.collect.base.Provider` contract as everything else, and stamps it
with the same provenance.

Manual data is marked with an explicit source name so it can never be mistaken
in a report for something that was fetched and cross-checked.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

from foot.collect.base import (
    Capability,
    CollectionError,
    ProviderStatus,
    Reachability,
    ResultSet,
    SeasonData,
)
from foot.data.csv_source import load_fixtures, load_matches, load_odds
from foot.domain import Fixture, MatchLog
from foot.market.odds import MatchOdds
from foot.provenance import Confidence, Evidence, Source, utcnow

__all__ = ["ManualProvider", "parse_odds_line"]


class ManualProvider:
    """Results, fixtures and odds supplied by the operator.

    Args:
        results_csv: a football-data.co.uk style file of played matches.
        odds_csv: a file carrying bookmaker columns for the same fixtures.
        label: how the source should appear in reports.
    """

    __slots__ = (
        "_competition_key",
        "_fixtures",
        "_fixtures_csv",
        "_label",
        "_odds",
        "_odds_csv",
        "_odds_quoted_at",
        "_results",
        "_results_csv",
    )

    def __init__(
        self,
        *,
        results_csv: str | Path | None = None,
        odds_csv: str | Path | None = None,
        results: MatchLog | None = None,
        fixtures: Sequence[Fixture] = (),
        odds: Mapping[Fixture, MatchOdds] | None = None,
        odds_quoted_at: dt.datetime | None = None,
        fixtures_csv: str | Path | None = None,
        label: str = "saisie manuelle",
        competition_key: str = "manuel",
    ) -> None:
        self._label = label
        self._competition_key = competition_key
        self._results_csv = Path(results_csv) if results_csv else None
        self._odds_csv = Path(odds_csv) if odds_csv else None
        self._results = results
        self._fixtures_csv = Path(fixtures_csv) if fixtures_csv else None
        self._fixtures = tuple(fixtures)
        self._odds = dict(odds or {})
        self._odds_quoted_at = odds_quoted_at

    @property
    def name(self) -> str:
        return self._label

    @property
    def upstream(self) -> str:
        return "opérateur"

    @property
    def capabilities(self) -> frozenset[Capability]:
        caps = set()
        if self._results_csv or self._results is not None:
            caps.add(Capability.RESULTS)
        if self._fixtures or self._fixtures_csv:
            caps.add(Capability.FIXTURES)
        if self._odds_csv or self._odds:
            caps.add(Capability.ODDS)
        return frozenset(caps)

    def competitions(self) -> Sequence[str]:
        return (self._competition_key,)

    def season(self, competition: str, season: str = "") -> SeasonData:
        """Serve the operator's own data through the engine's provider contract.

        Without this the CSV options were advertised but fed nothing: the engine
        consumes :class:`~foot.collect.base.SeasonSource`, and a provider that
        cannot answer :meth:`season` is silently skipped.
        """
        if competition != self._competition_key:
            raise CollectionError(
                f"compétition inconnue pour l'import manuel : {competition!r} "
                f"(disponible : {self._competition_key})"
            )
        matches = self._results
        if matches is None and self._results_csv is not None:
            if not self._results_csv.exists():
                raise CollectionError(f"fichier de résultats introuvable : {self._results_csv}")
            # Stamped with the provider's own competition key, exactly as the
            # calendar already is: a provider that declares it serves ``it.1``
            # must not return matches labelled with nothing. Without this, a
            # later measurement cannot join a forecast to its own result.
            matches = load_matches(self._results_csv, competition=self._competition_key)
        if matches is None:
            matches = MatchLog()
        return SeasonData(
            competition=self._competition_key,
            season=season,
            label=self._label,
            played=matches,
            fixtures=self.fixtures(),
            retrieved_at=utcnow(),
            url=str(self._results_csv) if self._results_csv else "",
        )

    def source(self) -> Source:
        return Source(name=self._label, provider="opérateur", official=False)

    def probe(self) -> ProviderStatus:
        missing = [
            str(path)
            for path in (self._results_csv, self._odds_csv)
            if path is not None and not path.exists()
        ]
        return ProviderStatus(
            provider=self.name,
            reachability=Reachability.ERROR if missing else Reachability.OK,
            capabilities=self.capabilities,
            checked_at=utcnow(),
            detail=f"fichiers introuvables : {', '.join(missing)}" if missing else "",
            coverage=("données fournies par l'opérateur",),
        )

    def results(self, competition: str = "manuel", season: str = "") -> ResultSet:
        scope = f"{competition}::{season}" if season else competition
        matches = self._results
        if matches is None and self._results_csv is not None:
            if not self._results_csv.exists():
                raise CollectionError(f"fichier de résultats introuvable : {self._results_csv}")
            matches = load_matches(self._results_csv)
        if matches is None:
            raise CollectionError("aucun résultat fourni manuellement")
        now = utcnow()
        return ResultSet(
            matches=matches,
            evidence=(
                Evidence(
                    key=f"résultats::{scope}",
                    value=f"{len(matches)} matchs",
                    source=self.source(),
                    retrieved_at=now,
                    status=Confidence.PROBABLE,
                    fact_date=matches.end if matches else None,
                    note="import manuel : non recoupé automatiquement",
                ),
            ),
            source=self.source(),
            retrieved_at=now,
        )

    def fixtures(self) -> tuple[Fixture, ...]:
        """Fixtures supplied inline, plus any read from the calendar file.

        Giving the operator a calendar route matters where no automatic one
        exists: without a verifiable fixture, a match can never leave the
        "unverified" state, and nothing is ever recommended for it.
        """
        if self._fixtures_csv is None:
            return self._fixtures
        if not self._fixtures_csv.exists():
            raise CollectionError(f"calendrier introuvable : {self._fixtures_csv}")
        loaded = load_fixtures(self._fixtures_csv, competition=self._competition_key)
        seen = {(f.date, f.home, f.away) for f in self._fixtures}
        return (
            *self._fixtures,
            *(f for f in loaded if (f.date, f.home, f.away) not in seen),
        )

    def quoted_at(self) -> dt.datetime | None:
        """When the supplied prices were observed, when the operator says so."""
        return self._odds_quoted_at

    def odds(self) -> tuple[dict[Fixture, MatchOdds], list[Evidence]]:
        """Operator-supplied prices, with evidence naming them as such."""
        book = dict(self._odds)
        now = self._odds_quoted_at or utcnow()
        if self._odds_csv is not None:
            if not self._odds_csv.exists():
                raise CollectionError(f"fichier de cotes introuvable : {self._odds_csv}")
            book.update(load_odds(self._odds_csv))
        evidence = [
            Evidence(
                key=f"cote::{fixture.home} vs {fixture.away}",
                value=str(quote),
                source=self.source(),
                retrieved_at=now,
                status=Confidence.PROBABLE,
                fact_date=fixture.date,
                note=(
                    "cote fournie par l'opérateur ; relevée le "
                    + (
                        self._odds_quoted_at.strftime("%Y-%m-%d %H:%M %Z")
                        if self._odds_quoted_at is not None
                        else "(heure de relevé non déclarée : "
                        "l'ancienneté du prix ne peut pas être vérifiée)"
                    )
                ),
            )
            for fixture, quote in book.items()
        ]
        return book, evidence


def parse_odds_line(text: str) -> tuple[float, float, float] | None:
    """Parse ``"2.10 3.40 3.60"`` or ``"2,10/3,40/3,60"`` into three prices.

    Returns ``None`` rather than raising when the text is not a price triple, so
    a free-text match line can carry optional odds without a separate field.
    """
    cleaned = text.replace(",", ".").replace("/", " ").replace(";", " ").replace("|", " ")
    parts = [p for p in cleaned.split() if p]
    if len(parts) != 3:
        return None
    try:
        values = tuple(float(p) for p in parts)
    except ValueError:
        return None
    if not all(1.0 < v < 1000.0 for v in values):
        return None
    return values  # type: ignore[return-value]


def odds_from_pairs(
    pairs: Iterable[tuple[Fixture, tuple[float, float, float]]],
    *,
    bookmaker: str | None = None,
    quoted_at: dt.datetime | None = None,
) -> tuple[dict[Fixture, MatchOdds], list[Evidence]]:
    """Build a book plus its evidence from ``(fixture, (h, d, a))`` pairs."""
    now = quoted_at or utcnow()
    book: dict[Fixture, MatchOdds] = {}
    evidence: list[Evidence] = []
    source = Source(name=bookmaker or "cotes fournies", provider="opérateur")
    for fixture, prices in pairs:
        quote = MatchOdds(prices[0], prices[1], prices[2], bookmaker=bookmaker)
        book[fixture] = quote
        evidence.append(
            Evidence(
                key=f"cote::{fixture.home} vs {fixture.away}",
                value=str(quote),
                source=source,
                retrieved_at=now,
                status=Confidence.PROBABLE,
                fact_date=fixture.date,
                note="saisie opérateur",
            )
        )
    return book, evidence
