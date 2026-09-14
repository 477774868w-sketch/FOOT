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
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

from foot.analysis.measure import ClosingPrices, ClosingState, closing_key
from foot.analysis.quotes import offer_for_key
from foot.collect.base import ClosingSource, CollectionError
from foot.collect.footballdata import DIVISION_FOR_KEY
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
    candidates: dict[str, set[float]] = {}
    refused = 0
    try:
        with file.open(newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                entry = _row_to_price(
                    row, competition=competition, bookmaker=bookmaker
                )
                if entry is None:
                    refused += 1
                    continue
                key, reference, price = entry
                prices[key] = price
                candidates.setdefault(reference, set()).add(price)
    except (OSError, csv.Error) as error:
        return ClosingPrices(
            state=ClosingState.UNREACHABLE, source=str(file), detail=str(error)
        )
    prices.update(_unambiguous(candidates))
    detail = f"{refused} ligne(s) illisible(s)" if refused else ""
    return ClosingPrices(
        state=ClosingState.SERVED,
        prices=prices,
        source=f"{file}{f' · {bookmaker}' if bookmaker else ''}",
        detail=detail,
    )


def _row_to_price(
    row: dict[str, str], *, competition: str, bookmaker: str = ""
) -> tuple[str, str, float] | None:
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
    where = (row.get("competition") or competition).strip()
    book = (row.get("bookmaker") or bookmaker).strip()
    return (
        closing_key(where, home, away, date, offer.key, book),
        closing_key(where, home, away, date, offer.key),
        price,
    )


def load_closing_from_sources(
    sources: Iterable[ClosingSource],
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
    candidates: dict[str, set[float]] = {}
    names: list[str] = []
    failures: list[str] = []
    wanted = [c for c in competitions if c] or [""]
    for source in listed:
        served = False
        for competition, season in _requests(source, wanted, seasons):
            try:
                # Actually asked for. Calling odds() bare let the archive answer
                # with its own defaults — E0 / 2024-25 — so a request for Serie A
                # 2026-27 came back with English prices from two seasons earlier.
                book, _evidence = source.odds(competition, season)
            except (CollectionError, OSError, KeyError, ValueError) as error:
                failures.append(f"{source.name} {competition} {season} : {error}")
                continue
            served = True
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
                            getattr(quote, "bookmaker", "") or "",
                        )
                    ] = price
                    reference = closing_key(
                        fixture.competition or "",
                        fixture.home,
                        fixture.away,
                        fixture.date,
                        market,
                    )
                    candidates.setdefault(reference, set()).add(price)
        if served:
            names.append(source.name)
    prices.update(_unambiguous(candidates))
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


def _requests(
    source: ClosingSource, competitions: Sequence[str], seasons: Sequence[str]
) -> list[tuple[str, str]]:
    """Which (competition, season) pairs to ask this source for.

    Our keys are translated into the source's own division codes where a mapping
    exists; a competition the source does not serve is not requested at all,
    rather than silently answered with its default division.
    """
    served = set(source.competitions()) if hasattr(source, "competitions") else set()
    pairs: list[tuple[str, str]] = []
    for competition in competitions:
        code = DIVISION_FOR_KEY.get(competition, competition)
        if served and code not in served:
            continue
        for season in seasons or ("",):
            pairs.append((code, season))
    return pairs


def _unambiguous(candidates: Mapping[str, set[float]]) -> dict[str, float]:
    """Market-reference prices, kept only where the books agree.

    A forecast that recorded no bookmaker can still be compared to "the market's
    close" — but only when there is one. Two books closing the same market at
    1.90 and 2.80 do not average into a reference; keeping either would silently
    pick a winner, so the entry is dropped and the comparison reported as
    unavailable.
    """
    return {
        key: next(iter(values))
        for key, values in candidates.items()
        if len(values) == 1
    }
