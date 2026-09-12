"""Monte Carlo simulation of the remainder of a season.

"Who wins the league?" is not a question about one match, so it cannot be
answered by a scoreline distribution alone.  The remaining fixtures are
simulated jointly, thousands of times, and the final tables are counted.

Two properties matter and are both guaranteed here:

* **Reproducibility.**  The same seed gives byte-identical output, so a
  projection can be re-derived months later and audited.
* **Correct dependence.**  Each iteration plays out an entire season, so the
  correlations that decide title races — the same team has to win *all* of its
  remaining games — are preserved.  Averaging per-match probabilities, the
  common shortcut, destroys exactly that.
"""

from __future__ import annotations

import bisect
import math
import random
from collections.abc import Sequence
from dataclasses import dataclass

from foot.domain import Fixture, MatchLog
from foot.league.table import LeagueRules, LeagueTable
from foot.models.base import MatchModel

__all__ = ["SeasonProjection", "SeasonSimulator", "TeamProjection"]


@dataclass(frozen=True, slots=True)
class TeamProjection:
    """One team's simulated distribution of season outcomes."""

    team: str
    current_points: int
    expected_points: float
    expected_goal_difference: float
    position_probabilities: tuple[float, ...]

    @property
    def title_probability(self) -> float:
        return self.position_probabilities[0]

    def top(self, places: int) -> float:
        """Probability of finishing in the top ``places``."""
        if not 1 <= places <= len(self.position_probabilities):
            raise ValueError(f"places must lie in 1..{len(self.position_probabilities)}")
        return math.fsum(self.position_probabilities[:places])

    def bottom(self, places: int) -> float:
        """Probability of finishing in the bottom ``places`` — relegation risk."""
        if not 1 <= places <= len(self.position_probabilities):
            raise ValueError(f"places must lie in 1..{len(self.position_probabilities)}")
        return math.fsum(self.position_probabilities[-places:])

    @property
    def expected_position(self) -> float:
        return math.fsum(
            (i + 1) * p for i, p in enumerate(self.position_probabilities)
        )


@dataclass(frozen=True, slots=True)
class SeasonProjection:
    """The simulated season, team by team."""

    rows: tuple[TeamProjection, ...]
    iterations: int
    remaining_fixtures: int
    seed: int

    def team(self, name: str) -> TeamProjection:
        for row in self.rows:
            if row.team == name:
                return row
        raise KeyError(f"{name!r} is not in this projection")

    def render(self, *, champions_league: int = 4, relegation: int = 3) -> str:
        """A projection table sorted by expected final position."""
        header = (
            f"{'Team':<22} {'Pts':>4} {'xPts':>7} {'xGD':>7} {'Title':>7} "
            f"{'Top' + str(champions_league):>7} {'Releg':>7}"
        )
        lines = [
            f"{self.iterations} simulations of {self.remaining_fixtures} "
            f"remaining fixtures (seed {self.seed})",
            header,
            "-" * len(header),
        ]
        for row in sorted(self.rows, key=lambda r: r.expected_position):
            lines.append(
                f"{row.team:<22} {row.current_points:>4} {row.expected_points:>7.1f} "
                f"{row.expected_goal_difference:>+7.1f} {row.title_probability * 100:>6.2f}% "
                f"{row.top(champions_league) * 100:>6.2f}% {row.bottom(relegation) * 100:>6.2f}%"
            )
        return "\n".join(lines)

    def __str__(self) -> str:
        return self.render()


