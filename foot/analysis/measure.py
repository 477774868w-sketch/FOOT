"""Mesurer ce qui a été prévu — sans jamais toucher à la prévision.

Le journal conserve ; ce module **mesure**. La séparation n'est pas cosmétique :
tout ce qui écrit dans le journal le fait avant le match, tout ce qui se trouve
ici s'exécute après, et **rien ici n'écrit**. Une mesure qui pourrait corriger la
prévision qu'elle évalue ne mesurerait rien.

Ce qui est apparié :

* le **résultat réel** de la rencontre, depuis les sources déjà chargées ;
* la **cote de clôture**, quand une source en sert — à ne pas confondre avec le
  prix disponible au moment de décider : l'écart entre les deux est justement ce
  qu'on cherche à voir ;
* le **règlement exact** du marché retenu, par sa clé de catalogue, remboursements
  et demi-lignes compris. C'est pour cela que le journal enregistre la clé et pas
  seulement le libellé : « Victoire extérieur (2) » ne se règle pas, ``1X2:A`` si.

Ce qui est refusé :

* une rencontre non jouée, ou introuvable dans les résultats chargés, reste **en
  attente** et n'entre dans aucune moyenne ;
* en dessous de :data:`MIN_TO_CONCLUDE` rencontres résolues, les chiffres sont
  affichés mais **aucune conclusion n'est énoncée**. Un rendement calculé sur
  douze paris ne dit rien, et le présenter comme un résultat serait le défaut le
  plus coûteux que ce logiciel puisse commettre.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum

from foot.analysis.ledgerbook import Forecast, ForecastBook
from foot.analysis.naming import normalise
from foot.analysis.quotes import offer_for_key
from foot.domain import Match, Outcome, OutcomeProbabilities, Score
from foot.evaluation.calibration import CalibrationReport, calibration_report
from foot.evaluation.metrics import ScoreCard
from foot.markets.catalogue import MarketOffer
from foot.markets.settlement import unit_return

__all__ = [
    "MIN_TO_CONCLUDE",
    "ClosingPrices",
    "ClosingState",
    "Measurement",
    "ResolvedForecast",
    "closing_key",
    "measure",
]

MIN_TO_CONCLUDE = 30
"""Resolved matches below which no conclusion is stated, only figures.

