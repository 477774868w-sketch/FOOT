"""Domain types: validation, invariants and leak-free time slicing."""

from __future__ import annotations

import datetime as dt
import itertools

from foot.data.synthetic import synthetic_league
from foot.domain import Fixture, Match, MatchLog, Outcome, OutcomeProbabilities, Score

from support import assert_close, assert_probability_vector, assert_raises

DAY = dt.date(2024, 3, 1)


def test_score_classifies_outcomes() -> None:
    assert Score(2, 1).outcome is Outcome.HOME_WIN
    assert Score(1, 1).outcome is Outcome.DRAW
    assert Score(0, 3).outcome is Outcome.AWAY_WIN
    assert Score(2, 1).margin == 1
    assert Score(2, 1).total == 3
    assert Score(2, 1).reversed() == Score(1, 2)
    assert Score(2, 1).both_teams_scored
    assert not Score(2, 0).both_teams_scored


def test_score_rejects_impossible_values() -> None:
    with assert_raises(ValueError, match="non-negative"):
        Score(-1, 0)
    with assert_raises(TypeError, match="int"):
        Score(1.5, 0)  # type: ignore[arg-type]
    with assert_raises(TypeError):
        Score(True, 0)  # booleans are ints, and a goal count is not a flag


def test_score_and_outcome_parsing() -> None:
    assert Score.parse("3-1") == Score(3, 1)
    assert Score.parse(" 3 : 1 ") == Score(3, 1)
    assert Outcome.parse("1") is Outcome.HOME_WIN
    assert Outcome.parse("x") is Outcome.DRAW
    assert Outcome.parse("away") is Outcome.AWAY_WIN
    with assert_raises(ValueError):
        Score.parse("3-1-2")
    with assert_raises(ValueError):
        Outcome.parse("maybe")


def test_fixture_rejects_a_team_playing_itself() -> None:
    with assert_raises(ValueError, match="cannot play itself"):
        Fixture("Arsenal", "Arsenal", DAY)
    with assert_raises(ValueError, match="non-empty"):
        Fixture("", "Chelsea", DAY)
    with assert_raises(TypeError, match="date"):
        Fixture("Arsenal", "Chelsea", "2024-03-01")  # type: ignore[arg-type]


def test_match_accessors_are_symmetric() -> None:
    match = Fixture("A", "B", DAY).played(2, 1)
    assert match.goals_for("A") == 2 and match.goals_against("A") == 1
    assert match.goals_for("B") == 1 and match.goals_against("B") == 2
    assert match.points_for("A") == 3 and match.points_for("B") == 0
    assert match.fixture == Fixture("A", "B", DAY)
    with assert_raises(KeyError):
        match.goals_for("C")


def test_matchlog_sorts_and_is_immutable() -> None:
    late = Match("A", "B", dt.date(2024, 5, 1), Score(1, 0))
    early = Match("C", "D", dt.date(2023, 5, 1), Score(0, 0))
    log = MatchLog([late, early])
    assert list(log) == [early, late]
    assert log.start == early.date and log.end == late.date
    assert log.teams == ("A", "B", "C", "D")
    assert len(log) == 2
    assert log[0] is early
    with assert_raises(TypeError):
        MatchLog(["not a match"])  # type: ignore[list-item]


def test_matchlog_slicing_matches_a_naive_filter() -> None:
    log, _ = synthetic_league(seed=2, seasons=2)
    for cutoff in log.dates()[::5]:
        assert tuple(log.before(cutoff)) == tuple(m for m in log if m.date < cutoff)
        assert tuple(log.before(cutoff, inclusive=True)) == tuple(
            m for m in log if m.date <= cutoff
        )
        assert tuple(log.after(cutoff)) == tuple(m for m in log if m.date >= cutoff)
        assert tuple(log.after(cutoff, inclusive=False)) == tuple(
            m for m in log if m.date > cutoff
        )
    first, last = log.dates()[3], log.dates()[-4]
    assert tuple(log.between(first, last)) == tuple(
        m for m in log if first <= m.date <= last
    )


