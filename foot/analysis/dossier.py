"""The sport dossier: built blind to the market, then sealed.

The operator's requirement is that the sporting view be formed *before* any
price is seen, and that this be verifiable rather than promised.  Two mechanisms
enforce it here, and they are different in kind:

**A whitelist, not a filter.**  :class:`SportInput` is the only channel into the
sport phase, and it carries exactly four things: the fixture, a match history,
evidence, and ``as_of``.  A :class:`~foot.domain.Match` has no price field at
all, so results cannot smuggle odds in structurally.  Evidence is admitted
through :func:`without_market_evidence`, which drops market keys rather than
trusting callers to omit them.  Blacklisting what must not pass would leave the
next odds column to slip through; whitelisting what may pass does not.

**A seal, as a trace.**  :meth:`SportDossier.seal` fingerprints the inputs,
parameters, probabilities and assumptions.  The digest proves afterwards what
the dossier contained; it is explicitly *not* the barrier — a digest cannot stop
a leak, it can only expose one — and the two are kept distinct in the code and
in the report.

A price move never rewrites a sealed dossier.  New *sporting* information — an
official lineup above all — produces a **new** dossier with its own seal, so the
history of what was believed, and when, stays intact.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum

from foot.domain import Fixture, MatchLog, OutcomeProbabilities
from foot.models.base import ScoreMatrix
from foot.provenance import Evidence, fingerprint, utcnow

__all__ = [
    "MARKET_EVIDENCE_PREFIXES",
    "Finding",
    "FindingKind",
    "OddsLeakError",
    "SealedDossier",
    "SportDossier",
    "SportInput",
    "without_market_evidence",
]

DEFAULT_RESULT_DELAY = dt.timedelta(days=1)
"""Delay after a match date before its result is treated as known.

