"""Adaptateur The Odds API : des prix d'**avant-match**, avec leur heure de relevé.

C'est la seule voie automatique testée ici vers des cotes disponibles au moment
de décider.  football-data.co.uk publie des cotes d'ouverture et de clôture, mais
après coup : elles servent à mesurer, jamais à choisir.

Trois propriétés sont tenues de bout en bout, parce que le reste du logiciel en
dépend :

* **chaque prix garde le bookmaker qui l'a donné.** Une cote d'un autre
  bookmaker que celui choisi est utilisable, mais elle doit être identifiée
  comme telle : jouer un prix qu'on ne peut pas prendre chez soi est une erreur
  coûteuse ;
* **chaque prix garde son heure de relevé**, celle que l'API déclare
  (``last_update``), jamais l'heure de l'analyse ;
* **le règlement est nommé** : 1–N–2, totaux et handicaps sont convertis en clés
  du catalogue interne, et un marché que le catalogue ne sait pas régler est
  laissé de côté plutôt qu'approximé.

Le parsing est séparé du réseau (:func:`parse_odds`), donc testable sur des
réponses enregistrées, sans clé ni connexion.  Ce que **votre** compte obtient
réellement, seule :meth:`OddsApiProvider.probe` peut le dire : le plan gratuit
plafonne à 500 requêtes par mois et chaque requête coûte d'autant plus de
crédits qu'elle demande de marchés.
"""

from __future__ import annotations

import datetime as dt
import os
import urllib.parse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from foot.analysis.naming import normalise
from foot.collect.base import (
    Capability,
    CollectionError,
    ProviderBlockedError,
    ProviderStatus,
    Reachability,
)
from foot.collect.cache import Cache
from foot.collect.http import fetch_json
from foot.domain import Fixture
from foot.provenance import Confidence, Evidence, Source, utcnow

__all__ = [
    "CREDENTIAL",
    "SPORT_KEYS",
    "OddsApiProvider",
    "QuotedPrice",
    "parse_odds",
]

CREDENTIAL = "ODDS_API_KEY"
"""Environment variable holding the API key. Never hard-code a key."""

_BASE = "https://api.the-odds-api.com/v4"

SPORT_KEYS: Mapping[str, str] = {
    "en.1": "soccer_epl",
    "es.1": "soccer_spain_la_liga",
    "de.1": "soccer_germany_bundesliga",
    "it.1": "soccer_italy_serie_a",
    "fr.1": "soccer_france_ligue_one",
}


@dataclass(frozen=True, slots=True)
class QuotedPrice:
    """One price, for one market, from one bookmaker, at one instant."""

    fixture: Fixture
    market_key: str
    """Key in the engine's own catalogue — ``1X2:H``, ``OU:2.5:over``…"""

    price: float
    bookmaker: str
    quoted_at: dt.datetime
    """When the bookmaker last updated it, as the API reports it."""

    requested_bookmaker: str = ""
    """The book the operator asked for, when they named one."""

    @property
    def from_requested_book(self) -> bool:
        return (
            not self.requested_bookmaker
            or self.bookmaker.lower() == self.requested_bookmaker.lower()
        )

    @property
    def label(self) -> str:
        if self.from_requested_book:
            return self.bookmaker
        return f"{self.bookmaker} (≠ {self.requested_bookmaker} demandé)"

    def evidence(self) -> Evidence:
        return Evidence(
            key=f"cote::{self.market_key}::{self.fixture.home} vs {self.fixture.away}",
            value=f"{self.price:.2f}",
            source=Source(
                name=self.bookmaker, provider="The Odds API", url=_BASE, official=False
            ),
            retrieved_at=self.quoted_at,
            status=Confidence.PROBABLE,
            fact_date=self.fixture.date,
            note=(
                f"prix {self.label}, relevé "
                f"{self.quoted_at.strftime('%Y-%m-%d %H:%M %Z')}"
            ),
        )


def _text(value: object, default: str = "") -> str:
    return value.strip() if isinstance(value, str) else default


def _moment(value: object) -> dt.datetime | None:
    text = _text(value)
    if not text:
        return None
    try:
        return dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _market_key(  # noqa: PLR0911 - one exit per market shape, each explicit
    market: str, outcome: Mapping[str, Any], home: str, away: str
) -> str:
    """Map one API outcome onto the engine's settlement catalogue.

    Returns an empty key for anything the catalogue cannot settle exactly —
    better no price than a price attached to the wrong settlement rule.
    """
    name = _text(outcome.get("name"))
    point = outcome.get("point")
    if market == "h2h":
        if name == home:
            return "1X2:H"
        if name == away:
            return "1X2:A"
        if name.lower() in ("draw", "tie"):
            return "1X2:D"
        return ""
    if market == "totals" and isinstance(point, (int, float)):
        side = "over" if name.lower().startswith("over") else "under"
        return f"OU:{float(point):g}:{side}"
    if market == "spreads" and isinstance(point, (int, float)):
        side = "H" if name == home else "A" if name == away else ""
        if not side:
            return ""
        return f"AH:{side}:{float(point):+g}"
    return ""


