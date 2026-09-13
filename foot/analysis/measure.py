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
from dataclasses import dataclass

from foot.analysis.ledgerbook import Forecast, ForecastBook
from foot.analysis.naming import normalise
from foot.domain import Match, Outcome, OutcomeProbabilities, Score
from foot.evaluation.calibration import CalibrationReport, calibration_report
from foot.evaluation.metrics import ScoreCard
from foot.markets.catalogue import MarketOffer, standard_catalogue
from foot.markets.settlement import unit_return

__all__ = [
    "MIN_TO_CONCLUDE",
    "Measurement",
    "ResolvedForecast",
    "measure",
]

MIN_TO_CONCLUDE = 30
"""Resolved matches below which no conclusion is stated, only figures.

Not a magic number so much as an honest floor: with fewer, the confidence
interval on any rate of return spans both signs, and the reader deserves to be
told that rather than shown a percentage.
"""

_CATALOGUE: Mapping[str, MarketOffer] = {o.key: o for o in standard_catalogue()}


@dataclass(frozen=True, slots=True)
class ResolvedForecast:
    """One forecast, joined to what actually happened."""

    forecast: Forecast
    score: Score
    closing: float | None = None
    """Closing price of the market backed, when a source served one."""

    closing_book: str = ""

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
        """The market backed, when the journal recorded a settleable key."""
        return _CATALOGUE.get(self.forecast.market_key)

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
    scorecard: ScoreCard | None = None
    baseline: ScoreCard | None = None
    calibration: CalibrationReport | None = None
    revised: int = 0
    """Matches whose forecast was revised at least once before kick-off."""

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
            lines.append(
                "  Cotes de clôture : aucune source n'en a servi pour ces "
                "rencontres — l'écart au prix de clôture reste non mesuré."
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
    closing: Mapping[str, float] | None = None,
    as_of: dt.datetime | None = None,
) -> Measurement:
    """Join the journal to what happened, and score it.

    Args:
        results: played matches, from whatever source already loaded them.
        closing: ``{clé de rencontre: cote de clôture du marché retenu}``, when
            a source serves closing prices. Absent is reported as absent.
        as_of: forecasts for matches after this instant stay pending even if a
            result somehow exists; defaults to no cut.

    Only the **last** forecast per match is scored. Scoring every revision would
    count one match several times and reward whoever revises most often.
    """
    del as_of  # reserved: the journal is already cut by construction
    index = {
        _key(m.competition or "", m.home, m.away, m.date): m for m in results
    }
    prices = dict(closing or {})

    latest: dict[tuple[str, str, str], Forecast] = {}
    revisions: dict[tuple[str, str, str], int] = {}
    for forecast in book:
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
        key = _key(
            forecast.competition, forecast.home, forecast.away, match.date
        )
        resolved.append(
            ResolvedForecast(
                forecast=forecast,
                score=match.score,
                closing=prices.get(key),
                closing_book="" if key not in prices else "source de clôture",
            )
        )

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
        scorecard=scorecard,
        baseline=baseline,
        calibration=calibration,
        revised=sum(1 for count in revisions.values() if count),
    )


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