class SeasonSimulator:
    """Plays out the rest of a season many times using a fitted match model."""

    __slots__ = ("_exact_tiebreakers", "_model", "_rules", "_seed")

    def __init__(
        self,
        model: MatchModel,
        *,
        rules: LeagueRules | None = None,
        seed: int = 0,
        exact_tiebreakers: bool = False,
    ) -> None:
        """
        Args:
            model: a *fitted* model supplying each fixture's score distribution.
            rules: points and ordering conventions.
            seed: makes the whole projection reproducible.
            exact_tiebreakers: apply the full :class:`LeagueTable` ordering,
                including head-to-head, inside every iteration.  Correct but
                far slower; the fast path orders by points, goal difference and
                goals scored, which is what most competitions use anyway.
        """
        self._model = model
        self._rules = rules if rules is not None else LeagueRules()
        self._seed = seed
        self._exact_tiebreakers = exact_tiebreakers

    def run(
        self,
        played: MatchLog,
        remaining: Sequence[Fixture],
        *,
        iterations: int = 10_000,
        teams: Sequence[str] | None = None,
    ) -> SeasonProjection:
        """Simulate ``iterations`` completions of the season.

        Args:
            played: results already in the books.
            remaining: fixtures still to be played.
            iterations: number of simulated seasons.
            teams: the full roster; inferred from the matches and fixtures when
                omitted.
        """
        if iterations < 1:
            raise ValueError("iterations must be positive")

        roster = list(teams) if teams is not None else sorted(
            {t for m in played for t in m.teams} | {t for f in remaining for t in f.teams}
        )
        index = {team: i for i, team in enumerate(roster)}
        n = len(roster)
        if n == 0:
            raise ValueError("no teams to simulate")

        base_table = LeagueTable.from_matches(played, rules=self._rules, teams=roster)
        base_points = [0] * n
        base_goals_for = [0] * n
        base_goals_against = [0] * n
        for row in base_table.rows:
            i = index[row.team]
            base_points[i] = row.points
            base_goals_for[i] = row.goals_for
            base_goals_against[i] = row.goals_against

        samplers = [self._build_sampler(fixture, index) for fixture in remaining]
        rng = random.Random(self._seed)

        position_counts = [[0] * n for _ in range(n)]
        points_total = [0.0] * n
        difference_total = [0.0] * n
        win, draw = self._rules.points_for_win, self._rules.points_for_draw
        loss = self._rules.points_for_loss

        for _ in range(iterations):
            points = base_points[:]
            goals_for = base_goals_for[:]
            goals_against = base_goals_against[:]
            sampled: list[tuple[int, int, int, int]] = []

            for home_i, away_i, cumulative, outcomes in samplers:
                slot = bisect.bisect_left(cumulative, rng.random())
                if slot >= len(outcomes):  # pragma: no cover - float guard
                    slot = len(outcomes) - 1
                home_goals, away_goals = outcomes[slot]
                goals_for[home_i] += home_goals
                goals_against[home_i] += away_goals
                goals_for[away_i] += away_goals
                goals_against[away_i] += home_goals
                if home_goals > away_goals:
                    points[home_i] += win
                    points[away_i] += loss
                elif home_goals < away_goals:
                    points[away_i] += win
                    points[home_i] += loss
                else:
                    points[home_i] += draw
                    points[away_i] += draw
                if self._exact_tiebreakers:
                    sampled.append((home_i, away_i, home_goals, away_goals))

            if self._exact_tiebreakers:
                order = self._exact_order(played, remaining, sampled, roster)
            else:
                order = sorted(
                    range(n),
                    key=lambda i: (
                        -points[i],
                        -(goals_for[i] - goals_against[i]),
                        -goals_for[i],
                        roster[i],
                    ),
                )
            for place, team_index in enumerate(order):
                position_counts[team_index][place] += 1
                points_total[team_index] += points[team_index]
                difference_total[team_index] += goals_for[team_index] - goals_against[team_index]

        rows = tuple(
            TeamProjection(
                team=roster[i],
                current_points=base_points[i],
                expected_points=points_total[i] / iterations,
                expected_goal_difference=difference_total[i] / iterations,
                position_probabilities=tuple(c / iterations for c in position_counts[i]),
            )
            for i in range(n)
        )
        return SeasonProjection(
            rows=rows,
            iterations=iterations,
            remaining_fixtures=len(remaining),
            seed=self._seed,
        )

    def _build_sampler(
        self, fixture: Fixture, index: dict[str, int]
    ) -> tuple[int, int, list[float], list[tuple[int, int]]]:
        """Flatten a fixture's score matrix into a cumulative table, once.

        Building this ahead of the loop turns each sampled scoreline into a
        single binary search rather than a full model evaluation.
        """
        matrix = self._model.score_matrix(fixture)
        cumulative: list[float] = []
        outcomes: list[tuple[int, int]] = []
        running = 0.0
        for home_goals, row in enumerate(matrix.grid):
            for away_goals, p in enumerate(row):
                if p <= 0.0:
                    continue
                running += p
                cumulative.append(running)
                outcomes.append((home_goals, away_goals))
        if cumulative:
            cumulative[-1] = 1.0  # absorb float drift so sampling cannot fall off the end
        return (index[fixture.home], index[fixture.away], cumulative, outcomes)

    def _exact_order(
        self,
        played: MatchLog,
        remaining: Sequence[Fixture],
        sampled: Sequence[tuple[int, int, int, int]],
        roster: Sequence[str],
    ) -> list[int]:
        """Rank a simulated season using the full tiebreak rules."""
        index = {team: i for i, team in enumerate(roster)}
        completed = list(played)
        for fixture, (_, _, home_goals, away_goals) in zip(remaining, sampled, strict=True):
            completed.append(fixture.played(home_goals, away_goals))
        table = LeagueTable.from_matches(completed, rules=self._rules, teams=list(roster))
        return [index[team] for team in table.teams()]
