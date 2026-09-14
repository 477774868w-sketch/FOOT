"""Scoring rules, calibration, leak-free backtesting and bankroll simulation."""

from __future__ import annotations

import math
import random
from collections.abc import Callable

from foot.data.synthetic import _load_margin, synthetic_league, synthetic_odds
from foot.domain import Fixture, MatchLog, Outcome, OutcomeProbabilities
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
from foot.evaluation.calibration import calibration_report
from foot.evaluation.metrics import (
    ScoreCard,
    brier_score,
    ignorance_score,
    log_loss,
    ranked_probability_score,
)
from foot.models.dixon_coles import DixonColesModel
from foot.models.poisson import PoissonModel

from support import assert_close, assert_raises

RULES = (ranked_probability_score, brier_score, log_loss)


def test_published_reference_values() -> None:
    """Values quoted in Constantinou and Fenton (2012), reproduced exactly."""
    forecast = OutcomeProbabilities(0.8, 0.1, 0.1)
    assert_close(
        ranked_probability_score(forecast, Outcome.HOME_WIN), 0.025,
        tolerance=1e-12, label="RPS",
    )
    assert_close(
        ranked_probability_score(OutcomeProbabilities(0.5, 0.25, 0.25), Outcome.HOME_WIN),
        0.15625, tolerance=1e-12, label="RPS of a flat-ish forecast",
    )
    assert_close(brier_score(forecast, Outcome.HOME_WIN), 0.06, tolerance=1e-12, label="Brier")
    assert_close(log_loss(forecast, Outcome.HOME_WIN), -math.log(0.8), label="log loss")
    assert_close(
        ignorance_score(forecast, Outcome.HOME_WIN), -math.log2(0.8), label="ignorance"
    )


def test_perfect_and_worst_possible_forecasts() -> None:
    perfect = OutcomeProbabilities(1.0, 0.0, 0.0)
    assert ranked_probability_score(perfect, Outcome.HOME_WIN) == 0.0
    assert brier_score(perfect, Outcome.HOME_WIN) == 0.0
    assert log_loss(perfect, Outcome.HOME_WIN) == 0.0
    assert ranked_probability_score(perfect, Outcome.AWAY_WIN) == 1.0
    assert brier_score(perfect, Outcome.AWAY_WIN) == 2.0
    assert math.isfinite(log_loss(perfect, Outcome.AWAY_WIN))  # floored, not infinite


def test_the_ranked_probability_score_is_distance_sensitive() -> None:
    """Being wrong by two categories must cost more than being wrong by one."""
    forecast = OutcomeProbabilities(0.8, 0.1, 0.1)
    near = ranked_probability_score(forecast, Outcome.DRAW)
    far = ranked_probability_score(forecast, Outcome.AWAY_WIN)
    assert far > near
    # The Brier score, by contrast, cannot tell the two apart.
    assert_close(
        brier_score(forecast, Outcome.DRAW),
        brier_score(forecast, Outcome.AWAY_WIN),
        label="Brier is indifferent to distance",
    )


def test_every_rule_is_proper() -> None:
    """Honesty must be optimal: reporting the truth minimises the expected score.

    This is the property that makes a metric safe to select models on.  If a
    rule were improper, the best strategy against it would be to lie, and any
    model tuned on it would learn to.
    """
    rng = random.Random(17)
    for _ in range(30):
        weights = [rng.random() + 0.05 for _ in range(3)]
        total = math.fsum(weights)
        truth = OutcomeProbabilities(*(w / total for w in weights))

        def expected(
            report: OutcomeProbabilities,
            rule: Callable[[OutcomeProbabilities, Outcome], float],
            truth: OutcomeProbabilities = truth,
        ) -> float:
            return math.fsum(
                truth[outcome] * rule(report, outcome) for outcome in Outcome
            )

        for rule in RULES:
            honest = expected(truth, rule)
            for _ in range(20):
                noise = [max(p + rng.gauss(0.0, 0.08), 1e-4) for p in truth.as_tuple()]
                lie = OutcomeProbabilities.normalised(*noise)
                assert expected(lie, rule) >= honest - 1e-12, rule.__name__