Not a magic number so much as an honest floor: with fewer, the confidence
interval on any rate of return spans both signs, and the reader deserves to be
told that rather than shown a percentage.
"""

class ClosingState(Enum):
    """Why a closing price is missing — three answers, three different actions."""

    NOT_WIRED = "non raccordé"
    """No closing source was given to the measurement at all."""

    UNREACHABLE = "inaccessible"
    """A source was configured and could not be reached or read."""

    SERVED = "servi"
    """A source answered; individual fixtures may still be absent from it."""


@dataclass(frozen=True, slots=True)
class ClosingPrices:
    """Closing prices, with the reason when there are none.

    « No source plugged in », « the source refused the connection » and « the
    source answered but does not carry this match » are three different facts.
    Reporting all three as one blank line is how a missing integration hides
    behind a network excuse.
    """

    state: ClosingState = ClosingState.NOT_WIRED
    prices: Mapping[str, float] = field(default_factory=dict)
    source: str = ""
    detail: str = ""

    def get(self, key: str) -> float | None:
        return self.prices.get(key)

    def render(self) -> str:
        if self.state is ClosingState.NOT_WIRED:
            return (
                "non raccordé — aucune source de clôture n'a été fournie à la "
                "mesure (voir « --clotures-csv » ou un fournisseur de cotes)"
            )
        if self.state is ClosingState.UNREACHABLE:
            return f"inaccessible — {self.detail or 'source injoignable'}"
        return (
            f"servi par {self.source or 'une source'} : {len(self.prices)} "
            f"cote(s) de clôture disponibles"
        )


@dataclass(frozen=True, slots=True)
class ResolvedForecast:
    """One forecast, joined to what actually happened."""

    forecast: Forecast
    score: Score
    closing: float | None = None
    """Closing price of the **same market**, when a source served one."""

    closing_book: str = ""

    score_date: dt.date | None = None
    """Date of the match actually joined — the identity a duplicate collides on."""

    @property
    def outcome(self) -> Outcome:
        return self.score.outcome

    @property
    def probabilities(self) -> OutcomeProbabilities:
        home, draw, away = self.forecast.probabilities
        total = home + draw + away
        if total <= 0.0:
            raise ValueError(f"prévision sans masse : {self.forecast.key}")
        return OutcomeProbabilities(home / total, draw / total, away / total)

    @property
    def offer(self) -> MarketOffer | None:
        """The market backed, rebuilt by the **same resolver** the engine used.

        Looking the key up in the standard catalogue silently dropped every
        market the engine accepts but the catalogue does not list — an Asian
        ``-1.75``, a total at 4.5 — so a recommendation could be made, priced
        and staked, then vanish from the ledger of results. A market that can be
        recommended must be settleable.
        """
        key = self.forecast.market_key
        return offer_for_key(key) if key else None

    def profit(self) -> float | None:
        """Realised profit per unit staked, or ``None`` when nothing was backed.

        Settled through the catalogue's own unit result, so a refund is a
        refund and a quarter line half-settles — exactly as the pre-match
        expected value assumed.
        """
        offer, odds = self.offer, self.forecast.odds
        if offer is None or odds is None or odds <= 1.0:
            return None
        unit = offer.unit_result(self.score.home, self.score.away)
        return unit_return(unit, odds) - 1.0

    def closing_edge(self) -> float | None:
        """Taken price against closing price, in per cent — the CLV.

        Positive means the price taken was better than the market's last word.
        It measures the *timing*, not the forecast, and the two are reported
        apart because a good bet at a bad price is a different mistake from a
        bad bet at a good price.
        """
        odds, closing = self.forecast.odds, self.closing
        if odds is None or closing is None or closing <= 1.0:
            return None
        return odds / closing - 1.0


@dataclass(frozen=True, slots=True)
class Measurement:
    """What the journal's forecasts turned out to be worth."""

    resolved: tuple[ResolvedForecast, ...]
    pending: tuple[Forecast, ...]
    excluded: tuple[Forecast, ...] = ()
    """Lines left out, and why matters: written after the cut-off, after their
    own kick-off, or part of a retrospective replay. Counted and named rather
    than dropped, so a shrinking sample is visible."""

    scorecard: ScoreCard | None = None
    baseline: ScoreCard | None = None
    calibration: CalibrationReport | None = None
    revised: int = 0
    """Matches whose forecast was revised at least once before kick-off."""

    closing: ClosingPrices = field(default_factory=ClosingPrices)

    @property
    def conclusive(self) -> bool:
        return len(self.resolved) >= MIN_TO_CONCLUDE

    def backed(self) -> tuple[ResolvedForecast, ...]:
        return tuple(r for r in self.resolved if r.profit() is not None)

    def realised_return(self) -> float | None:
        """Mean profit per unit staked over every settled recommendation."""
        profits = [r.profit() for r in self.backed()]
        values = [p for p in profits if p is not None]
        if not values:
            return None
        return math.fsum(values) / len(values)

    def mean_closing_edge(self) -> float | None:
        edges = [r.closing_edge() for r in self.resolved]
        values = [e for e in edges if e is not None]
        if not values:
            return None
        return math.fsum(values) / len(values)

    def render(self) -> str:
        lines = [
            f"Mesure de {len(self.resolved)} rencontre(s) résolue(s), "
            f"{len(self.pending)} en attente"
        ]
        if self.excluded:
            lines.append(
                f"  {len(self.excluded)} ligne(s) écartée(s) : postérieure(s) à la "
                f"date du rapport, au coup d'envoi, ou issue(s) d'un rejeu "
                f"rétrospectif"
            )
        if self.revised:
            lines.append(
                f"  {self.revised} prévision(s) révisée(s) avant le coup d'envoi ; "
                f"c'est la dernière qui est mesurée"
            )
        if not self.resolved:
            lines.append(
                "  Aucune prévision du journal n'a encore de résultat connu. "
                "Rien n'est mesurable, et rien n'est affirmé."
            )
            return "\n".join(lines)

        if self.scorecard is not None:
            lines.append(f"  {self.scorecard}")
        if self.baseline is not None and self.scorecard is not None:
            lines.append(f"  {self.baseline}")
            if self.baseline.rps > 0.0:
                skill = self.scorecard.skill_against(self.baseline)
                lines.append(
                    f"  skill sur le RPS contre le taux de base de l'échantillon : "
                    f"{skill * 100:+.2f}%"
                )
            else:
                # Every match in the sample ended the same way, so the sample's
                # own frequencies are a perfect forecaster and the ratio is
                # undefined. Reporting that beats crashing, and beats printing a
                # skill score computed against nothing.
                lines.append(
                    "  skill non calculable : toutes les rencontres de "
                    "l'échantillon ont la même issue, le taux de base y est "
                    "parfait par construction"
                )
        if self.calibration is not None:
            lines.append("")
            lines.extend(f"  {line}" for line in str(self.calibration).splitlines())

        backed = self.backed()
        lines.append("")
        if not backed:
            lines.append(
                "  Aucun marché réglable n'a été retenu sur ces rencontres : "
                "aucun rendement n'est calculé."
            )
        else:
            realised = self.realised_return()
            wins = sum(1 for r in backed if (r.profit() or 0.0) > 0.0)
            assert realised is not None
            lines.append(
                f"  {len(backed)} recommandation(s) réglée(s) : {wins} gagnante(s), "
                f"rendement observé {realised * 100:+.2f}% par unité misée"
            )
        edge = self.mean_closing_edge()
        if edge is None:
            lines.append(f"  Cotes de clôture : {self.closing.render()}.")
            if self.closing.state is ClosingState.SERVED:
                lines.append(
                    "  Aucune des rencontres mesurées n'y figure avec le marché "
                    "retenu : l'écart au prix de clôture reste non mesuré."
                )
        else:
            lines.append(
                f"  Écart moyen au prix de clôture : {edge * 100:+.2f}% "
                f"(mesure du moment choisi, pas de la prévision)"
            )

        lines.append("")
        if self.conclusive:
            lines.append(
                f"  Échantillon de {len(self.resolved)} rencontres : les chiffres "
                f"ci-dessus sont interprétables, avec la prudence due à un "
                f"échantillon de cette taille."
            )
        else:
            lines.append(
                f"  ÉCHANTILLON INSUFFISANT POUR CONCLURE ({len(self.resolved)} < "
                f"{MIN_TO_CONCLUDE}). Les chiffres sont affichés parce qu'ils "
                f"existent, pas parce qu'ils démontrent quoi que ce soit — un "
                f"rendement calculé sur si peu de paris est du bruit."
            )
        lines.append(
            "  Aucune prévision n'a été modifiée par cette mesure : le journal "
            "est en ajout seul, et ce rapport n'y écrit rien."
        )
        return "\n".join(lines)