def test_matchlog_slicing_is_exclusive_at_the_cutoff() -> None:
    """The property every backtest depends on: `before` never leaks the day itself."""
    log, _ = synthetic_league(seed=4)
    for cutoff in log.dates():
        assert all(m.date < cutoff for m in log.before(cutoff))


def test_matchlog_goal_statistics() -> None:
    log, _ = synthetic_league(seed=3)
    stats = log.goal_stats()
    assert_close(
        stats["home_win_rate"] + stats["draw_rate"] + stats["away_win_rate"],
        1.0,
        label="outcome rates",
    )
    assert_close(
        stats["home_goals_per_game"] + stats["away_goals_per_game"],
        stats["goals_per_game"],
        label="goals per game",
    )
    assert stats["matches"] == len(log)


def test_matchlog_filters_and_algebra() -> None:
    log, _ = synthetic_league(seed=3)
    team = log.teams[0]
    assert all(m.involves(team) for m in log.involving(team))
    assert len(log.involving(team)) == 2 * (len(log.teams) - 1)
    assert len(log.last(10)) == 10
    assert tuple(log.last(10)) == tuple(log)[-10:]
    assert len(log[:5] + log[5:]) == len(log)
    assert log.filter(lambda m: m.score.total > 100) == MatchLog()


def test_outcome_probabilities_validate_and_derive() -> None:
    probabilities = OutcomeProbabilities(0.5, 0.3, 0.2)
    assert_probability_vector(probabilities.as_tuple())
    assert probabilities.most_likely is Outcome.HOME_WIN
    assert probabilities[Outcome.DRAW] == 0.3
    assert_close(probabilities.fair_odds()[0], 2.0, label="fair odds")
    assert_close(
        OutcomeProbabilities(1 / 3, 1 / 3, 1 / 3).entropy(base=3.0), 1.0, label="entropy"
    )
    with assert_raises(ValueError, match="sum to 1"):
        OutcomeProbabilities(0.5, 0.3, 0.3)
    with assert_raises(ValueError, match="out of"):
        OutcomeProbabilities(1.5, -0.3, -0.2)
    with assert_raises(TypeError, match="Outcome"):
        probabilities[0]  # type: ignore[index]


def test_outcome_probabilities_blending_and_flooring() -> None:
    sharp = OutcomeProbabilities(0.9, 0.05, 0.05)
    flat = OutcomeProbabilities(1 / 3, 1 / 3, 1 / 3)
    blended = sharp.shrunk_towards(flat, 0.5)
    assert_close(blended.home, 0.5 * 0.9 + 0.5 / 3, label="blend")
    assert blended.shrunk_towards(flat, 0.0) == blended
    floored = OutcomeProbabilities(1.0, 0.0, 0.0).with_floor(0.01)
    assert floored.draw >= 0.01 and floored.away >= 0.01
    assert_probability_vector(floored.as_tuple())
    with assert_raises(ValueError):
        sharp.with_floor(0.5)


def test_seasons_split_on_the_summer_break() -> None:
    """Concatenated season files carry no boundary column, only a calendar gap."""
    log, _ = synthetic_league(seed=3, seasons=3)
    seasons = log.seasons()
    assert [len(season) for season in seasons] == [380, 380, 380]
    assert sum(len(season) for season in seasons) == len(log)
    for earlier, later in itertools.pairwise(seasons):
        assert earlier.end < later.start
        assert (later.start - earlier.end).days >= 45
    # One season stays one season; an empty log has none.
    single, _ = synthetic_league(seed=3, seasons=1)
    assert len(single.seasons()) == 1
    assert MatchLog().seasons() == ()
    with assert_raises(ValueError, match="gap_days"):
        log.seasons(gap_days=0)


def test_seasons_respect_a_custom_gap() -> None:
    log, _ = synthetic_league(seed=3, seasons=2)
    # Rounds are a week apart, so a 7-day threshold splits every matchweek.
    assert len(log.seasons(gap_days=7)) == len(log.dates())
    assert len(log.seasons(gap_days=400)) == 1
