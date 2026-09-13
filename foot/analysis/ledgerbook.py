"""Le journal : ce qui a été prévu, quand, sur quoi — et qui ne se réécrit pas.

Mesurer un système de prévision exige une chose avant toutes les autres : que la
prévision soit **écrite avant** le match et ne bouge plus ensuite.  Un journal
qu'on peut corriger après coup ne mesure rien, il raconte.

Ce module écrit donc en **append seul**, une ligne JSON par enregistrement, et
ne propose aucune fonction de modification ni de suppression.  Une décision
révisée — après une composition officielle, par exemple — s'ajoute avec un
renvoi vers celle qu'elle remplace ; les deux restent lisibles, et l'ordre est
celui du temps.

Chaque ligne porte de quoi refaire le calcul plus tard :

* l'instant d'analyse et l'empreinte du dossier scellé ;
* les probabilités du modèle, sa version et ses réglages ;
* le marché retenu, sa cote, **son bookmaker et son heure de relevé** ;
* les sources utilisées, avec leur horodatage de récupération.

Rien n'y est ajouté après le match : le résultat se joint au moment de la
mesure, par appariement, sans toucher à la prévision.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from foot.analysis.engine import MatchAnalysis
from foot.provenance import utcnow

__all__ = ["Forecast", "ForecastBook", "record_run"]

DEFAULT_BOOK = Path(".foot-journal.jsonl")
"""Where forecasts accumulate. One JSON object per line, append only."""


def _triple(value: object) -> tuple[float, float, float]:
    """Read three probabilities from a journal line, whatever it actually holds.

    A journal is read long after it was written, sometimes by a later version of
    this code.  A truncated or oversized line is a defect worth seeing, not a
    crash in the middle of a measurement run.
    """
    values = _floats(value, 3)
    return (values[0], values[1], values[2])


def _pair(value: object) -> tuple[float, float]:
    values = _floats(value, 2)
    return (values[0], values[1])


def _floats(value: object, size: int) -> list[float]:
    items = list(value) if isinstance(value, (list, tuple)) else []
    out = [float(item) for item in items[:size] if isinstance(item, (int, float))]
    out.extend(0.0 for _ in range(size - len(out)))
    return out


@dataclass(frozen=True, slots=True)
class Forecast:
    """One dated forecast, exactly as it stood before the match."""

    recorded_at: dt.datetime
    as_of: dt.datetime
    competition: str
    home: str
    away: str
    kickoff: str
    fingerprint: str
    """Digest of the sealed dossier — the anchor a later measurement joins on."""

    probabilities: tuple[float, float, float]
    expected_goals: tuple[float, float]
    model_version: str
    parameters: Mapping[str, object] = field(default_factory=dict)
    decision: str = ""
    market: str = ""
    odds: float | None = None
    bookmaker: str = ""
    quoted_at: str = ""
    confidence: str = ""
    sources: tuple[str, ...] = ()
    supersedes: str = ""
    """Fingerprint of the forecast this one revises, when it revises one."""

    reason: str = ""

    def to_json(self) -> str:
        return json.dumps(
            {
                "recorded_at": self.recorded_at.isoformat(),
                "as_of": self.as_of.isoformat(),
                "competition": self.competition,
                "home": self.home,
                "away": self.away,
                "kickoff": self.kickoff,
                "fingerprint": self.fingerprint,
                "probabilities": list(self.probabilities),
                "expected_goals": list(self.expected_goals),
                "model_version": self.model_version,
                "parameters": dict(self.parameters),
                "decision": self.decision,
                "market": self.market,
                "odds": self.odds,
                "bookmaker": self.bookmaker,
                "quoted_at": self.quoted_at,
                "confidence": self.confidence,
                "sources": list(self.sources),
                "supersedes": self.supersedes,
                "reason": self.reason,
            },
            ensure_ascii=False,
            sort_keys=True,
        )

    @staticmethod
    def from_json(line: str) -> Forecast:
        raw = json.loads(line)
        return Forecast(
            recorded_at=dt.datetime.fromisoformat(raw["recorded_at"]),
            as_of=dt.datetime.fromisoformat(raw["as_of"]),
            competition=raw.get("competition", ""),
            home=raw["home"],
            away=raw["away"],
            kickoff=raw.get("kickoff", ""),
            fingerprint=raw.get("fingerprint", ""),
            probabilities=_triple(raw.get("probabilities")),
            expected_goals=_pair(raw.get("expected_goals")),
            model_version=raw.get("model_version", ""),
            parameters=raw.get("parameters", {}),
            decision=raw.get("decision", ""),
            market=raw.get("market", ""),
            odds=raw.get("odds"),
            bookmaker=raw.get("bookmaker", ""),
            quoted_at=raw.get("quoted_at", ""),
            confidence=raw.get("confidence", ""),
            sources=tuple(raw.get("sources", ())),
            supersedes=raw.get("supersedes", ""),
            reason=raw.get("reason", ""),
        )

    @property
    def key(self) -> tuple[str, str, str]:
        """What identifies the *match*, across revisions of its forecast."""
        return (self.competition, self.home, self.away)


class ForecastBook:
    """An append-only journal of forecasts.

    There is deliberately no ``update`` and no ``delete``: a forecast that can be
    edited after the fact cannot measure anything. A revision is a new line that
    names the one it supersedes.
    """

    __slots__ = ("_path",)

    def __init__(self, path: str | Path = DEFAULT_BOOK) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def append(self, forecast: Forecast) -> Forecast:
        """Write one forecast. Never rewrites, never reorders."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(forecast.to_json() + "\n")
        return forecast

    def __iter__(self) -> Iterator[Forecast]:
        if not self._path.exists():
            return
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                yield Forecast.from_json(line)

    def __len__(self) -> int:
        return sum(1 for _ in self)

    def latest_for(self, key: tuple[str, str, str]) -> Forecast | None:
        """The most recent forecast for one match — revisions included."""
        found: Forecast | None = None
        for forecast in self:
            if forecast.key == key:
                found = forecast
        return found

    def history_for(self, key: tuple[str, str, str]) -> tuple[Forecast, ...]:
        """Every version, oldest first — what makes a revision auditable."""
        return tuple(f for f in self if f.key == key)

    def render(self, limit: int = 20) -> str:
        entries = list(self)[-limit:]
        if not entries:
            return f"Journal vide ({self._path})."
        lines = [f"Journal des prévisions — {self._path} ({len(list(self))} ligne(s))"]
        for forecast in entries:
            odds = f"{forecast.odds:.2f}" if forecast.odds else "—"
            mark = " (révision)" if forecast.supersedes else ""
            lines.append(
                f"  {forecast.as_of.strftime('%d/%m %H:%M')} "
                f"{forecast.home} – {forecast.away} · {forecast.decision} · "
                f"{forecast.market or '—'} @ {odds} "
                f"[{forecast.bookmaker or 'sans bookmaker'}]{mark}"
            )
        return "\n".join(lines)


