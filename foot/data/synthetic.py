"""Reproducible synthetic leagues with known ground truth.

Real football data cannot tell you whether an estimator is correct — the true
parameters are unobservable.  Synthetic leagues can: generate matches from
*known* attack, defence, home-advantage and ``rho`` values, fit the model back,
and check that the estimates converge on the truth.  That parameter-recovery
argument is the strongest available evidence that a likelihood and its gradient
are right, and it is what :mod:`tests.test_dixon_coles` rests on.
"""

from __future__ import annotations

import datetime as dt
import math
import random
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from foot.domain import Fixture, Match, MatchLog, Score
from foot.models.base import ScoreMatrix
from foot.models.dixon_coles import dixon_coles_tau

if TYPE_CHECKING:
    from foot.market.odds import MatchOdds

__all__ = [
    "SYNTHETIC_MARKER",
    "LeagueTruth",
    "round_robin_schedule",
    "synthetic_league",
    "synthetic_odds",
]

SYNTHETIC_MARKER = "[SYNTHÉTIQUE] Ligue de démonstration"
"""Stamped into every synthetic competition label.

Synthetic data is for demonstrations and tests.  Marking it at the source means
the analysis engine can refuse it in real mode by inspection, rather than
relying on the caller to remember which dataset they loaded.
"""


@dataclass(frozen=True, slots=True)
class LeagueTruth:
    """The generating parameters of a synthetic league."""

    teams: tuple[str, ...]
    attack: tuple[float, ...]
    defence: tuple[float, ...]
    home_advantage: float
    rho: float

    def rates(self, home: str, away: str) -> tuple[float, float]:
        i = self.teams.index(home)
        j = self.teams.index(away)
        return (
            math.exp(self.home_advantage + self.attack[i] - self.defence[j]),
            math.exp(self.attack[j] - self.defence[i]),
        )

    def attack_of(self, team: str) -> float:
        return self.attack[self.teams.index(team)]

    def defence_of(self, team: str) -> float:
        return self.defence[self.teams.index(team)]


