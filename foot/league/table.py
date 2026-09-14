"""League tables, including the tiebreakers that actually decide titles.

Two teams level on points are separated differently in Spain (head-to-head)
than in England (goal difference), and getting that wrong changes who wins the
league.  The ordering here is therefore configurable, and head-to-head is
implemented properly: a mini-table over the matches played *among the tied
teams only*, applied to the whole tied group at once rather than pairwise.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import Enum

from foot.domain import Match

__all__ = ["LeagueRules", "LeagueTable", "TableRow", "Tiebreaker"]


class Tiebreaker(Enum):
    """Criteria for separating teams level on points."""

    GOAL_DIFFERENCE = "goal_difference"
    GOALS_FOR = "goals_for"
    GOALS_AGAINST = "goals_against"
    WINS = "wins"
    HEAD_TO_HEAD = "head_to_head"
    AWAY_GOALS_FOR = "away_goals_for"
    NAME = "name"


@dataclass(frozen=True, slots=True)
class LeagueRules:
    """Scoring and ordering conventions of a competition."""

    points_for_win: int = 3
    points_for_draw: int = 1
    points_for_loss: int = 0
    tiebreakers: tuple[Tiebreaker, ...] = (
        Tiebreaker.GOAL_DIFFERENCE,
        Tiebreaker.GOALS_FOR,
        Tiebreaker.NAME,
    )

    def __post_init__(self) -> None:
        if not self.tiebreakers:
            raise ValueError("at least one tiebreaker is required")
        if len(set(self.tiebreakers)) != len(self.tiebreakers):
            raise ValueError("tiebreakers must not repeat")

    @classmethod
    def english(cls) -> LeagueRules:
        """Goal difference, then goals scored — the Premier League's order."""
        return cls()

    @classmethod
    def spanish(cls) -> LeagueRules:
        """Head-to-head first, as used in La Liga."""
        return cls(
            tiebreakers=(
                Tiebreaker.HEAD_TO_HEAD,
                Tiebreaker.GOAL_DIFFERENCE,
                Tiebreaker.GOALS_FOR,
                Tiebreaker.NAME,
            )
        )


@dataclass(frozen=True, slots=True)
class TableRow:
    """One team's record."""

    team: str
    played: int
    won: int
    drawn: int
    lost: int
    goals_for: int
    goals_against: int
    points: int

    @property
    def goal_difference(self) -> int:
        return self.goals_for - self.goals_against

    @property
    def points_per_game(self) -> float:
        return self.points / self.played if self.played else 0.0

    def __str__(self) -> str:
        return (
            f"{self.team:<22} {self.played:>3} {self.won:>3} {self.drawn:>3} {self.lost:>3} "
            f"{self.goals_for:>4} {self.goals_against:>4} {self.goal_difference:>+5} "
            f"{self.points:>4}"
        )


@dataclass(frozen=True, slots=True)
class _Accumulator:
    """Mutable-by-replacement counters used while walking the match list."""

    played: int = 0
    won: int = 0
    drawn: int = 0
    lost: int = 0
    goals_for: int = 0
    goals_against: int = 0
    away_goals_for: int = 0


class LeagueTable:
    """An ordered league table built from played matches."""

    __slots__ = ("_index", "_rows", "_rules")

    def __init__(self, rows: Sequence[TableRow], rules: LeagueRules) -> None:
        self._rows = tuple(rows)
        self._rules = rules
        self._index = {row.team: i for i, row in enumerate(self._rows)}

    # -- Construction ------------------------------------------------------
    @classmethod
    def from_matches(
        cls,
        matches: Iterable[Match],
        *,
        rules: LeagueRules | None = None,
        teams: Sequence[str] | None = None,
    ) -> LeagueTable:
        """Build and order a table.

        Args:
            matches: played matches to count.
            rules: scoring and tiebreak conventions; English by default.
            teams: force these teams into the table even with no matches
                played, which keeps a table stable at the start of a season.
        """
        rules = rules if rules is not None else LeagueRules()
        played = list(matches)
        roster = list(teams) if teams is not None else sorted(
            {t for match in played for t in match.teams}
        )
        stats = {team: _Accumulator() for team in roster}

        for match in played:
            for team, scored, conceded, at_home in (
                (match.home, match.score.home, match.score.away, True),
                (match.away, match.score.away, match.score.home, False),
            ):
                if team not in stats:
                    stats[team] = _Accumulator()
                    roster.append(team)
                current = stats[team]
                stats[team] = _Accumulator(
                    played=current.played + 1,
                    won=current.won + (1 if scored > conceded else 0),
                    drawn=current.drawn + (1 if scored == conceded else 0),
                    lost=current.lost + (1 if scored < conceded else 0),
                    goals_for=current.goals_for + scored,
                    goals_against=current.goals_against + conceded,
                    away_goals_for=current.away_goals_for + (0 if at_home else scored),
                )

        rows = [
            TableRow(
                team=team,
                played=stats[team].played,
                won=stats[team].won,
                drawn=stats[team].drawn,
                lost=stats[team].lost,
                goals_for=stats[team].goals_for,
                goals_against=stats[team].goals_against,
                points=(
                    stats[team].won * rules.points_for_win
                    + stats[team].drawn * rules.points_for_draw
                    + stats[team].lost * rules.points_for_loss
                ),
            )
            for team in roster
        ]
        ordered = _order(rows, played, rules, stats)
        return cls(ordered, rules)

    # -- Access ------------------------------------------------------------
    @property
    def rows(self) -> tuple[TableRow, ...]:
        return self._rows

    @property
    def rules(self) -> LeagueRules:
        return self._rules

    def __len__(self) -> int:
        return len(self._rows)

    def __iter__(self) -> Iterable[TableRow]:
        return iter(self._rows)

    def row(self, team: str) -> TableRow:
        try:
            return self._rows[self._index[team]]
        except KeyError:
            raise KeyError(f"{team!r} is not in this table") from None

    def position(self, team: str) -> int:
        """One-based league position."""
        try:
            return self._index[team] + 1
        except KeyError:
            raise KeyError(f"{team!r} is not in this table") from None

    def teams(self) -> tuple[str, ...]:
        """Teams in table order, champion first."""
        return tuple(row.team for row in self._rows)

    def render(self) -> str:
        """The table as aligned plain text."""
        header = (
            f"{'#':>2}  {'Team':<22} {'P':>3} {'W':>3} {'D':>3} {'L':>3} "
            f"{'GF':>4} {'GA':>4} {'GD':>5} {'Pts':>4}"
        )
        lines = [header, "-" * len(header)]
        lines.extend(f"{i + 1:>2}  {row}" for i, row in enumerate(self._rows))
        return "\n".join(lines)

    def __str__(self) -> str:
        return self.render()

    def __repr__(self) -> str:
        return f"LeagueTable({len(self._rows)} teams)"