def record_run(
    analyses: Sequence[MatchAnalysis],
    *,
    book: ForecastBook,
    as_of: dt.datetime,
    reason: str = "",
) -> tuple[Forecast, ...]:
    """Journal every analysed match of one run, before its kick-off.

    A match that could not be analysed is **not** journalled: there is no
    forecast to measure, and writing an empty one would pollute the calibration
    later.
    """
    written: list[Forecast] = []
    for analysis in analyses:
        if analysis.sealed is None or analysis.resolved.fixture is None:
            continue
        dossier = analysis.sealed.dossier
        fixture = analysis.resolved.fixture
        decision = analysis.decision
        main = decision.main if decision else None
        previous = book.latest_for(
            (fixture.competition or "", fixture.home, fixture.away)
        )
        probabilities = dossier.probabilities
        forecast = Forecast(
            recorded_at=utcnow(),
            as_of=as_of,
            competition=fixture.competition or "",
            home=fixture.home,
            away=fixture.away,
            kickoff=analysis.resolved.kickoff_local(),
            fingerprint=analysis.sealed.data_fingerprint,
            probabilities=(
                probabilities.home,
                probabilities.draw,
                probabilities.away,
            ),
            expected_goals=dossier.expected_goals,
            model_version=dossier.model_version,
            parameters={
                key: value
                for key, value in dossier.parameters.items()
                if isinstance(value, (int, float, str, bool))
            },
            decision=decision.status.value if decision else "",
            market=main.offer.label if main else "",
            odds=main.offer.odds if main else None,
            bookmaker=(main.offer.bookmaker or "") if main else "",
            quoted_at=(
                main.priced.quoted_at.isoformat()
                if main and main.priced.quoted_at
                else ""
            ),
            confidence=decision.confidence.value if decision else "",
            sources=tuple(
                sorted({item.source.name for item in dossier.evidence})
            )[:8],
            supersedes=(
                previous.fingerprint
                if previous and previous.fingerprint != analysis.sealed.data_fingerprint
                else ""
            ),
            reason=reason,
        )
        written.append(book.append(forecast))
    return tuple(written)