def round_robin_schedule(
    teams: list[str],
    start: dt.date,
    *,
    double: bool = True,
    days_between_rounds: int = 7,
    competition: str | None = None,
) -> list[Fixture]:
    """A balanced schedule built with the circle method.

    Every team meets every other exactly once per leg; with ``double=True`` the
    second leg reverses home advantage, so the fixture list is as balanced as a
    real league's.
    """
    if len(teams) < 2:
        raise ValueError("need at least two teams")
    if len(set(teams)) != len(teams):
        raise ValueError("team names must be unique")

    roster = list(teams)
    bye: str | None = None
    if len(roster) % 2 == 1:  # odd leagues need a resting team each round
        bye = "__bye__"
        roster.append(bye)

    n = len(roster)
    rotation = list(range(n))
    first_leg: list[list[tuple[int, int]]] = []
    for round_number in range(n - 1):
        pairs: list[tuple[int, int]] = []
        for slot in range(n // 2):
            a, b = rotation[slot], rotation[n - 1 - slot]
            pairs.append((a, b) if (round_number + slot) % 2 == 0 else (b, a))
        first_leg.append(pairs)
        rotation = [rotation[0], rotation[-1], *rotation[1:-1]]

    legs = [first_leg]
    if double:
        legs.append([[(b, a) for a, b in pairs] for pairs in first_leg])

    fixtures: list[Fixture] = []
    round_index = 0
    for leg in legs:
        for pairs in leg:
            date = start + dt.timedelta(days=days_between_rounds * round_index)
            for a, b in pairs:
                home, away = roster[a], roster[b]
                if bye in (home, away):
                    continue
                fixtures.append(Fixture(home, away, date, competition=competition))
            round_index += 1
    return fixtures


def _sample_scoreline(matrix: ScoreMatrix, rng: random.Random) -> Score:
    """Inverse-CDF sample from a joint scoreline distribution."""
    target = rng.random()
    cumulative = 0.0
    for home_goals, row in enumerate(matrix.grid):
        for away_goals, p in enumerate(row):
            cumulative += p
            if target <= cumulative:
                return Score(home_goals, away_goals)
    return Score(matrix.max_goals, matrix.max_goals)  # pragma: no cover - float guard


def synthetic_league(
    *,
    n_teams: int = 20,
    seed: int = 0,
    seasons: int = 1,
    start: dt.date = dt.date(2023, 8, 12),
    attack_spread: float = 0.33,
    defence_spread: float = 0.28,
    home_advantage: float = 0.26,
    rho: float = -0.06,
    competition: str = SYNTHETIC_MARKER,
) -> tuple[MatchLog, LeagueTruth]:
    """Generate a league whose true parameters are known exactly.

    Args:
        n_teams: number of clubs.
        seed: any two calls with the same seed produce byte-identical data.
        seasons: number of double round-robin seasons to play out.
        attack_spread: standard deviation of the true attack ratings.
        defence_spread: standard deviation of the true defence ratings.
        home_advantage: the true log home-advantage; 0.26 is realistic for a
            European top division.
        rho: the true low-score dependence; realistically a small negative.

    Returns:
        The played matches and the :class:`LeagueTruth` that generated them.
    """
    if n_teams < 2:
        raise ValueError("need at least two teams")
    if seasons < 1:
        raise ValueError("need at least one season")

    rng = random.Random(seed)
    teams = tuple(_team_name(i) for i in range(n_teams))
    attack = [rng.gauss(0.0, attack_spread) for _ in range(n_teams)]
    centre = math.fsum(attack) / n_teams
    attack = [a - centre for a in attack]  # canonical gauge: mean attack is zero
    defence = [rng.gauss(0.0, defence_spread) for _ in range(n_teams)]

    truth = LeagueTruth(
        teams=teams,
        attack=tuple(attack),
        defence=tuple(defence),
        home_advantage=home_advantage,
        rho=rho,
    )

    matches: list[Match] = []
    season_start = start
    for _ in range(seasons):
        fixtures = round_robin_schedule(
            list(teams), season_start, competition=competition
        )
        for fixture in fixtures:
            home_rate, away_rate = truth.rates(fixture.home, fixture.away)
            matrix = ScoreMatrix.from_rates(
                home_rate,
                away_rate,
                max_goals=12,
                correction=lambda h, a, lam, mu: max(
                    dixon_coles_tau(h, a, lam, mu, rho), 0.0
                ),
            )
            score = _sample_scoreline(matrix, rng)
            matches.append(fixture.played(score.home, score.away))
        season_start = max(f.date for f in fixtures) + dt.timedelta(days=60)

    return MatchLog(matches), truth


def _team_name(index: int) -> str:
    """Stable, sortable, human-readable club names: ``FC Alpha``, ``FC Bravo``."""
    alphabet = (
        "Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot", "Golf", "Hotel",
        "India", "Juliett", "Kilo", "Lima", "Mike", "November", "Oscar", "Papa",
        "Quebec", "Romeo", "Sierra", "Tango", "Uniform", "Victor", "Whiskey",
        "Xray", "Yankee", "Zulu",
    )
    if index < len(alphabet):
        return f"FC {alphabet[index]}"
    return f"FC {alphabet[index % len(alphabet)]} {index // len(alphabet) + 1}"


def synthetic_odds(
    matches: MatchLog,
    truth: LeagueTruth,
    *,
    seed: int = 0,
    margin: float = 0.05,
    noise: float = 0.10,
    favourite_longshot_bias: float = 0.0,
    minimum_odds: float = 1.01,
) -> dict[Fixture, MatchOdds]:
    """A synthetic bookmaker quoting the truth, imperfectly.

    The book is built from the *true* generating probabilities, perturbed in
    log space, then loaded with a margin.  A model fitted to the same data can
    therefore beat it, which is what makes the betting machinery testable end
    to end.

    This is emphatically **not** evidence that the model would beat a real
    bookmaker: real books are sharper than ``noise`` and move against you.  Use
    it to verify the pipeline, never to estimate profitability.

    Args:
        seed: independent of the league's own seed, so the same league can be
            paired with different books.
        margin: the overround loaded onto the quoted prices.
        noise: standard deviation of the log-space perturbation.  Zero makes
            the book exactly efficient and unbeatable.
        favourite_longshot_bias: shifts probability from longshots to
            favourites, reproducing the best-documented real market bias.
        minimum_odds: the shortest price the book will quote.  Without a floor,
            loading a margin onto a near-certain favourite produces decimal odds
            below one, which is not a price.
    """
    # Imported here rather than at module scope: `foot.market` builds on
    # `foot.domain`, and keeping `foot.data` free of that edge leaves the
    # dependency graph acyclic and the import order obvious.
    from foot.market.odds import MatchOdds  # noqa: PLC0415

    if margin < 0.0:
        raise ValueError("margin must be non-negative")
    if noise < 0.0:
        raise ValueError("noise must be non-negative")

    rng = random.Random(seed)
    book: dict[Fixture, MatchOdds] = {}
    for match in matches:
        home_rate, away_rate = truth.rates(match.home, match.away)
        matrix = ScoreMatrix.from_rates(
            home_rate,
            away_rate,
            max_goals=12,
            correction=lambda h, a, lam, mu: max(
                dixon_coles_tau(h, a, lam, mu, truth.rho), 0.0
            ),
        )
        probabilities = list(matrix.outcome_probabilities().as_tuple())
        if favourite_longshot_bias:
            centre = math.fsum(probabilities) / 3.0
            probabilities = [
                max(p + favourite_longshot_bias * (p - centre), 1e-6) for p in probabilities
            ]
        logits = [math.log(p) + rng.gauss(0.0, noise) for p in probabilities]
        peak = max(logits)
        weights = [math.exp(v - peak) for v in logits]
        total = math.fsum(weights)
        loaded = _load_margin(
            [w / total for w in weights], margin=margin, minimum_odds=minimum_odds
        )
        quoted = [1.0 / p for p in loaded]
        book[match.fixture] = MatchOdds(
            quoted[0], quoted[1], quoted[2], bookmaker="Synthetic Book"
        )
    return book


def _load_margin(
    probabilities: Sequence[float], *, margin: float, minimum_odds: float
) -> list[float]:
    """Apply a bookmaker's margin without ever quoting a price below ``minimum_odds``.

    Scaling probabilities by ``1 + margin`` is the textbook way to load a book,
    but a heavy favourite can be pushed past an implied probability of one,
    which corresponds to decimal odds below evens-money-on — a price no book
    quotes and no bettor could take.  Real books cap the favourite and push the
    remaining margin onto the other outcomes, which is what happens here: the
    capped outcomes are pinned, the rest are rescaled to preserve the book sum,
    and the pass repeats until nothing exceeds the cap.
    """
    if minimum_odds <= 1.0:
        raise ValueError(f"minimum_odds must exceed 1, got {minimum_odds!r}")
    booksum = 1.0 + margin
    ceiling = 1.0 / minimum_odds
    if len(probabilities) * ceiling < booksum:
        raise ValueError("minimum_odds is too high to support the requested margin")

    values = [p * booksum for p in probabilities]
    for _ in range(len(values)):
        if max(values) <= ceiling:
            break
        pinned = [i for i, v in enumerate(values) if v >= ceiling]
        free = [i for i in range(len(values)) if i not in pinned]
        free_mass = math.fsum(values[i] for i in free)
        if not free or free_mass <= 0.0:
            values = [booksum / len(values)] * len(values)
            break
        scale = (booksum - ceiling * len(pinned)) / free_mass
        for i in pinned:
            values[i] = ceiling
        for i in free:
            values[i] *= scale
    return values
