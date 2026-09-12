"""Command line interface: ``foot <command>`` (or ``python -m foot``).

Every command works out of the box with no data files, by falling back to a
reproducible synthetic league.  That makes the whole library explorable in one
command and makes every example in the documentation verifiable.
"""

from __future__ import annotations

import argparse
import datetime as dt
import math
import sys
from collections.abc import Sequence

from foot import __version__
from foot.data.csv_source import load_matches
from foot.data.synthetic import LeagueTruth, synthetic_league, synthetic_odds
from foot.domain import Fixture, MatchLog
from foot.evaluation.backtest import (
    BaseRateForecaster,
    BlendForecaster,
    EloForecaster,
    Forecaster,
    MarketForecaster,
    ModelForecaster,
    walk_forward,
)
from foot.evaluation.betting import simulate_betting
from foot.league.table import LeagueTable
from foot.market.devig import DevigMethod, fair_probabilities, shin_insider_fraction
from foot.market.kelly import kelly_portfolio
from foot.market.odds import MatchOdds
from foot.models.dixon_coles import DixonColesModel
from foot.models.poisson import PoissonModel
from foot.ratings.elo import EloRatingSystem
from foot.simulation.season import SeasonSimulator

__all__ = ["main"]

_RULE = "=" * 78


def _heading(title: str) -> str:
    return f"\n{_RULE}\n{title}\n{_RULE}"


def _load(args: argparse.Namespace) -> tuple[MatchLog, LeagueTruth | None]:
    """Read the requested data, defaulting to a reproducible synthetic league."""
    if getattr(args, "csv", None):
        matches = load_matches(args.csv)
        if not matches:
            raise ValueError(f"no usable matches found in {args.csv}")
        return matches, None
    return synthetic_league(
        seed=args.seed, seasons=args.seasons, n_teams=args.teams
    )