def test_scorecard_aggregates_and_compares() -> None:
    forecasts = [OutcomeProbabilities(0.8, 0.1, 0.1)] * 4
    outcomes = [Outcome.HOME_WIN] * 3 + [Outcome.AWAY_WIN]
    card = ScoreCard.evaluate(forecasts, outcomes, name="sharp")
    assert card.count == 4
    assert_close(card.accuracy, 0.75, label="accuracy")
    assert_close(card.rps, (3 * 0.025 + 0.725) / 4, tolerance=1e-12, label="mean RPS")

    flat = ScoreCard.evaluate(
        [OutcomeProbabilities(1 / 3, 1 / 3, 1 / 3)] * 4, outcomes, name="flat"
    )
    assert card.skill_against(flat) > 0.0
    assert_close(flat.skill_against(flat), 0.0, tolerance=1e-12, label="self skill")
    with assert_raises(ValueError):
        ScoreCard.evaluate([], [])
    with assert_raises(ValueError):
        ScoreCard.evaluate(forecasts, outcomes[:2])


def test_calibration_is_near_perfect_on_honestly_generated_data() -> None:
    """Sample outcomes from the forecasts themselves; the error must vanish."""
    rng = random.Random(23)
    forecasts = []
    outcomes = []
    for _ in range(20000):
        weights = [rng.random() + 0.05 for _ in range(3)]
        total = math.fsum(weights)
        forecast = OutcomeProbabilities(*(w / total for w in weights))
        draw = rng.random()
        cumulative = 0.0
        for outcome in Outcome:
            cumulative += forecast[outcome]
            if draw <= cumulative:
                outcomes.append(outcome)
                break
        else:  # pragma: no cover - float guard
            outcomes.append(Outcome.AWAY_WIN)
        forecasts.append(forecast)

    report = calibration_report(forecasts, outcomes, bins=10)
    assert report.count == 20000
    assert report.expected_calibration_error < 0.01, report
    assert math.fsum(b.count for b in report.bins) == 3 * 20000


def test_calibration_detects_systematic_overconfidence() -> None:
    """A model that always claims 90 percent but is right 60 percent is caught."""
    forecasts = [OutcomeProbabilities(0.9, 0.05, 0.05)] * 1000
    outcomes = [Outcome.HOME_WIN] * 600 + [Outcome.DRAW] * 400
    report = calibration_report(forecasts, outcomes, bins=10)
    assert report.expected_calibration_error > 0.1
    overconfident = next(b for b in report.bins if b.lower >= 0.8)
    assert overconfident.bias > 0.25
    with assert_raises(ValueError):
        calibration_report([], [])
    with assert_raises(ValueError):
        calibration_report(forecasts, outcomes, bins=0)


class _Spy:
    """A forecaster that records exactly what the backtester showed it."""

    def __init__(self) -> None:
        self.seen: list[tuple[Fixture, MatchLog]] = []

    @property
    def name(self) -> str:
        return "spy"

    def forecast(self, fixture: Fixture, history: MatchLog) -> OutcomeProbabilities:
        self.seen.append((fixture, history))
        return OutcomeProbabilities(1 / 3, 1 / 3, 1 / 3)


def test_the_backtester_never_shows_a_forecaster_the_future() -> None:
    """The property the whole evaluation rests on, asserted directly.

    Every match in the history handed to a forecaster must be strictly older
    than the fixture being predicted — not merely 'not the same match'.  Two
    fixtures played on the same day cannot inform each other either.
    """
    matches, _ = synthetic_league(seed=19, seasons=2)
    spy = _Spy()
    walk_forward(matches, [spy])
    assert len(spy.seen) == len(matches)
    for fixture, history in spy.seen:
        assert all(match.date < fixture.date for match in history)
        assert not any(
            match.home == fixture.home
            and match.away == fixture.away
            and match.date == fixture.date
            for match in history
        )


def test_the_backtester_scores_every_forecaster_on_an_identical_sample() -> None:
    matches, _ = synthetic_league(seed=29, seasons=2)
    forecasters: list[Forecaster] = [
        BaseRateForecaster(min_matches=10),
        EloForecaster("elo", min_matches=40),
        ModelForecaster(
            "dixon-coles", lambda: DixonColesModel(half_life_days=200),
            min_matches=200, refit_interval=40,
        ),
    ]
    result = walk_forward(matches, forecasters)
    assert result.evaluated + result.skipped == len(matches)
    counts = {card.count for card in result.scorecards.values()}
    assert len(counts) == 1, "forecasters were scored on different samples"
    assert result.evaluated == counts.pop()
    for record in result.records:
        assert set(record.forecasts) == {"base rate", "elo", "dixon-coles"}