def parse_odds(
    payload: object,
    *,
    competition: str,
    requested_bookmaker: str = "",
) -> tuple[QuotedPrice, ...]:
    """Read one ``/odds`` response into priced markets.

    Every price keeps its own bookmaker and its own ``last_update``: collapsing
    them onto one timestamp is precisely how a stale price passes for a fresh
    one.
    """
    if not isinstance(payload, Sequence) or isinstance(payload, (str, bytes)):
        raise CollectionError("réponse The Odds API inattendue : liste attendue")
    quotes: list[QuotedPrice] = []
    for event in payload:
        if not isinstance(event, Mapping):
            continue
        home = _text(event.get("home_team"))
        away = _text(event.get("away_team"))
        kickoff = _moment(event.get("commence_time"))
        if not home or not away or kickoff is None:
            continue
        fixture = Fixture(
            home=home, away=away, date=kickoff.date(), competition=competition
        )
        books = event.get("bookmakers")
        if not isinstance(books, Sequence):
            continue
        for book in books:
            if not isinstance(book, Mapping):
                continue
            title = _text(book.get("title")) or _text(book.get("key"))
            markets = book.get("markets")
            if not title or not isinstance(markets, Sequence):
                continue
            for market in markets:
                if not isinstance(market, Mapping):
                    continue
                kind = _text(market.get("key"))
                updated = _moment(market.get("last_update")) or _moment(
                    book.get("last_update")
                )
                outcomes = market.get("outcomes")
                if updated is None or not isinstance(outcomes, Sequence):
                    continue
                for outcome in outcomes:
                    if not isinstance(outcome, Mapping):
                        continue
                    price = outcome.get("price")
                    if not isinstance(price, (int, float)) or price <= 1.0:
                        continue
                    key = _market_key(kind, outcome, home, away)
                    if not key:
                        continue
                    quotes.append(
                        QuotedPrice(
                            fixture=fixture,
                            market_key=key,
                            price=float(price),
                            bookmaker=title,
                            quoted_at=updated,
                            requested_bookmaker=requested_bookmaker,
                        )
                    )
    return tuple(quotes)


def best_prices(
    quotes: Sequence[QuotedPrice], *, prefer: str = ""
) -> dict[str, QuotedPrice]:
    """One price per market: the requested book if it quotes it, else the best.

    Preferring the operator's own book over a higher price elsewhere is
    deliberate: a price they cannot actually take is not an opportunity. When
    their book is silent, the best available price is offered **and labelled**
    with whoever gave it.
    """
    chosen: dict[str, QuotedPrice] = {}
    for quote in quotes:
        current = chosen.get(quote.market_key)
        if current is None:
            chosen[quote.market_key] = quote
            continue
        if prefer:
            mine, theirs = quote.from_requested_book, current.from_requested_book
            if mine != theirs:
                chosen[quote.market_key] = quote if mine else current
                continue
        if quote.price > current.price:
            chosen[quote.market_key] = quote
    return chosen