def _add_data_arguments(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("data source")
    group.add_argument("--csv", help="football-data.co.uk style CSV of results")
    group.add_argument("--seed", type=int, default=7, help="synthetic league seed")
    group.add_argument("--seasons", type=int, default=2, help="synthetic seasons to generate")
    group.add_argument("--teams", type=int, default=20, help="synthetic league size")


def _build_model(args: argparse.Namespace) -> DixonColesModel:
    half_life = None if args.half_life <= 0 else args.half_life
    if getattr(args, "poisson", False):
        return PoissonModel(half_life_days=half_life)
    return DixonColesModel(half_life_days=half_life)


def _add_model_arguments(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("model")
    group.add_argument(
        "--half-life", type=float, default=180.0,
        help="time-decay half-life in days; 0 disables decay",
    )
    group.add_argument(
        "--poisson", action="store_true",
        help="use the independent-Poisson baseline instead of Dixon-Coles",
    )


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #


def command_ratings(args: argparse.Namespace) -> int:
    matches, _ = _load(args)
    model = _build_model(args).fit(matches)
    parameters = model.parameters
    print(_heading(f"Team ratings — {len(matches)} matches, {len(parameters.teams)} teams"))
    print(f"{parameters.diagnostics}")
    print(
        f"home advantage = {parameters.home_advantage:+.4f} log-goals "
        f"({(math.exp(parameters.home_advantage) - 1) * 100:+.1f}% scoring rate), "
        f"rho = {parameters.rho:+.4f}"
    )
    header = (
        f"{'#':>2}  {'Team':<22} {'Attack':>8} {'Defence':>8} "
        f"{'Strength':>9} {'xGF':>6} {'xGA':>6}"
    )
    print(f"\n{header}\n{'-' * len(header)}")
    for position, row in enumerate(model.ratings_table(), start=1):
        print(
            f"{position:>2}  {row.team:<22} {row.attack:>+8.3f} {row.defence:>+8.3f} "
            f"{row.strength:>+9.3f} {row.expected_scored_vs_average:>6.2f} "
            f"{row.expected_conceded_vs_average:>6.2f}"
        )
    print("\nxGF/xGA are multiples of a league-average opponent's scoring rate.")
    return 0


def command_predict(args: argparse.Namespace) -> int:
    matches, _ = _load(args)
    model = _build_model(args).fit(matches)
    home = args.home or model.parameters.ranking()[0][0]
    away = args.away or model.parameters.ranking()[-1][0]
    date = dt.date.fromisoformat(args.date) if args.date else matches.end
    fixture = Fixture(home, away, date, neutral=args.neutral)

    matrix = model.score_matrix(fixture)
    outcome = matrix.outcome_probabilities()
    home_rate, away_rate = model.rates(fixture)

    print(_heading(f"{home} vs {away}  ({date.isoformat()})"))
    print(f"expected goals      {home_rate:.3f} - {away_rate:.3f}")
    print(f"1X2                 {outcome}")
    print(
        "fair odds           "
        + " / ".join(f"{o:.2f}" for o in outcome.fair_odds())
    )
    print(f"most likely score   {matrix.most_likely_score()[0]} "
          f"({matrix.most_likely_score()[1] * 100:.1f}%)")
    print(f"both teams to score {matrix.both_teams_to_score() * 100:.1f}%")
    clean_home, clean_away = matrix.clean_sheet_probabilities()
    print(f"clean sheets        {home} {clean_home * 100:.1f}% / {away} {clean_away * 100:.1f}%")

    print("\ntotals")
    for line in (1.5, 2.5, 3.5):
        totals = matrix.totals(line)
        print(f"  over/under {line:<4} over {totals.over * 100:5.1f}%   "
              f"under {totals.under * 100:5.1f}%")
    print("\nasian handicap (home side)")
    for line in (-1.5, -1.0, -0.5, 0.0, 0.5, 1.0):
        handicap = matrix.asian_handicap(line)
        print(
            f"  {line:>+5.2f}   home {handicap.home * 100:5.1f}%   "
            f"push {handicap.push * 100:5.1f}%   away {handicap.away * 100:5.1f}%"
        )
    print("\nmost likely scorelines")
    for score, probability in matrix.top_scorelines(8):
        print(f"  {score}   {probability * 100:5.2f}%")
    return 0


def command_table(args: argparse.Namespace) -> int:
    matches, _ = _load(args)
    if args.season_only:
        matches = matches.after(matches.end - dt.timedelta(days=args.window))
    print(_heading(f"League table — {len(matches)} matches"))
    print(LeagueTable.from_matches(matches).render())
    return 0


def command_backtest(args: argparse.Namespace) -> int:
    matches, truth = _load(args)
    forecasters: list[Forecaster] = [
        BaseRateForecaster(),
        EloForecaster("elo"),
        ModelForecaster(
            "poisson",
            lambda: PoissonModel(half_life_days=args.half_life or None),
            min_matches=args.min_matches,
            refit_interval=args.refit,
        ),
        ModelForecaster(
            "dixon-coles",
            lambda: DixonColesModel(half_life_days=args.half_life or None),
            min_matches=args.min_matches,
            refit_interval=args.refit,
        ),
    ]
    forecasters.append(
        BlendForecaster("blend dc+elo", [forecasters[3], forecasters[1]], [0.75, 0.25])
    )
    book = None
    if truth is not None and args.market:
        book = synthetic_odds(matches, truth, seed=args.seed + 1000, noise=args.market_noise)
        forecasters.append(MarketForecaster(book))

    print(_heading("Walk-forward backtest"))
    print(f"data: {matches}")
    result = walk_forward(matches, forecasters)
    print(result.summary(baseline="base rate"))
    print("\ncalibration of the Dixon-Coles forecasts")
    print(result.calibration("dixon-coles", bins=args.bins))

    if book is not None:
        print("\nstaking the Dixon-Coles disagreements with the book")
        for fraction in (1.0, 0.5, 0.25):
            outcome = simulate_betting(
                result.records, book, "dixon-coles",
                kelly_fraction=fraction, minimum_edge=args.min_edge,
            )
            print(f"  {fraction:>5.2f} Kelly  {outcome.summary()}")
        print(
            "\n  The synthetic book is deliberately noisy, so these returns measure\n"
            "  the machinery, not real-world profitability. A real book is sharper\n"
            "  and moves against you."
        )
    return 0


def command_simulate(args: argparse.Namespace) -> int:
    matches, _ = _load(args)
    season = matches.seasons()[-1]  # project the most recent season only
    dates = season.dates()
    if len(dates) < 4:
        raise ValueError("need more match dates to split a season")
    cut = dates[int(len(dates) * args.progress)]
    remaining = season.after(cut)
    if not remaining:
        raise ValueError("nothing left to simulate; lower --progress")

    # Fit on everything available before the cut, project only this season.
    model = _build_model(args).fit(matches.before(cut))
    simulator = SeasonSimulator(model, seed=args.seed)
    projection = simulator.run(
        season.before(cut),
        [m.fixture for m in remaining],
        iterations=args.iterations,
        teams=list(season.teams),
    )
    print(_heading(f"Season projection from {cut.isoformat()}"))
    print(f"model: {model.parameters.diagnostics}")
    print(projection.render(champions_league=args.top, relegation=args.relegation))
    print("\nwhat actually happened")
    print(LeagueTable.from_matches(season).render())
    return 0


def command_devig(args: argparse.Namespace) -> int:
    odds = MatchOdds(*args.odds)
    print(_heading(f"Market analysis — {odds}"))
    print(f"book sum          {odds.booksum:.6f}")
    print(f"margin            {odds.overround * 100:.3f}%")
    print(f"shin insider z    {shin_insider_fraction(odds.raw_probabilities()) * 100:.3f}%")
    print(f"\n{'method':<16} {'home':>8} {'draw':>8} {'away':>8}   fair odds")
    print("-" * 62)
    for method in DevigMethod:
        probabilities = fair_probabilities(odds, method)
        fair = " / ".join(f"{o:.3f}" for o in probabilities.fair_odds())
        print(
            f"{method.value:<16} {probabilities.home:>8.4f} {probabilities.draw:>8.4f} "
            f"{probabilities.away:>8.4f}   {fair}"
        )
    return 0


def command_kelly(args: argparse.Namespace) -> int:
    if len(args.probabilities) != len(args.odds):
        raise ValueError("--probabilities and --odds must have the same length")
    allocation = kelly_portfolio(args.probabilities, args.odds, fraction=args.fraction)
    print(_heading(f"Kelly staking ({args.fraction:g} Kelly)"))
    print(f"{'outcome':<10} {'prob':>8} {'odds':>8} {'edge':>9} {'stake':>9}")
    print("-" * 48)
    for i, (p, o) in enumerate(zip(args.probabilities, args.odds, strict=True)):
        print(
            f"{i + 1:<10} {p:>8.4f} {o:>8.2f} {p * o - 1:>+9.4f} "
            f"{allocation.stakes[i]:>9.4f}"
        )
    print("-" * 48)
    print(f"{'total':<10} {sum(args.probabilities):>8.4f} {'':>8} {'':>9} "
          f"{allocation.total_staked:>9.4f}")
    print(f"\nreserve held back   {allocation.reserve:.4f}")
    print(f"expected log growth {allocation.growth_rate:+.6f} per bet")
    booksum = math.fsum(1.0 / o for o in args.odds)
    if booksum < 1.0:
        print(
            f"\nnote: these prices sum to {booksum:.4f} < 1, so the book is an arbitrage.\n"
            f"      Staking in proportion to 1/odds locks in "
            f"{1.0 / booksum - 1.0:+.2%} risk-free;\n"
            "      Kelly trades a little of that certainty for a higher growth rate."
        )
    return 0


def command_demo(args: argparse.Namespace) -> int:
    """One command that exercises the whole library end to end."""
    matches, truth = synthetic_league(seed=args.seed, seasons=2)
    assert truth is not None
    print(_heading("foot — end-to-end demonstration"))
    print(f"generated {matches} from known parameters")
    stats = matches.goal_stats()
    print(
        f"  {stats['goals_per_game']:.2f} goals/game, "
        f"home wins {stats['home_win_rate'] * 100:.1f}%, "
        f"draws {stats['draw_rate'] * 100:.1f}%, "
        f"away wins {stats['away_win_rate'] * 100:.1f}%"
    )

    model = DixonColesModel(half_life_days=180).fit(matches)
    parameters = model.parameters
    print(_heading("1. Dixon-Coles fit vs the true generating parameters"))
    print(f"   {parameters.diagnostics}")
    print(
        f"   home advantage   estimated {parameters.home_advantage:+.4f}   "
        f"true {truth.home_advantage:+.4f}"
    )
    print(f"   rho              estimated {parameters.rho:+.4f}   true {truth.rho:+.4f}")
    worst = max(
        abs(parameters.attack_of(team) - truth.attack_of(team)) for team in parameters.teams
    )
    print(f"   largest attack-rating error across {len(parameters.teams)} teams: {worst:.4f}")

    best = parameters.ranking()[0][0]
    worst_team = parameters.ranking()[-1][0]
    fixture = Fixture(best, worst_team, matches.end)
    matrix = model.score_matrix(fixture)
    print(_heading(f"2. A forecast: {best} vs {worst_team}"))
    print(f"   expected goals {model.rates(fixture)[0]:.2f} - {model.rates(fixture)[1]:.2f}")
    print(f"   1X2            {matrix.outcome_probabilities()}")
    print(f"   over 2.5       {matrix.totals(2.5).over * 100:.1f}%")
    print(f"   -1.5 handicap  {matrix.asian_handicap(-1.5).home * 100:.1f}%")
    print(f"   top scoreline  {matrix.most_likely_score()[0]} "
          f"({matrix.most_likely_score()[1] * 100:.1f}%)")

    elo = EloRatingSystem(season_regression=1 / 3).rate(matches)
    print(_heading("3. Elo agrees on who is good"))
    for team, rating in elo.ranking()[:5]:
        print(f"   {team:<22} {rating:7.1f}")

    print(_heading("4. Walk-forward backtest (no model ever sees its own future)"))
    book = synthetic_odds(matches, truth, seed=args.seed + 500, noise=0.08)
    forecasters: list[Forecaster] = [
        BaseRateForecaster(),
        EloForecaster("elo"),
        ModelForecaster("poisson", lambda: PoissonModel(half_life_days=180),
                        min_matches=150, refit_interval=20),
        ModelForecaster("dixon-coles", lambda: DixonColesModel(half_life_days=180),
                        min_matches=150, refit_interval=20),
        MarketForecaster(book),
    ]
    result = walk_forward(matches, forecasters)
    print(result.summary(baseline="base rate"))

    print(_heading("5. Staking the model's disagreements with the book"))
    for fraction in (1.0, 0.25):
        outcome = simulate_betting(
            result.records, book, "dixon-coles", kelly_fraction=fraction, minimum_edge=0.05
        )
        print(f"   {fraction:>4.2f} Kelly  {outcome.summary()}")
    model_rps = result.scorecards["dixon-coles"].rps
    market_rps = result.scorecards["market"].rps
    verdict = "beat" if model_rps < market_rps else "did not beat"
    print(
        f"\n   The model {verdict} the book on RPS "
        f"({model_rps:.5f} vs {market_rps:.5f}), and the bankroll above says what\n"
        "   that was worth. A better score is not the same thing as an edge: the\n"
        "   money is made on the fixtures where you disagree, not on average.\n"
        "   (The book here is synthetic and noisy; this measures the pipeline,\n"
        "    not profitability against a real bookmaker.)"
    )

    # Project the final season only, so the table reads like a real one.
    final_season = matches.seasons()[-1]
    dates = final_season.dates()
    cut = dates[int(len(dates) * 0.65)]
    played_all, remaining = matches.before(cut), final_season.after(cut)
    projection = SeasonSimulator(
        DixonColesModel(half_life_days=180).fit(played_all), seed=args.seed
    ).run(
        final_season.before(cut),
        [m.fixture for m in remaining],
        iterations=args.iterations,
        teams=list(final_season.teams),
    )
    print(_heading(f"6. Monte Carlo projection of the final season from {cut.isoformat()}"))
    print(projection.render())
    print("\n" + _RULE)
    print("Everything above is reproducible: same seed, same numbers.")
    print(_RULE)
    return 0


# --------------------------------------------------------------------------- #
# Argument parsing
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="foot",
        description="Football modelling: Dixon-Coles, Elo, markets, backtests, simulation.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"foot {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    demo = subparsers.add_parser("demo", help="run an end-to-end tour of the library")
    demo.add_argument("--seed", type=int, default=7)
    demo.add_argument("--iterations", type=int, default=5000)
    demo.set_defaults(handler=command_demo)

    ratings = subparsers.add_parser("ratings", help="fit a model and show team ratings")
    _add_data_arguments(ratings)
    _add_model_arguments(ratings)
    ratings.set_defaults(handler=command_ratings)

    predict = subparsers.add_parser("predict", help="full market breakdown for one fixture")
    _add_data_arguments(predict)
    _add_model_arguments(predict)
    predict.add_argument("--home", help="home team (defaults to the strongest)")
    predict.add_argument("--away", help="away team (defaults to the weakest)")
    predict.add_argument("--date", help="fixture date, ISO format")
    predict.add_argument("--neutral", action="store_true", help="neutral venue")
    predict.set_defaults(handler=command_predict)

    table = subparsers.add_parser("table", help="print a league table")
    _add_data_arguments(table)
    table.add_argument("--season-only", action="store_true", help="restrict to a recent window")
    table.add_argument("--window", type=int, default=330, help="window length in days")
    table.set_defaults(handler=command_table)

    backtest = subparsers.add_parser("backtest", help="compare forecasters walk-forward")
    _add_data_arguments(backtest)
    backtest.add_argument("--half-life", type=float, default=180.0)
    backtest.add_argument("--min-matches", type=int, default=150)
    backtest.add_argument("--refit", type=int, default=20, help="matches between refits")
    backtest.add_argument("--bins", type=int, default=10, help="calibration bins")
    backtest.add_argument("--market", action="store_true", help="add a synthetic bookmaker")
    backtest.add_argument("--market-noise", type=float, default=0.08)
    backtest.add_argument("--min-edge", type=float, default=0.05)
    backtest.set_defaults(handler=command_backtest)

    simulate = subparsers.add_parser("simulate", help="Monte Carlo the rest of a season")
    _add_data_arguments(simulate)
    _add_model_arguments(simulate)
    simulate.add_argument("--progress", type=float, default=0.65,
                          help="fraction of match dates already played")
    simulate.add_argument("--iterations", type=int, default=10000)
    simulate.add_argument("--top", type=int, default=4, help="places counted as qualification")
    simulate.add_argument("--relegation", type=int, default=3)
    simulate.set_defaults(handler=command_simulate)

    devig = subparsers.add_parser("devig", help="strip the margin from 1X2 odds")
    devig.add_argument("odds", type=float, nargs=3, metavar=("HOME", "DRAW", "AWAY"))
    devig.set_defaults(handler=command_devig)

    kelly = subparsers.add_parser("kelly", help="optimal simultaneous stake sizing")
    kelly.add_argument("--probabilities", type=float, nargs="+", required=True)
    kelly.add_argument("--odds", type=float, nargs="+", required=True)
    kelly.add_argument("--fraction", type=float, default=1.0, help="Kelly multiplier")
    kelly.set_defaults(handler=command_kelly)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point.  Returns a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except (ValueError, KeyError, FileNotFoundError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
