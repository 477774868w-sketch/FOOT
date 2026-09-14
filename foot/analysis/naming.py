"""Turning what the operator typed into a competition and two teams.

Team names arrive as people write them — "Man Utd", "l'OM", "Inter" — while
providers publish "Manchester United FC", "Olympique de Marseille",
"FC Internazionale Milano".  Resolution here is deliberately conservative: it
resolves when it is sure, and otherwise reports *ambiguous* with candidates,
because a silently wrong team produces a confident analysis of the wrong match.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum

__all__ = ["NameMatch", "NameResolution", "TeamIndex", "normalise"]

_NOISE = (
    "fc", "afc", "cf", "ac", "as", "ss", "sc", "sv", "tsg", "vfb", "vfl", "bsc",
    "rc", "rcd", "ud", "cd", "sd", "us", "usl", "calcio", "club", "de", "the",
    "1899", "1901", "1904", "1907", "1909", "04", "05", "96",
)


def normalise(text: str) -> str:
    """Fold a club name to a comparable core.

    Strips accents, punctuation and the corporate noise ("FC", "1901", "Calcio")
    that differs between feeds while keeping the distinguishing words.

    >>> normalise("Olympique de Marseille")
    'olympique marseille'
    >>> normalise("Manchester United FC")
    'manchester united'
    >>> normalise("Arsenal F.C.")
    'arsenal'
    """
    folded = unicodedata.normalize("NFKD", text.lower())
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    folded = folded.replace("&", " and ").replace("'", " ").replace("-", " ")
    # An abbreviation dot joins its letters instead of splitting them: "F.C."
    # must fold to "fc", which the noise list then drops. Turning it into two
    # separate words left "arsenal f c", which matched nothing. A dot after a
    # digit is left alone, so "1. FSV Mainz" keeps its separation.
    folded = re.sub(r"(?<=[a-z])\.", "", folded)
    folded = re.sub(r"[^a-z0-9 ]+", " ", folded)
    words = [w for w in folded.split() if w and w not in _NOISE]
    return " ".join(words) or folded.strip()


class NameResolution(Enum):
    """How confidently a typed name was matched."""

    EXACT = "exact"
    ALIAS = "alias"
    FUZZY = "approché"
    AMBIGUOUS = "ambigu"
    UNKNOWN = "inconnu"

    @property
    def resolved(self) -> bool:
        return self in (NameResolution.EXACT, NameResolution.ALIAS, NameResolution.FUZZY)


@dataclass(frozen=True, slots=True)
class NameMatch:
    """The outcome of resolving one typed name."""

    query: str
    resolution: NameResolution
    name: str | None = None
    score: float = 0.0
    candidates: tuple[str, ...] = ()

    @property
    def resolved(self) -> bool:
        return self.resolution.resolved and self.name is not None

    def explain(self) -> str:
        if self.resolution is NameResolution.UNKNOWN:
            return f"« {self.query} » : aucune équipe correspondante dans la compétition"
        if self.resolution is NameResolution.AMBIGUOUS:
            return (
                f"« {self.query} » : plusieurs correspondances possibles — "
                f"{', '.join(self.candidates)}. Précisez le nom complet."
            )
        if self.resolution is NameResolution.FUZZY:
            return f"« {self.query} » → {self.name} (approché, score {self.score:.2f})"
        return f"« {self.query} » → {self.name}"


# Hand-curated aliases for the shorthand people actually type.  Keys are
# normalised; values are the normalised provider name they point at.
_ALIASES: Mapping[str, str] = {
    "man utd": "manchester united", "man united": "manchester united",
    "mufc": "manchester united", "manu": "manchester united",
    "man city": "manchester city", "mcfc": "manchester city", "city": "manchester city",
    "spurs": "tottenham hotspur", "tottenham": "tottenham hotspur",
    "wolves": "wolverhampton wanderers", "newcastle": "newcastle united",
    "west ham": "west ham united", "leeds": "leeds united",
    "brighton": "brighton and hove albion", "forest": "nottingham forest",
    "psg": "paris saint germain", "paris sg": "paris saint germain",
    "om": "olympique marseille", "marseille": "olympique marseille",
    "ol": "olympique lyonnais", "lyon": "olympique lyonnais",
    "asse": "saint etienne", "ogc nice": "nice",
    "rennes": "stade rennais", "losc": "lille osc", "lens": "racing lens",
    "barca": "barcelona", "barça": "barcelona", "fcb": "barcelona",
    "real": "real madrid", "atleti": "atletico madrid",
    "atletico": "atletico madrid", "athletic": "athletic bilbao",
    "bayern": "bayern munchen", "munich": "bayern munchen",
    "dortmund": "borussia dortmund", "bvb": "borussia dortmund",
    "gladbach": "borussia monchengladbach", "leverkusen": "bayer 04 leverkusen",
    "inter": "internazionale milano", "milan": "milan",
    "juve": "juventus", "napoli": "napoli", "roma": "roma", "lazio": "lazio",
}


class TeamIndex:
    """Resolves typed names against one competition's roster."""

    __slots__ = ("_by_normal", "_names", "_normals")

    def __init__(self, names: Iterable[str]) -> None:
        self._names = tuple(dict.fromkeys(names))
        self._by_normal: dict[str, list[str]] = {}
        for name in self._names:
            self._by_normal.setdefault(normalise(name), []).append(name)
        self._normals = tuple(self._by_normal)

    @property
    def names(self) -> tuple[str, ...]:
        return self._names

    def __len__(self) -> int:
        return len(self._names)

    def resolve(  # noqa: PLR0911 - one early return per resolution strategy
        self, query: str, *, cutoff: float = 0.72
    ) -> NameMatch:
        """Resolve one typed name, reporting ambiguity rather than guessing.

        Strategies are tried in decreasing order of certainty — exact, alias,
        containment, then fuzzy — and each returns as soon as it is sure, which
        is why this reads as a chain of early exits rather than one expression.
        """
        cleaned = query.strip()
        if not cleaned:
            return NameMatch(query, NameResolution.UNKNOWN)
        target = normalise(cleaned)

        exact = self._by_normal.get(target)
        if exact:
            if len(exact) > 1:
                return NameMatch(query, NameResolution.AMBIGUOUS, candidates=tuple(exact))
            return NameMatch(query, NameResolution.EXACT, exact[0], 1.0)

        aliased = _ALIASES.get(target)
        if aliased:
            hit = self._by_normal.get(aliased)
            if hit and len(hit) == 1:
                return NameMatch(query, NameResolution.ALIAS, hit[0], 1.0)
            if hit:
                return NameMatch(query, NameResolution.AMBIGUOUS, candidates=tuple(hit))

        # Containment: "arsenal" inside "arsenal" but also "inter" inside
        # "internazionale milano".  Several hits means ambiguous, not a guess.
        contained = [n for n in self._normals if target and target in n.split()]
        if not contained:
            contained = [n for n in self._normals if target and target in n]
        if len(contained) == 1:
            return NameMatch(query, NameResolution.ALIAS, self._by_normal[contained[0]][0], 0.95)
        if len(contained) > 1:
            names = tuple(self._by_normal[n][0] for n in contained)
            return NameMatch(query, NameResolution.AMBIGUOUS, candidates=names)

        close = difflib.get_close_matches(target, self._normals, n=3, cutoff=cutoff)
        if not close:
            # Looser than the resolution cutoff — a suggestion costs nothing —
            # but not so loose that it suggests a club the typed name does not
            # resemble at all. At 0.45, « Machin » proposed Milan, Monaco and
            # Manchester City; on a phone that is an invitation to pick one of
            # them. At 0.55 a real typo still lands (« Napli » → SSC Napoli,
            # « Bayrn Munich » → Bayern München) and nonsense suggests nothing.
            suggestions = difflib.get_close_matches(target, self._normals, n=3, cutoff=0.55)
            return NameMatch(
                query,
                NameResolution.UNKNOWN,
                candidates=tuple(self._by_normal[c][0] for c in suggestions),
            )
        best = close[0]
        score = difflib.SequenceMatcher(None, target, best).ratio()
        if len(close) > 1:
            runner = difflib.SequenceMatcher(None, target, close[1]).ratio()
            if score - runner < 0.08:  # too close to call
                return NameMatch(
                    query,
                    NameResolution.AMBIGUOUS,
                    candidates=tuple(self._by_normal[c][0] for c in close),
                )
        return NameMatch(query, NameResolution.FUZZY, self._by_normal[best][0], score)

    def resolve_pair(self, home: str, away: str) -> tuple[NameMatch, NameMatch]:
        return (self.resolve(home), self.resolve(away))


def build_indexes(rosters: Mapping[str, Sequence[str]]) -> dict[str, TeamIndex]:
    """One :class:`TeamIndex` per competition key."""
    return {key: TeamIndex(names) for key, names in rosters.items()}