def measure(
    book: ForecastBook,
    *,
    results: Sequence[Match],
    closing: ClosingPrices | None = None,
    as_of: dt.datetime | None = None,
    include_retrospective: bool = False,
) -> Measurement:
    """Join the journal to what happened, and score it.

    Four instants are kept apart here, because conflating any two of them lets a
    measurement flatter itself:

    ``recorded_at``
        when the line was physically written;
    ``as_of``  (on the forecast)
        the instant the forecast claims to see from — a historical replay sets
        it in the past on purpose;
    ``kickoff_at``
        when the match started, and therefore the last moment a forecast can
        still be a forecast;
    ``as_of``  (this argument)
        the report's cut-off: nothing written after it may enter the score.

    Args:
        results: played matches, from whatever source already loaded them.
        closing: closing prices **and the reason when there are none** — not
            wired, unreachable, or served but silent on this fixture.
        as_of: report cut-off. A forecast recorded after it is excluded — asking
            for "the report as it stood on 14 September" must not be answered
            with a line written on the 15th.
        include_retrospective: whether replays — forecasts written after their
            own kick-off — join the score. Off by default: they are useful, and
            they are not forecasts anybody stood behind beforehand.

    Among the admissible lines, the **latest by ``as_of``** is scored, not the
    last one appended: a journal is a file, and file order is not chronology.
    """
    index = {
        _key(m.competition or "", m.home, m.away, m.date): m for m in results
    }
    prices = closing or ClosingPrices()

    latest: dict[tuple[str, str, str, str], Forecast] = {}
    revisions: dict[tuple[str, str, str, str], int] = {}
    excluded: list[Forecast] = []
    for forecast in book:
        if as_of is not None and forecast.recorded_at > as_of:
            excluded.append(forecast)
            continue
        if forecast.retrospective and not include_retrospective:
            excluded.append(forecast)
            continue
        kickoff = forecast.kickoff_instant
        if kickoff is not None and forecast.as_of > kickoff:
            excluded.append(forecast)  # made after kick-off: not a forecast
            continue
        held = latest.get(forecast.key)
        if held is None or forecast.as_of >= held.as_of:
            latest[forecast.key] = forecast
        if forecast.supersedes:
            revisions[forecast.key] = revisions.get(forecast.key, 0) + 1

    resolved: list[ResolvedForecast] = []
    pending: list[Forecast] = []
    for forecast in latest.values():
        match = _find(index, forecast)
        if match is None or match.score is None:
            pending.append(forecast)
            continue
        quoted = closing_key(
            forecast.competition,
            forecast.home,
            forecast.away,
            match.date,
            forecast.market_key,
        )
        resolved.append(
            ResolvedForecast(
                forecast=forecast,
                score=match.score,
                score_date=match.date,
                closing=prices.get(quoted),
                closing_book=prices.source if prices.get(quoted) else "",
            )
        )
    resolved, duplicates = _one_per_fixture(resolved)
    excluded.extend(duplicates)

    scorecard = baseline = None
    calibration = None
    if resolved:
        forecasts = [r.probabilities for r in resolved]
        outcomes = [r.outcome for r in resolved]
        scorecard = ScoreCard.evaluate(forecasts, outcomes, name="journal")
        baseline = ScoreCard.evaluate(
            [_base_rate(outcomes)] * len(outcomes), outcomes, name="taux de base"
        )
        calibration = calibration_report(forecasts, outcomes, bins=5)
    return Measurement(
        resolved=tuple(resolved),
        pending=tuple(pending),
        excluded=tuple(excluded),
        scorecard=scorecard,
        baseline=baseline,
        calibration=calibration,
        revised=sum(1 for count in revisions.values() if count),
        closing=prices,
    )