class OddsApiProvider:
    """Pre-match prices from The Odds API, for an account that has a key."""

    __slots__ = ("_bookmaker", "_cache", "_key", "_regions", "_timeout")

    def __init__(
        self,
        cache: Cache | None = None,
        *,
        key: str | None = None,
        bookmaker: str = "",
        regions: str = "eu",
        timeout: float = 20.0,
    ) -> None:
        self._cache = cache
        self._key = key if key is not None else os.environ.get(CREDENTIAL, "")
        self._bookmaker = bookmaker
        self._regions = regions
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "The Odds API"

    @property
    def upstream(self) -> str:
        return "the-odds-api.com (v4)"

    @property
    def configured(self) -> bool:
        return bool(self._key.strip())

    @property
    def capabilities(self) -> frozenset[Capability]:
        return frozenset({Capability.ODDS})

    def competitions(self) -> Sequence[str]:
        return tuple(SPORT_KEYS)

    def _url(self, competition: str, markets: str) -> str:
        if not self.configured:
            raise ProviderBlockedError(
                f"The Odds API : aucune clé configurée. Renseignez {CREDENTIAL} "
                f"(voir « foot config »)."
            )
        sport = SPORT_KEYS.get(competition)
        if sport is None:
            raise CollectionError(
                f"compétition non servie par cet adaptateur : {competition!r}"
            )
        query = urllib.parse.urlencode(
            {
                "apiKey": self._key,
                "regions": self._regions,
                "markets": markets,
                "oddsFormat": "decimal",
            }
        )
        return f"{_BASE}/sports/{sport}/odds?{query}"

    def odds_for(
        self, competition: str, *, markets: str = "h2h,totals"
    ) -> tuple[tuple[QuotedPrice, ...], dt.datetime]:
        """Prices for every upcoming fixture of one competition.

        ``markets`` is deliberately short by default: each extra market costs
        credits, and the free plan allows 500 requests a month.
        """
        url = self._url(competition, markets)
        if self._cache is not None:
            cached = self._cache.load(url)
            if cached is not None:
                return (
                    parse_odds(
                        cached.payload,
                        competition=competition,
                        requested_bookmaker=self._bookmaker,
                    ),
                    cached.retrieved_at,
                )
        payload, retrieved = fetch_json(url, timeout=self._timeout)
        if self._cache is not None:
            self._cache.store(url, payload, retrieved)
        return (
            parse_odds(
                payload,
                competition=competition,
                requested_bookmaker=self._bookmaker,
            ),
            retrieved,
        )

    def probe(self, competition: str = "en.1") -> ProviderStatus:
        """Ask the service what this key actually returns, right now."""
        now = utcnow()
        if not self.configured:
            return ProviderStatus(
                provider=self.name,
                reachability=Reachability.BLOCKED,
                capabilities=frozenset(),
                checked_at=now,
                detail=f"aucune clé dans {CREDENTIAL} — adaptateur écrit, non activé",
            )
        try:
            quotes, retrieved = self.odds_for(competition)
        except ProviderBlockedError as error:
            return ProviderStatus(
                provider=self.name,
                reachability=Reachability.BLOCKED,
                capabilities=frozenset(),
                checked_at=now,
                detail=str(error),
            )
        except CollectionError as error:
            return ProviderStatus(
                provider=self.name,
                reachability=Reachability.ERROR,
                capabilities=frozenset(),
                checked_at=now,
                detail=str(error),
            )
        books = sorted({quote.bookmaker for quote in quotes})
        return ProviderStatus(
            provider=self.name,
            reachability=Reachability.OK,
            capabilities=frozenset({Capability.ODDS}) if quotes else frozenset(),
            checked_at=retrieved,
            detail=(
                f"{len(quotes)} prix sur {len(books)} bookmaker(s) pour {competition}"
                if quotes
                else "réponse acceptée mais vide : aucune rencontre cotée"
            ),
            coverage=tuple(books[:8]),
        )

    # -- MarketSource ------------------------------------------------------
    def market_prices(
        self, fixture: Fixture
    ) -> dict[str, tuple[float, dt.datetime | None, str]]:
        """Every market this API quotes for one fixture, priced and dated.

        Satisfies :class:`~foot.collect.base.MarketSource`, so the engine can
        consult it after sealing the dossier without knowing this class exists.
        A fixture the API does not cover returns nothing — reported as "no price
        available", never as a missing market.

        Club names are matched through :func:`~foot.analysis.naming.normalise`,
        not by string equality: the API says « Arsenal » where our calendar says
        « Arsenal FC », and requiring an exact match meant the price for that
        match simply never appeared. Folding is not guessing — the date and the
        pairing must still agree exactly, and two API fixtures folding onto the
        same pair on the same day are **refused**, because pricing one of them
        arbitrarily is worse than quoting nothing.
        """
        competition = fixture.competition or ""
        if competition not in SPORT_KEYS:
            return {}
        try:
            quotes, _retrieved = self.odds_for(competition)
        except (CollectionError, ProviderBlockedError):
            return {}
        matching = _quotes_for(quotes, fixture)
        return {
            key: (quote.price, quote.quoted_at, quote.label)
            for key, quote in best_prices(matching, prefer=self._bookmaker).items()
        }


def _quotes_for(
    quotes: Sequence[QuotedPrice], fixture: Fixture
) -> tuple[QuotedPrice, ...]:
    """Prices for one fixture, matched on folded club names — or none at all.

    Grouped before being returned so an ambiguity is visible rather than
    resolved by list order: if the folded pairing and date match two different
    API events, neither is used.
    """
    wanted = (normalise(fixture.home), normalise(fixture.away), fixture.date)
    groups: dict[tuple[str, str], list[QuotedPrice]] = {}
    for quote in quotes:
        folded = (
            normalise(quote.fixture.home),
            normalise(quote.fixture.away),
            quote.fixture.date,
        )
        if folded != wanted:
            continue
        groups.setdefault((quote.fixture.home, quote.fixture.away), []).append(quote)
    if len(groups) != 1:
        return ()
    return tuple(next(iter(groups.values())))
