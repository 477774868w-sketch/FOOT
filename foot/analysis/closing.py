"""Charger des cotes de clôture — et dire précisément quand on n'en a pas.

Trois situations que la commande confondait en une seule phrase :

``non raccordé``
    aucune source n'a été fournie. Ce n'est pas un problème de réseau, c'est une
    intégration absente, et l'annoncer comme une panne serait commode ;
``inaccessible``
    une source **a** été fournie et n'a pas répondu, ou a répondu illisiblement ;
``servi``
    la source a répondu ; une rencontre peut encore en être absente, ce qui est
    encore une autre chose.

Deux routes, la même sortie :

* un **import explicite** (``--clotures-csv``), qui marche partout, y compris
  derrière un proxy qui bloque tout ;
* un **fournisseur de cotes** du registre, quand il en existe un de joignable.

Dans les deux cas, une cote de clôture est appariée par **rencontre, marché et
bookmaker** : comparer le prix pris sur « plus de 2,5 buts » à la clôture du
« 1 » produirait un nombre qui ne veut rien dire.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable, Sequence
from pathlib import Path

from foot.analysis.measure import ClosingPrices, ClosingState, closing_key
from foot.analysis.quotes import offer_for_key
from foot.collect.base import CollectionError, OddsSource
from foot.data.csv_source import parse_date
from foot.domain import Outcome

__all__ = ["load_closing_csv", "load_closing_from_sources"]

_MARKET_BY_OUTCOME = {
    Outcome.HOME_WIN: "1X2:H",
    Outcome.DRAW: "1X2:D",
    Outcome.AWAY_WIN: "1X2:A",
}


def load_closing_csv(
    path: str | Path, *, competition: str = "", bookmaker: str = ""
) -> ClosingPrices:
    """Read ``date,home,away,marche,cote[,bookmaker,competition]``.

    The explicit route. It exists because a measurement must not depend on an
    outbound connection that an organisation's proxy may simply refuse: an
    operator who can read a price can paste it.
    """
    file = Path(path)
    if not file.exists():
        return ClosingPrices(
            state=ClosingState.UNREACHABLE,
            source=str(file),
            detail=f"fichier de clôtures introuvable : {file}",
        )
    prices: dict[str, float] = {}
    refused = 0
    try:
        with file.open(newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                entry = _row_to_price(row, competition=competition)
                if entry is None:
                    refused += 1
                    continue
                key, price = entry
                prices[key] = price
    except (OSError, csv.Error) as error:
        return ClosingPrices(
            state=ClosingState.UNREACHABLE, source=str(file), detail=str(error)
        )
    detail = f"{refused} ligne(s) illisible(s)" if refused else ""
    return ClosingPrices(
        state=ClosingState.SERVED,
        prices=prices,
        source=f"{file}{f' · {bookmaker}' if bookmaker else ''}",
        detail=detail,
    )


def _row_to_price(
    row: dict[str, str], *, competition: str
) -> tuple[str, float] | None:
    """One CSV row into a keyed price, or nothing when it cannot be trusted.

    A market whose key the engine cannot rebuild is refused rather than stored:
    an unsettleable closing price can only produce a meaningless comparison.
    """
    try:
        date = parse_date(row["date"].strip())
        home = row["home"].strip()
        away = row["away"].strip()
        market = row["marche"].strip()
        price = float(row["cote"].strip().replace(",", "."))
    except (KeyError, ValueError, AttributeError):
        return None
    if not home or not away or price <= 1.0:
        return None
    offer = offer_for_key(market)
    if offer is None:
        return None
    key = closing_key(
        (row.get("competition") or competition).strip(), home, away, date, offer.key
    )
    return (key, price)


def load_closing_from_sources(
    sources: Iterable[OddsSource],
    *,
    competitions: Sequence[str],
    seasons: Sequence[str],
) -> ClosingPrices:
    """Ask every reachable odds source for its archived 1–N–2 closing prices.

    Only 1–N–2, because that is what the archives actually carry. Claiming a
    closing price for a market the source never published would be worse than
    reporting none.
    """
    listed = list(sources)
    if not listed:
        return ClosingPrices(state=ClosingState.NOT_WIRED)
    prices: dict[str, float] = {}
    names: list[str] = []
    failures: list[str] = []
    for source in listed:
        try:
            book, _evidence = source.odds()
        except (CollectionError, OSError) as error:
            failures.append(f"{source.name} : {error}")
            continue
        names.append(source.name)
        for fixture, quote in book.items():
            for outcome, market in _MARKET_BY_OUTCOME.items():
                price = _price_for(quote, outcome)
                if price is None:
                    continue
                prices[
                    closing_key(
                        fixture.competition or "",
                        fixture.home,
                        fixture.away,
                        fixture.date,
                        market,
                    )
                ] = price
    del competitions, seasons  # sources serve whole archives; no filtering needed
    if not names:
        return ClosingPrices(
            state=ClosingState.UNREACHABLE,
            detail=" ; ".join(failures) or "aucune source n'a répondu",
        )
    return ClosingPrices(
        state=ClosingState.SERVED,
        prices=prices,
        source=", ".join(names),
        detail=" ; ".join(failures),
    )


def _price_for(quote: object, outcome: Outcome) -> float | None:
    value = getattr(
        quote,
        {
            Outcome.HOME_WIN: "home",
            Outcome.DRAW: "draw",
            Outcome.AWAY_WIN: "away",
        }[outcome],
        None,
    )
    if isinstance(value, (int, float)) and value > 1.0:
        return float(value)
    return None