def _one_per_fixture(
    resolved: Sequence[ResolvedForecast],
) -> tuple[list[ResolvedForecast], list[Forecast]]:
    """Keep one forecast per **resolved match**, whatever route found it.

    A journal written before fixture dates existed still resolves, by its teams
    alone, when exactly one played match pairs them. That is compatibility, not
    guesswork — but it means an old line and a new dated line can land on the
    same match, and scoring both would count that match twice.

    The dated line wins, then the latest by ``as_of``. The loser is reported as
    excluded rather than dropped, so the count stays auditable.
    """
    best: dict[tuple[str, str, str, str], ResolvedForecast] = {}
    dropped: list[Forecast] = []
    for item in resolved:
        identity = (
            item.forecast.competition,
            normalise(item.forecast.home),
            normalise(item.forecast.away),
            item.score_date.isoformat() if item.score_date else "",
        )
        held = best.get(identity)
        if held is None:
            best[identity] = item
            continue
        winner, loser = _prefer(held, item)
        best[identity] = winner
        dropped.append(loser.forecast)
    return (list(best.values()), dropped)


def _prefer(
    held: ResolvedForecast, candidate: ResolvedForecast
) -> tuple[ResolvedForecast, ResolvedForecast]:
    """Between two forecasts of the same match, the better-identified one."""
    dated_held = bool(held.forecast.match_date)
    dated_new = bool(candidate.forecast.match_date)
    if dated_held != dated_new:
        return (held, candidate) if dated_held else (candidate, held)
    if candidate.forecast.as_of >= held.forecast.as_of:
        return (candidate, held)
    return (held, candidate)


def _base_rate(outcomes: Sequence[Outcome]) -> OutcomeProbabilities:
    """The sample's own frequencies — the baseline any model must beat.

    Drawn from the *same* matches, deliberately: a baseline fitted elsewhere
    would make the comparison depend on which season it came from.
    """
    n = len(outcomes)
    counts = {o: sum(1 for x in outcomes if x is o) / n for o in Outcome}
    return OutcomeProbabilities(
        counts[Outcome.HOME_WIN], counts[Outcome.DRAW], counts[Outcome.AWAY_WIN]
    )


def closing_key(
    competition: str, home: str, away: str, date: dt.date, market: str
) -> str:
    """Identity of one **priced market on one fixture**.

    A closing price belongs to a match *and* a market *and* a line — comparing
    the price taken on ``OU:2.5:over`` with the closing price of ``1X2:H`` would
    produce a number that means nothing at all.
    """
    return f"{_key(competition, home, away, date)}#{market.strip().upper()}"


def _key(competition: str, home: str, away: str, date: dt.date) -> str:
    return f"{competition}|{normalise(home)}|{normalise(away)}|{date.isoformat()}"


def _find(index: Mapping[str, Match], forecast: Forecast) -> Match | None:
    """Locate the played match a forecast refers to, without guessing.

    The recorded fixture date is used when the journal has one. A forecast
    written before that field existed falls back to the teams alone, and only
    when exactly one played match pairs them — never when several do, because
    picking one would silently score the wrong game.
    """
    if forecast.match_date:
        try:
            date = dt.date.fromisoformat(forecast.match_date)
        except ValueError:
            date = None
        if date is not None:
            return index.get(
                _key(forecast.competition, forecast.home, forecast.away, date)
            )
    pair = (normalise(forecast.home), normalise(forecast.away))
    hits = [
        match
        for key, match in index.items()
        if key.split("|")[1:3] == list(pair)
        and key.startswith(f"{forecast.competition}|")
    ]
    return hits[0] if len(hits) == 1 else None
