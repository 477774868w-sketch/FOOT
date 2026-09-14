"""The command line interface: every command must run and stay reproducible."""

from __future__ import annotations

import contextlib
import io
import tempfile
from pathlib import Path

from foot.cli import main
from foot.data.csv_source import load_matches, load_odds, write_matches
from foot.data.synthetic import synthetic_league

from support import assert_raises

FAST = ["--seasons", "1", "--teams", "8", "--seed", "3"]


def run(argv: list[str]) -> str:
    """Run a command, returning its stdout and asserting a clean exit."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = main(argv)
    assert code == 0, f"{argv} exited with {code}"
    return buffer.getvalue()


def test_demo_runs_end_to_end() -> None:
    output = run(["demo", "--seed", "3", "--iterations", "200"])
    for expected in (
        "end-to-end demonstration",
        "Dixon-Coles fit vs the true generating parameters",
        "Walk-forward backtest",
        "Monte Carlo projection",
        "reproducible",
    ):
        assert expected in output, expected


def test_demo_is_reproducible() -> None:
    first = run(["demo", "--seed", "5", "--iterations", "200"])
    again = run(["demo", "--seed", "5", "--iterations", "200"])
    assert first == again


def test_ratings_command() -> None:
    output = run(["ratings", *FAST])
    assert "Team ratings" in output
    assert "home advantage" in output
    assert output.count("FC ") >= 8


def test_ratings_with_the_poisson_baseline() -> None:
    output = run(["ratings", *FAST, "--poisson"])
    assert "rho = +0.0000" in output


def test_predict_command_covers_every_market() -> None:
    output = run(["predict", *FAST])
    for expected in (
        "expected goals",
        "1X2",
        "fair odds",
        "both teams to score",
        "asian handicap",
        "most likely scorelines",
    ):
        assert expected in output, expected


def test_predict_accepts_explicit_teams_and_a_neutral_venue() -> None:
    matches, _ = synthetic_league(seed=3, seasons=1, n_teams=8)
    home, away = matches.teams[0], matches.teams[1]
    normal = run(["predict", *FAST, "--home", home, "--away", away])
    neutral = run(["predict", *FAST, "--home", home, "--away", away, "--neutral"])
    assert home in normal and away in normal
    assert normal != neutral


def test_table_command() -> None:
    output = run(["table", *FAST])
    assert "Pts" in output
    output = run(["table", *FAST, "--season-only", "--window", "200"])
    assert "Pts" in output


def test_backtest_command_with_and_without_a_market() -> None:
    plain = run(["backtest", *FAST, "--min-matches", "30", "--refit", "40"])
    assert "walk-forward backtest" in plain
    assert "base rate" in plain
    assert "calibration" in plain

    with_market = run(
        ["backtest", *FAST, "--min-matches", "30", "--refit", "40", "--market"]
    )
    assert "market" in with_market
    assert "Kelly" in with_market


def test_simulate_command() -> None:
    output = run(["simulate", *FAST, "--iterations", "200"])
    assert "Season projection" in output
    assert "Title" in output
    assert "what actually happened" in output


def test_devig_command_lists_every_method() -> None:
    output = run(["devig", "2.10", "3.40", "3.60"])
    assert "shin insider z" in output
    for method in ("multiplicative", "additive", "power", "shin"):
        assert method in output


def test_kelly_command_flags_an_arbitrage() -> None:
    normal = run(["kelly", "--probabilities", "0.5", "0.3", "0.2",
                  "--odds", "2.5", "3.2", "4.0"])
    assert "expected log growth" in normal
    arbitrage = run(["kelly", "--probabilities", "0.5", "0.28", "0.22",
                     "--odds", "2.3", "3.6", "4.4"])
    assert "arbitrage" in arbitrage


def test_cli_reads_and_writes_csv() -> None:
    matches, _ = synthetic_league(seed=4, seasons=1, n_teams=8)
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "results.csv"
        assert write_matches(path, matches) == len(matches)
        loaded = load_matches(path)
        assert len(loaded) == len(matches)
        assert loaded.teams == matches.teams
        for original, restored in zip(matches, loaded, strict=True):
            assert (original.home, original.away, original.date, original.score) == (
                restored.home, restored.away, restored.date, restored.score,
            )
        output = run(["ratings", "--csv", str(path)])
        assert "Team ratings" in output


def test_csv_reader_skips_unusable_rows_rather_than_inventing_data() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "messy.csv"
        path.write_text(
            "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,B365H,B365D,B365A\n"
            "E0,12/08/2023,Arsenal,Chelsea,2,1,2.10,3.40,3.60\n"
            "E0,13/08/2023,Spurs,,1,0,2.00,3.50,4.00\n"          # missing away team
            "E0,not-a-date,Everton,Fulham,0,0,2.00,3.50,4.00\n"  # unparseable date
            "E0,14/08/2023,Leeds,Luton,,2,2.00,3.50,4.00\n"      # missing score
            "E0,2023-08-15,Wolves,Burnley,3,0,1.80,3.60,4.50\n"  # ISO date is fine
            ,
            encoding="utf-8",
        )
        matches = load_matches(path)
        assert len(matches) == 2
        assert matches.teams == ("Arsenal", "Burnley", "Chelsea", "Wolves")
        assert matches[0].competition == "E0"

        # load_odds needs a fixture and prices, not a result, so the row whose
        # score is missing still yields a usable quote: three, not two.
        book = load_odds(path)
        assert len(book) == 3
        assert next(iter(book.values())).bookmaker == "B365"
        assert load_odds(path, prefix="NOPE") == {}


def test_the_parser_rejects_nonsense() -> None:
    with assert_raises(SystemExit):
        main(["not-a-command"])
    with assert_raises(SystemExit):
        main([])
    with assert_raises(SystemExit):
        main(["--version"])
    # Argument errors are reported through one handler with one exit code.
    assert main(["kelly", "--probabilities", "0.5", "0.5", "--odds", "2.0"]) == 2
    assert main(["ratings", "--csv", "/nonexistent/path.csv"]) == 2