def test_models_beat_the_base_rate_and_dixon_coles_beats_poisson() -> None:
    """The ordering any correct implementation must produce on clean data."""
    matches, _ = synthetic_league(seed=39, seasons=3)
    forecasters: list[Forecaster] = [
        BaseRateForecaster(),
        EloForecaster("elo"),
        ModelForecaster(
            "poisson", lambda: PoissonModel(half_life_days=400),
            min_matches=200, refit_interval=40,
        ),
        ModelForecaster(
            "dixon-coles", lambda: DixonColesModel(half_life_days=400),
            min_matches=200, refit_interval=40,
        ),
    ]
    result = walk_forward(matches, forecasters)
    cards = result.scorecards
    assert cards["dixon-coles"].rps < cards["base rate"].rps
    assert cards["elo"].rps < cards["base rate"].rps
    assert cards["dixon-coles"].rps <= cards["poisson"].rps + 1e-4
    # How much skill is attainable depends on how much true strength dispersion
    # the seed happened to generate, so the bar is a floor, not a target.
    assert cards["dixon-coles"].skill_against(cards["base rate"]) > 0.02
    assert result.ranked()[0].rps <= result.ranked()[-1].rps
    assert "walk-forward backtest" in result.summary(baseline="base rate")


def test_blending_pools_the_component_log_odds() -> None:
    """Geometric pooling averages log odds ratios, which is what makes it sharp.

    A linear blend of two agreeing, confident forecasts is less confident than
    either — the wrong behaviour when both sources point the same way.  Under
    geometric pooling every pairwise log odds ratio is a weighted average of
    the components', so the pool can be *sharper* than both.  That also means
    individual probabilities need not lie between the components', which is why
    the property is stated on odds ratios rather than on probabilities.
    """
    matches, _ = synthetic_league(seed=49, seasons=2)
    elo = EloForecaster("elo")
    base = BaseRateForecaster()
    blend = BlendForecaster("blend", [elo, base], [0.3, 0.7])
    history = matches.before(matches.dates()[30])
    fixture = matches[-1].fixture

    combined = blend.forecast(fixture, history)
    left = elo.forecast(fixture, history)
    right = base.forecast(fixture, history)
    assert combined is not None and left is not None and right is not None

    for i, j in ((0, 1), (0, 2), (1, 2)):
        pooled = math.log(combined.as_tuple()[i] / combined.as_tuple()[j])
        expected = 0.3 * math.log(left.as_tuple()[i] / left.as_tuple()[j]) + 0.7 * math.log(
            right.as_tuple()[i] / right.as_tuple()[j]
        )
        assert_close(pooled, expected, tolerance=1e-9, label=f"log odds {i}/{j}")

    # A blend with a single component reproduces it exactly.
    solo = BlendForecaster("solo", [elo]).forecast(fixture, history)
    assert solo is not None
    for got, want in zip(solo.as_tuple(), left.as_tuple(), strict=True):
        assert_close(got, want, tolerance=1e-9, label="single-component blend")

    with assert_raises(ValueError):
        BlendForecaster("bad", [elo], [1.0, 2.0])
    with assert_raises(ValueError):
        BlendForecaster("bad", [])
    with assert_raises(ValueError):
        BlendForecaster("bad", [elo], [-1.0])


def test_forecasters_decline_rather_than_guess() -> None:
    matches, _ = synthetic_league(seed=59)
    empty = MatchLog()
    assert BaseRateForecaster(min_matches=10).forecast(matches[0].fixture, empty) is None
    assert EloForecaster(min_matches=10).forecast(matches[0].fixture, empty) is None
    assert (
        ModelForecaster("m", DixonColesModel, min_matches=10).forecast(
            matches[0].fixture, empty
        )
        is None
    )
    unknown = Fixture("Nowhere United", matches.teams[0], matches.end)
    forecaster = ModelForecaster("m", DixonColesModel, min_matches=10, refit_interval=1)
    assert forecaster.forecast(unknown, matches) is None


def test_backtest_validates_its_configuration() -> None:
    matches, _ = synthetic_league(seed=69)
    with assert_raises(ValueError, match="at least one"):
        walk_forward(matches, [])
    with assert_raises(ValueError, match="unique"):
        walk_forward(matches, [BaseRateForecaster(), BaseRateForecaster()])
    with assert_raises(ValueError, match="no fixture"):
        walk_forward(matches, [BaseRateForecaster(min_matches=10**9)])


