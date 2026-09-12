"""League tables: accounting identities and tiebreak rules."""

from __future__ import annotations

import datetime as dt

from foot.data.synthetic import synthetic_league
from foot.domain import Match, Score
from foot.league.table import LeagueRules, LeagueTable, Tiebreaker

from support import assert_close, assert_raises

DAY = dt.date(2024, 2, 1)


def _played(results: list[tuple[str, str, int, int]]) -> list[Match]:
    return [
        Match(home, away, DAY + dt.timedelta(days=i), Score(hg, ag))
        for i, (home, away, hg, ag) in enumerate(results)
    ]


def test_table_accounting_identities_hold() -> None:
    matches, _ = synthetic_league(seed=5, seasons=2)
    table = LeagueTable.from_matches(matches)
    assert sum(row.played for row in table.rows) == 2 * len(matches)
    assert sum(row.goals_for for row in table.rows) == sum(
        m.score.total for m in matches
    )
    assert sum(row.goals_for for row in table.rows) == sum(
        row.goals_against for row in table.rows
    )
    assert sum(row.goal_difference for row in table.rows) == 0
    assert sum(row.won for row in table.rows) == sum(row.lost for row in table.rows)
    assert sum(row.drawn for row in table.rows) % 2 == 0
    for row in table.rows:
        assert row.played == row.won + row.drawn + row.lost
        assert row.points == 3 * row.won + row.drawn
        assert_close(row.points_per_game, row.points / row.played, label=row.team)


def test_table_is_ordered_by_points_then_the_configured_criteria() -> None:
    matches, _ = synthetic_league(seed=6)
    table = LeagueTable.from_matches(matches)
    keys = [(-row.points, -row.goal_difference, -row.goals_for) for row in table.rows]
    assert keys == sorted(keys)
    assert table.position(table.rows[0].team) == 1
    assert table.row(table.rows[3].team) is table.rows[3]
    assert table.teams()[0] == table.rows[0].team
    assert len(table) == len(matches.teams)
    with assert_raises(KeyError):
        table.position("Nowhere United")
    with assert_raises(KeyError):
        table.row("Nowhere United")


def test_head_to_head_and_goal_difference_can_disagree() -> None:
    """A constructed case where the two conventions crown different teams.

    A and B finish level on points and goal difference.  A scored more goals
    overall, so England ranks A first; B won the head-to-head meeting, so Spain
    ranks B first.  Any implementation that quietly ignores the setting will
    fail one of these two assertions.
    """
    results = _played(
        [
            ("A", "C", 3, 0),
            ("A", "D", 1, 0),
            ("B", "A", 2, 0),
            ("B", "C", 1, 0),
            ("D", "B", 1, 0),
            ("C", "D", 1, 0),
        ]
    )
    english = LeagueTable.from_matches(results, rules=LeagueRules.english())
    spanish = LeagueTable.from_matches(results, rules=LeagueRules.spanish())

    for table in (english, spanish):
        assert table.row("A").points == table.row("B").points == 6
        assert table.row("A").goal_difference == table.row("B").goal_difference == 2

    assert english.position("A") < english.position("B"), "England separates on goals scored"
    assert spanish.position("B") < spanish.position("A"), "Spain separates head to head"


def test_head_to_head_only_applies_inside_a_tied_group() -> None:
    """Teams on different points are never re-ranked by a mini-table."""
    results = _played([("A", "B", 5, 0), ("B", "C", 1, 0), ("C", "A", 1, 0)])
    table = LeagueTable.from_matches(results, rules=LeagueRules.spanish())
    assert table.row("A").points == table.row("B").points == table.row("C").points == 3
    # All three tied, so the mini-table is the whole table and goal difference decides.
    assert table.teams()[0] == "A"


def test_alternative_tiebreakers_are_honoured() -> None:
    results = _played([("A", "C", 1, 0), ("B", "C", 1, 0), ("C", "A", 0, 3), ("C", "B", 0, 1)])
    fewest_conceded = LeagueTable.from_matches(
        results,
        rules=LeagueRules(tiebreakers=(Tiebreaker.GOALS_AGAINST, Tiebreaker.NAME)),
    )
    assert fewest_conceded.row("A").goals_against == fewest_conceded.row("B").goals_against
    most_away_goals = LeagueTable.from_matches(
        results,
        rules=LeagueRules(tiebreakers=(Tiebreaker.AWAY_GOALS_FOR, Tiebreaker.NAME)),
    )
    assert most_away_goals.position("A") < most_away_goals.position("B")
    by_wins = LeagueTable.from_matches(
        results, rules=LeagueRules(tiebreakers=(Tiebreaker.WINS, Tiebreaker.NAME))
    )
    assert by_wins.row("A").won == 2


def test_custom_point_systems() -> None:
    results = _played([("A", "B", 1, 0), ("B", "C", 0, 0)])
    two_points = LeagueTable.from_matches(
        results, rules=LeagueRules(points_for_win=2, points_for_draw=1)
    )
    assert two_points.row("A").points == 2
    assert two_points.row("B").points == 1


def test_tables_can_include_teams_with_no_matches() -> None:
    table = LeagueTable.from_matches(
        _played([("A", "B", 1, 0)]), teams=["A", "B", "C"]
    )
    assert len(table) == 3
    assert table.row("C").played == 0
    assert table.row("C").points == 0
    assert table.row("C").points_per_game == 0.0
    assert LeagueTable.from_matches([]).rows == ()


def test_rendering_is_aligned_and_complete() -> None:
    matches, _ = synthetic_league(seed=7)
    rendered = LeagueTable.from_matches(matches).render()
    lines = rendered.splitlines()
    assert len(lines) == len(matches.teams) + 2  # header plus rule
    assert len({len(line) for line in lines[1:]}) == 1, "columns are ragged"
    assert matches.teams[0] in rendered
    assert "Pts" in lines[0]


def test_rules_are_validated() -> None:
    with assert_raises(ValueError, match="at least one"):
        LeagueRules(tiebreakers=())
    with assert_raises(ValueError, match="repeat"):
        LeagueRules(tiebreakers=(Tiebreaker.GOALS_FOR, Tiebreaker.GOALS_FOR))