Openfootball and most result archives publish a date without a kickoff time, so
the exact moment a score became public is unknown.  Treating a result as known
from the *start* of its own match day is a look-ahead: at midnight nobody knows
the evening's score.  One day is the conservative reading of "date only" — the
result is available once the day is over — and it is what
:func:`result_available_at` applies.  A source that does carry kickoff times can
narrow it by passing a smaller delay.
"""

MARKET_EVIDENCE_PREFIXES: tuple[str, ...] = ("cote::", "odds::", "marché::", "market::")
"""Evidence key prefixes that carry price information and never reach the sport phase."""

_MARKET_KEY_TOKENS = frozenset(
    {
        "cote", "cotes", "odds", "price", "prices", "booksum", "overround",
        "vig", "juice", "bookmaker", "closing", "handicap_price", "implied_odds",
        "b365h", "b365d", "b365a", "psh", "psd", "psa", "whh", "whd", "wha",
        "maxh", "maxd", "maxa", "avgh", "avgd", "avga",
    }
)


def result_available_at(
    match_date: dt.date,
    *,
    tzinfo: dt.tzinfo,
    delay: dt.timedelta = DEFAULT_RESULT_DELAY,
) -> dt.datetime:
    """The instant a result dated ``match_date`` can first be known.

    The analysis cuts on *availability*, never on the match date itself.
    """
    return dt.datetime.combine(match_date, dt.time(0, 0), tzinfo=tzinfo) + delay


class OddsLeakError(RuntimeError):
    """Market information reached a stage that must be blind to it."""


def without_market_evidence(entries: Iterable[Evidence]) -> tuple[Evidence, ...]:
    """Admit only evidence that carries no price information.

    Applied on the way *into* the sport phase, so the caller cannot forget.
    """
    kept: list[Evidence] = []
    for entry in entries:
        key = entry.key.lower()
        if key.startswith(MARKET_EVIDENCE_PREFIXES):
            continue
        if any(token in key.split("::")[0].split() for token in _MARKET_KEY_TOKENS):
            continue
        kept.append(entry)
    return tuple(kept)


def assert_odds_free(payload: object, *, where: str) -> None:
    """Raise :class:`OddsLeakError` if a structure carries anything price-shaped.

    A defence in depth behind the whitelist: it is what turns "we believe the
    sport phase is blind" into a statement a test can falsify.
    """
    def walk(node: object, path: str) -> None:
        if isinstance(node, Mapping):
            for key, value in node.items():
                text = str(key).lower()
                if any(token in text for token in _MARKET_KEY_TOKENS):
                    raise OddsLeakError(
                        f"{where} : la clé {key!r} (chemin {path}) porte une information "
                        f"de marché ; la phase sportive doit en être aveugle"
                    )
                walk(value, f"{path}.{key}")
        elif isinstance(node, (list, tuple)):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")

    walk(payload, where)


class FindingKind(Enum):
    """Keeps facts, model output and interpretation from blurring together."""

    FACT = "fait"
    """Observed and sourced."""

    MODEL = "modèle"
    """Produced by an estimator, with the variable named."""

    INTERPRETATION = "lecture"
    """Tactical reading; argued, not measured."""

    SCENARIO = "scénario"
    """A hypothesis used for sensitivity, explicitly not an estimate."""


@dataclass(frozen=True, slots=True)
class Finding:
    """One traceable step from a source to the forecast.

    Together the findings answer the operator's chain in full:
    source → donnée normalisée → variable → effet sur la prévision.
    """

    rubric: int
    kind: FindingKind
    statement: str
    evidence_keys: tuple[str, ...] = ()
    model_variable: str | None = None
    effect: str | None = None

    @property
    def used_in_model(self) -> bool:
        """False for a finding that is reported but never reaches the forecast."""
        return self.model_variable is not None

    def render(self) -> str:
        mark = "→ modèle" if self.used_in_model else "affiché seulement"
        line = f"[{self.kind.value} · R{self.rubric:02d} · {mark}] {self.statement}"
        if self.model_variable:
            line += f"\n      variable : {self.model_variable}"
            if self.effect:
                line += f" — effet : {self.effect}"
        if self.evidence_keys:
            line += f"\n      preuves : {', '.join(self.evidence_keys)}"
        return line


@dataclass(frozen=True, slots=True)
class SportInput:
    """The *only* channel into the sport phase.

    Constructing one filters market evidence out; there is no other constructor
    and no setter, so a caller cannot widen what the sport phase can see.
    """

    fixture: Fixture
    history: MatchLog
    as_of: dt.datetime
    evidence: tuple[Evidence, ...] = ()
    competition: str = ""
    notes: tuple[str, ...] = ()
    knowledge_cutoff: dt.datetime | None = None
    """Latest instant whose information the dossier is allowed to contain."""

    cutoff_rule: str = ""
    """How that cutoff was derived, carried into the report."""

    def __post_init__(self) -> None:
        if self.as_of.tzinfo is None:
            raise ValueError("as_of doit être horodaté avec un fuseau")
        object.__setattr__(self, "evidence", without_market_evidence(self.evidence))
        if self.knowledge_cutoff is None:
            object.__setattr__(self, "knowledge_cutoff", self.as_of)
        if not self.cutoff_rule:
            object.__setattr__(
                self,
                "cutoff_rule",
                "coupure sur la date de disponibilité de l'information "
                f"(date du match + {DEFAULT_RESULT_DELAY.days} j), non sur la date du match",
            )
        cutoff = self.knowledge_cutoff
        assert cutoff is not None
        # The bar is availability, not the match date: a score dated today is
        # not knowable at midnight today.
        late = [
            m
            for m in self.history
            if result_available_at(m.date, tzinfo=cutoff.tzinfo or dt.timezone.utc) > cutoff
        ]
        if late:
            raise ValueError(
                f"fuite temporelle : {len(late)} résultats non disponibles à "
                f"{cutoff.isoformat()}, le premier étant {late[0]}"
            )

    @property
    def history_span(self) -> str:
        if not self.history:
            return "aucun historique"
        return f"{self.history.start.isoformat()} → {self.history.end.isoformat()}"


@dataclass(frozen=True, slots=True)
class SportDossier:
    """The sporting view of one match, formed without any price."""

    fixture: Fixture
    as_of: dt.datetime
    probabilities: OutcomeProbabilities
    score_matrix: ScoreMatrix
    expected_goals: tuple[float, float]
    model_version: str
    parameters: Mapping[str, object]
    findings: tuple[Finding, ...] = ()
    assumptions: tuple[str, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    history_span: str = ""
    history_size: int = 0
    counter_analysis: tuple[str, ...] = ()

    def model_findings(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.used_in_model)

    def display_only_findings(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if not f.used_in_model)

    def seal_payload(self) -> dict[str, object]:
        """Exactly what the fingerprint covers."""
        return {
            "fixture": {
                "home": self.fixture.home,
                "away": self.fixture.away,
                "date": self.fixture.date.isoformat(),
                "neutral": self.fixture.neutral,
                "competition": self.fixture.competition,
            },
            "as_of": self.as_of.isoformat(),
            "model_version": self.model_version,
            "parameters": dict(self.parameters),
            "probabilities": list(self.probabilities.as_tuple()),
            "expected_goals": list(self.expected_goals),
            "history_span": self.history_span,
            "history_size": self.history_size,
            "assumptions": list(self.assumptions),
            "findings": [
                {
                    "rubric": f.rubric,
                    "kind": f.kind.value,
                    "statement": f.statement,
                    "variable": f.model_variable,
                }
                for f in self.findings
            ],
        }

    def seal(self, *, sealed_at: dt.datetime | None = None) -> SealedDossier:
        """Freeze the dossier and fingerprint it, before any market is read."""
        payload = self.seal_payload()
        assert_odds_free(payload, where="scellement du dossier sportif")
        return SealedDossier(
            dossier=self,
            data_fingerprint=fingerprint(payload),
            sealed_at=sealed_at or utcnow(),
        )


@dataclass(frozen=True, slots=True)
class SealedDossier:
    """A sport dossier that can no longer change, with its audit digest."""

    dossier: SportDossier
    data_fingerprint: str
    sealed_at: dt.datetime
    supersedes: str | None = None
    reason: str = "évaluation initiale"

    @property
    def fixture(self) -> Fixture:
        return self.dossier.fixture

    @property
    def probabilities(self) -> OutcomeProbabilities:
        return self.dossier.probabilities

    @property
    def score_matrix(self) -> ScoreMatrix:
        return self.dossier.score_matrix

    @property
    def short_fingerprint(self) -> str:
        return self.data_fingerprint[:16]

    def revise(self, dossier: SportDossier, *, reason: str) -> SealedDossier:
        """A new dossier superseding this one, for *sporting* news only.

        The caller states the reason, and the chain of digests records that a
        revision happened rather than overwriting what was believed before.
        """
        if not reason.strip():
            raise ValueError("une révision doit indiquer sa raison")
        payload = dossier.seal_payload()
        assert_odds_free(payload, where="révision du dossier sportif")
        return SealedDossier(
            dossier=dossier,
            data_fingerprint=fingerprint(payload),
            sealed_at=utcnow(),
            supersedes=self.data_fingerprint,
            reason=reason,
        )

    def render(self) -> str:
        lines = [
            f"Dossier sportif scellé le {self.sealed_at.strftime('%Y-%m-%d %H:%M %Z')}",
            f"  empreinte : {self.short_fingerprint}…  ({self.reason})",
            f"  as_of     : {self.dossier.as_of.strftime('%Y-%m-%d %H:%M %Z')}",
            f"  historique: {self.dossier.history_size} matchs ({self.dossier.history_span})",
            f"  modèle    : {self.dossier.model_version}",
            f"  1X2       : {self.dossier.probabilities}",
        ]
        if self.supersedes:
            lines.append(f"  remplace  : {self.supersedes[:16]}…")
        return "\n".join(lines)


@dataclass(slots=True)
class ExposureAudit:
    """Records whether a price was consulted before the dossier was sealed.

    The operator asked that prior exposure be *signalled* rather than denied.
    The engine writes here; the report reads it.
    """

    first_odds_access: dt.datetime | None = None
    sealed_at: dt.datetime | None = None
    notes: list[str] = field(default_factory=list)

    def saw_odds(self, note: str = "") -> None:
        if self.first_odds_access is None:
            self.first_odds_access = utcnow()
        if note:
            self.notes.append(note)

    def sealed(self, at: dt.datetime) -> None:
        self.sealed_at = at

    @property
    def leaked(self) -> bool:
        """True when odds were read before the sport dossier was sealed."""
        if self.first_odds_access is None or self.sealed_at is None:
            return False
        return self.first_odds_access < self.sealed_at

    def verdict(self) -> str:
        if self.first_odds_access is None:
            return "aucune cote consultée avant scellement : analyse sportive indépendante"
        if self.leaked:
            return (
                "ATTENTION : des cotes ont été consultées avant le scellement "
                f"({self.first_odds_access.isoformat()}) — l'indépendance de la "
                "phase sportive n'est pas garantie pour cette analyse"
            )
        return "cotes consultées après scellement : séparation respectée"


def build_sport_input(
    fixture: Fixture,
    history: MatchLog,
    as_of: dt.datetime,
    evidence: Sequence[Evidence] = (),
    *,
    competition: str = "",
    result_delay: dt.timedelta = DEFAULT_RESULT_DELAY,
) -> SportInput:
    """Construct the sport phase's input, cut on **information availability**.

    Results are admitted only once they could have been known.  With date-only
    sources that means the day after the match, so an analysis run at midnight
    does not quietly consume that evening's scores.
    """
    tzinfo = as_of.tzinfo or dt.timezone.utc
    known = MatchLog(
        m
        for m in history
        if result_available_at(m.date, tzinfo=tzinfo, delay=result_delay) <= as_of
    )
    return SportInput(
        fixture=fixture,
        history=known,
        as_of=as_of,
        evidence=tuple(evidence),
        competition=competition,
        knowledge_cutoff=as_of,
        cutoff_rule=(
            f"date de disponibilité = date du match + {result_delay.days} j "
            f"(sources sans horaire de coup d'envoi) ; coupure au "
            f"{as_of.strftime('%Y-%m-%d %H:%M %Z')}"
        ),
    )
