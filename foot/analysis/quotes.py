"""Reading quoted prices for markets other than 1–N–2.

The catalogue prices thirty-six selections, but a comparison is only meaningful
between markets that carry a **real price**.  Sending the selector three 1X2
odds and calling the result "the best market" overstates what was weighed, so
this module lets the operator quote any of them, in a notation short enough to
type on the same line as the fixture.

Notation — ``FAMILLE[:précision]=cote`` — with the French shorthand people
actually use::

    1=2.10  N=3.40  2=3.60            1–N–2
    BTTS:oui=1.85   BTTS:non=1.95     les deux marquent
    TOTAL:+2.5=1.95 TOTAL:-2.5=1.90   plus / moins de 2,5 buts
    DC:1N=1.30      DC:12=1.25        double chance
    DNB:1=1.62                        remboursé si nul
    AH:H:-0.5=2.05  AH:A:+1=1.90      handicap asiatique
    TE:H:+1.5=2.20                    total d'équipe

Every token resolves to a catalogue key, so the line, the settlement rule, the
bookmaker and the quotation instant all travel with the price.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from foot.domain import Outcome
from foot.markets.catalogue import (
    MarketOffer,
    asian_handicap,
    both_teams_to_score,
    double_chance,
    draw_no_bet,
    match_result,
    refuse_if_unsupported,
    team_total,
    total_goals,
)

__all__ = ["QUOTE_SYNTAX", "offer_for_key", "parse_quote_token", "parse_quotes"]

QUOTE_SYNTAX = (
    "1=2.10 · N=3.40 · 2=3.60 · BTTS:oui=1.85 · TOTAL:+2.5=1.95 · TOTAL:-2.5=1.90 · "
    "DC:1N=1.30 · DNB:1=1.62 · AH:H:-0.5=2.05 · TE:H:+1.5=2.20"
)

_TOKEN = re.compile(r"^\s*([A-Za-z0-9+\-.:]+)\s*=\s*([0-9]+(?:[.,][0-9]+)?)\s*$")
# Quotes are separated by a pipe, a semicolon *or* plain whitespace: the guide
# shows both, and a user who groups several prices in one field must not see
# them silently glued to a team name.
_TOKENS = re.compile(r"[|;\s]+")
_SIGNED = re.compile(r"^([+-]?)([0-9]+(?:[.,][0-9]+)?)$")

_OUTCOMES: Mapping[str, Outcome] = {
    "1": Outcome.HOME_WIN, "H": Outcome.HOME_WIN, "DOM": Outcome.HOME_WIN,
    "N": Outcome.DRAW, "X": Outcome.DRAW, "D": Outcome.DRAW, "NUL": Outcome.DRAW,
    "2": Outcome.AWAY_WIN, "A": Outcome.AWAY_WIN, "EXT": Outcome.AWAY_WIN,
}
_DOUBLE = {
    "1N": (Outcome.HOME_WIN, Outcome.DRAW), "1X": (Outcome.HOME_WIN, Outcome.DRAW),
    "12": (Outcome.HOME_WIN, Outcome.AWAY_WIN),
    "N2": (Outcome.DRAW, Outcome.AWAY_WIN), "X2": (Outcome.DRAW, Outcome.AWAY_WIN),
}
_YES = {"OUI", "YES", "O", "Y"}
_NO = {"NON", "NO", "N"}


def _number(text: str) -> float | None:
    match = _SIGNED.match(text.strip())
    if not match:
        return None
    value = float(match.group(2).replace(",", "."))
    return -value if match.group(1) == "-" else value


def offer_for_key(key: str) -> MarketOffer | None:  # noqa: PLR0911
    """Build the offer a quotation token names, or ``None`` if unrecognised.

    Constructing on demand means a line the standard catalogue does not carry —
    an Asian ``-0.75``, a total at ``0.5`` — is still priceable, instead of the
    quote being silently dropped.
    """
    refuse_if_unsupported(key)
    parts = [p for p in key.strip().upper().split(":") if p]
    if not parts:
        return None
    head, rest = parts[0], parts[1:]

    if head in _OUTCOMES and not rest:
        return match_result(_OUTCOMES[head])
    if head in ("1X2", "RES") and rest and rest[0] in _OUTCOMES:
        return match_result(_OUTCOMES[rest[0]])
    if head == "DC" and rest and rest[0] in _DOUBLE:
        return double_chance(*_DOUBLE[rest[0]])
    if head in ("DNB", "RSN") and rest and rest[0] in _OUTCOMES:
        side = _OUTCOMES[rest[0]]
        return None if side is Outcome.DRAW else draw_no_bet(side)
    if head == "BTTS" and rest:
        if rest[0] in _YES:
            return both_teams_to_score(yes=True)
        if rest[0] in _NO:
            return both_teams_to_score(yes=False)
        return None
    if head in ("TOTAL", "OU", "TOT") and rest:
        line = _number(rest[0])
        if line is None:
            return None
        # A sign selects the side: "+2.5" is over, "-2.5" is under.
        over = not rest[0].strip().startswith("-")
        return total_goals(abs(line), over=over)
    if head == "AH" and len(rest) >= 2:
        line = _number(rest[1])
        if line is None or rest[0] not in _OUTCOMES:
            return None
        return asian_handicap(line, home=_OUTCOMES[rest[0]] is Outcome.HOME_WIN)
    if head in ("TE", "TT") and len(rest) >= 2:
        line = _number(rest[1])
        if line is None or rest[0] not in _OUTCOMES:
            return None
        over = not rest[1].strip().startswith("-")
        return team_total(
            abs(line), home=_OUTCOMES[rest[0]] is Outcome.HOME_WIN, over=over
        )
    return None


def parse_quote_token(text: str) -> tuple[MarketOffer, float] | None:
    """Parse one ``FAMILLE[:précision]=cote`` token."""
    match = _TOKEN.match(text)
    if not match:
        return None
    try:
        odds = float(match.group(2).replace(",", "."))
    except ValueError:
        return None
    if not 1.0 < odds < 10000.0:
        return None
    offer = offer_for_key(match.group(1))
    return None if offer is None else (offer, odds)


def parse_quotes(text: str) -> dict[str, float]:
    """Extract every quotation token from a free-text fragment.

    Returns catalogue keys mapped to decimal odds, so the caller can price them
    against the very same grid the rest of the analysis uses.
    """
    found: dict[str, float] = {}
    for chunk in _TOKENS.split(text):
        parsed = parse_quote_token(chunk)
        if parsed is not None:
            offer, odds = parsed
            found[offer.key] = odds
    return found