def test_a_book_cannot_find_value_against_its_own_prices() -> None:
    """Betting the de-vigged market back into the margined book must stake nothing.

    If this ever placed a bet, the de-vigging or the edge calculation would be
    inconsistent, and every reported edge elsewhere would be suspect.
    """
    matches, truth = synthetic_league(seed=79, seasons=2)
    book = synthetic_odds(matches, truth, seed=5, margin=0.05)
    result = walk_forward(matches, [MarketForecaster(book), BaseRateForecaster()])
    outcome = simulate_betting(result.records, book, "market", minimum_edge=0.0)
    assert outcome.bets == ()
    assert outcome.final_bankroll == outcome.starting_bankroll
    assert "no bets placed" in outcome.summary()


def test_fractional_kelly_reduces_the_drawdown() -> None:
    matches, truth = synthetic_league(seed=89, seasons=3)
    book = synthetic_odds(matches, truth, seed=6, margin=0.05, noise=0.12)
    result = walk_forward(
        matches,
        [
            MarketForecaster(book),
            ModelForecaster(
                "dixon-coles", lambda: DixonColesModel(half_life_days=400),
                min_matches=200, refit_interval=40,
            ),
        ],
    )
    full = simulate_betting(result.records, book, "dixon-coles", kelly_fraction=1.0)
    quarter = simulate_betting(result.records, book, "dixon-coles", kelly_fraction=0.25)
    assert full.bets, "the noisy book should offer the model some disagreements"
    assert len(full.bets) == len(quarter.bets)
    assert quarter.turnover < full.turnover
    assert quarter.max_drawdown < full.max_drawdown
    assert 0.0 <= quarter.hit_rate <= 1.0
    assert quarter.bankroll_curve[0] == quarter.starting_bankroll
    assert_close(
        quarter.final_bankroll, quarter.bankroll_curve[-1], tolerance=1e-12, label="curve end"
    )
    assert simulate_betting(
        result.records, book, "dixon-coles", kelly_fraction=0.0
    ).bets == ()


def test_betting_validates_its_configuration() -> None:
    matches, truth = synthetic_league(seed=99)
    book = synthetic_odds(matches, truth, seed=7)
    result = walk_forward(matches, [BaseRateForecaster(min_matches=10)])
    for kwargs in (
        {"starting_bankroll": 0.0},
        {"kelly_fraction": 1.5},
        {"minimum_edge": -0.1},
        {"maximum_stake": 0.0},
    ):
        with assert_raises(ValueError):
            simulate_betting(result.records, book, "base rate", **kwargs)  # type: ignore[arg-type]
    with assert_raises(KeyError):
        simulate_betting(result.records, book, "nobody")


def test_the_synthetic_book_always_quotes_a_real_price() -> None:
    """Loading a margin onto a near-certain favourite must not produce odds below 1.

    A dominant team can carry a true win probability above 1/(1 + margin); the
    naive proportional loading then implies a probability greater than one,
    which is not a price.  The book caps the favourite and pushes the remaining
    margin onto the other outcomes, exactly as a real one does.
    """
    for seed in range(12):
        matches, truth = synthetic_league(seed=seed)
        book = synthetic_odds(matches, truth, seed=seed + 100, margin=0.05)
        assert len(book) == len(matches)
        for odds in book.values():
            assert min(odds.as_tuple()) >= 1.01 - 1e-12
            assert_close(odds.overround, 0.05, tolerance=1e-9, label="margin preserved")

    loaded = _load_margin([0.999, 0.0005, 0.0005], margin=0.05, minimum_odds=1.01)
    assert_close(math.fsum(loaded), 1.05, tolerance=1e-12, label="book sum")
    assert_close(max(loaded), 1.0 / 1.01, tolerance=1e-12, label="favourite capped")
    assert all(0.0 < value < 1.0 for value in loaded)

    with assert_raises(ValueError, match="minimum_odds"):
        _load_margin([0.5, 0.3, 0.2], margin=0.05, minimum_odds=1.0)
    with assert_raises(ValueError, match="too high"):
        _load_margin([0.5, 0.5], margin=2.0, minimum_odds=1.01)