def _order(
    rows: Sequence[TableRow],
    matches: Sequence[Match],
    rules: LeagueRules,
    stats: dict[str, _Accumulator],
) -> list[TableRow]:
    """Sort rows by points, then by the configured tiebreakers."""
    before, after = _split_at_head_to_head(rules.tiebreakers)

    def key(row: TableRow) -> tuple[float, ...]:
        return (-row.points, *(_criterion(row, c, stats) for c in before))

    ordered = sorted(rows, key=lambda row: (*key(row), row.team))
    if Tiebreaker.HEAD_TO_HEAD not in rules.tiebreakers:
        return ordered

    resolved: list[TableRow] = []
    start = 0
    while start < len(ordered):
        end = start + 1
        while end < len(ordered) and key(ordered[end]) == key(ordered[start]):
            end += 1
        group = ordered[start:end]
        resolved.extend(group if len(group) == 1 else _break_tie(group, matches, rules, after))
        start = end
    return resolved


def _split_at_head_to_head(
    tiebreakers: Sequence[Tiebreaker],
) -> tuple[tuple[Tiebreaker, ...], tuple[Tiebreaker, ...]]:
    """Criteria applied before head-to-head, and those applied after it."""
    if Tiebreaker.HEAD_TO_HEAD not in tiebreakers:
        return (tuple(tiebreakers), ())
    cut = tiebreakers.index(Tiebreaker.HEAD_TO_HEAD)
    return (tuple(tiebreakers[:cut]), tuple(tiebreakers[cut + 1 :]))


def _break_tie(
    group: Sequence[TableRow],
    matches: Sequence[Match],
    rules: LeagueRules,
    remaining: Sequence[Tiebreaker],
) -> list[TableRow]:
    """Re-rank a tied group by a mini-table of the matches among its members."""
    members = {row.team for row in group}
    mini_matches = [
        match for match in matches if match.home in members and match.away in members
    ]
    mini = LeagueTable.from_matches(
        mini_matches,
        rules=LeagueRules(
            points_for_win=rules.points_for_win,
            points_for_draw=rules.points_for_draw,
            points_for_loss=rules.points_for_loss,
            tiebreakers=(Tiebreaker.GOAL_DIFFERENCE, Tiebreaker.GOALS_FOR, Tiebreaker.NAME),
        ),
        teams=sorted(members),
    )
    mini_stats = {row.team: row for row in mini.rows}
    mini_order = {team: i for i, team in enumerate(mini.teams())}

    def key(row: TableRow) -> tuple[float, ...]:
        sub = mini_stats[row.team]
        return (
            -sub.points,
            -sub.goal_difference,
            -sub.goals_for,
            *(_criterion(row, c, {}) for c in remaining if c is not Tiebreaker.NAME),
            float(mini_order[row.team]),
        )

    return sorted(group, key=lambda row: (*key(row), row.team))


def _criterion(row: TableRow, tiebreaker: Tiebreaker, stats: dict[str, _Accumulator]) -> float:
    """Sort value for one criterion; smaller sorts higher."""
    if tiebreaker is Tiebreaker.GOAL_DIFFERENCE:
        return -row.goal_difference
    if tiebreaker is Tiebreaker.GOALS_FOR:
        return -row.goals_for
    if tiebreaker is Tiebreaker.GOALS_AGAINST:
        return row.goals_against
    if tiebreaker is Tiebreaker.WINS:
        return -row.won
    if tiebreaker is Tiebreaker.AWAY_GOALS_FOR:
        accumulator = stats.get(row.team)
        return -(accumulator.away_goals_for if accumulator else 0)
    if tiebreaker is Tiebreaker.NAME:
        return 0.0  # the stable trailing sort on the name handles this
    raise ValueError(f"head-to-head must be handled by the group resolver, got {tiebreaker!r}")
