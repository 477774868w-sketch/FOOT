# foot

A rigorous football (soccer) modelling engine in pure Python. **No dependencies.**

> **Système d'analyse de rencontres (français)** — collecte réelle, dossier
> sportif scellé avant lecture des cotes, grille des 22 rubriques, comparaison
> des marchés et une décision par rencontre :
> **[TELEPHONE.md](TELEPHONE.md)** pour le parcours depuis un téléphone, une
> page, **sans aucune commande quotidienne** ; **[COUTS.md](COUTS.md)** pour ce
> que coûterait la suite, avant tout engagement ; **[GUIDE.md](GUIDE.md)** pour l'utilisation complète ;
> **[AUDIT.md](AUDIT.md)** pour la correspondance avec le protocole, l'état
> vérifié des fournisseurs, et ce qui fonctionne / attend une clé / reste à
> développer.
>
> ```console
> $ python3 -m foot web --jeton --journal   # interface privée, analyses conservées
> $ python3 -m foot analyser "it.1 | Napoli - Bologna | 1.62 4.00 5.50"
> $ python3 -m foot suivre "…" --coup-denvoi 2026-09-13T20:45  # contrôle T−75/T−60
> $ python3 -m foot config       # quelles clés sont posées, et ce qu'elles coûtent
> ```

`foot` fits Dixon-Coles and Poisson goal models by maximum likelihood with
hand-derived analytic gradients, rates teams with Elo, reads the betting market,
sizes bets with the Kelly criterion, backtests without look-ahead leakage and
projects seasons by Monte Carlo — all on the standard library alone, including
the optimiser.

```console
$ python3 -m foot demo
```

---

## The one idea

A football forecast is **a joint distribution over scorelines**. Everything else
is a summation over it.

```python
from foot import DixonColesModel, Fixture, synthetic_league
import datetime as dt

matches, _ = synthetic_league(seed=7)
model = DixonColesModel(half_life_days=180).fit(matches)

fixture = Fixture("FC Golf", "FC Romeo", dt.date(2024, 5, 1))
matrix = model.score_matrix(fixture)

matrix.outcome_probabilities()      # H 0.841 / D 0.102 / A 0.057
matrix.totals(2.5).over             # 0.776
matrix.asian_handicap(-1.5).home    # 0.673
matrix.both_teams_to_score()        # 0.537
matrix.most_likely_score()          # (Score(3, 0), 0.098)
matrix.clean_sheet_probabilities()  # (0.443, 0.037)
```

One object, one fit, every market — consistent with each other by construction,
because they are all sums over the same grid.

---

## Installation

None required. Clone the repository and use it:

```console
$ git clone <this repo> && cd FOOT
$ python3 -m foot demo            # Python 3.10+, nothing else
```

Or install it properly:

```console
$ pip install -e .
$ foot demo
```

---

## What is in it

| Module | What it does |
|---|---|
| `foot.domain` | Immutable, validated `Match`, `Fixture`, `Score`, `MatchLog`, `OutcomeProbabilities`. Binary-search time slicing. |
| `foot.numerics` | L-BFGS with a strong-Wolfe line search, safeguarded bisection, numerically stable Poisson and log-sum-exp. Written from scratch. |
| `foot.models` | Dixon-Coles and the independent-Poisson baseline, fitted by maximum likelihood with exact analytic gradients. `ScoreMatrix` derives every market. |
| `foot.ratings` | Elo with two published margin-of-victory rules (plus none), season regression, and Davidson's tie model for the draw. |
| `foot.market` | Decimal/fractional/American odds, four margin-removal methods (multiplicative, additive, power, **Shin**), exact simultaneous Kelly staking. |
| `foot.evaluation` | RPS, Brier, log loss, ignorance; calibration reports; leak-free walk-forward backtesting; bankroll simulation with drawdowns. |
| `foot.simulation` | Monte Carlo projection of a season: title, top-*n* and relegation probabilities. |
| `foot.league` | League tables with configurable tiebreakers, head-to-head done properly. |
| `foot.data` | Reproducible synthetic leagues **with known ground truth**, plus football-data.co.uk CSV ingestion. |

---

## The mathematics, and why

### Dixon-Coles

For home team *i* against away team *j*:

```
log λ = h + attack_i − defence_j          (home goal rate)
log μ =     attack_j − defence_i          (away goal rate)
```

Goals are Poisson, coupled at low scores by the correction τ:

```
τ(0,0) = 1 − λμρ     τ(0,1) = 1 + λρ
τ(1,0) = 1 + μρ      τ(1,1) = 1 − ρ       τ = 1 elsewhere
```

Independent Poissons systematically understate 0-0 and 1-1 and overstate 1-0 and
0-1; τ reweights exactly those four cells. Its defining property is that it
**moves probability mass without creating any**: the four adjustments cancel
algebraically against their Poisson weights, so a corrected grid still sums to
one. That is asserted to 1e-15 in
`tests/test_models.py::test_dixon_coles_tau_conserves_total_probability_exactly`.

Match *m* enters the likelihood with weight `exp(−ξ · age)`, parameterised by an
interpretable **half-life in days**. The weighted log-likelihood is maximised by
the in-house L-BFGS using a gradient derived by hand, including the derivatives
of τ with respect to both rates and to ρ.

**Identifiability.** Adding a constant to every attack *and* every defence rating
leaves every goal rate unchanged, so the likelihood is exactly flat in that
direction. `foot` does not paper over this with a penalty: it fits unconstrained
— L-BFGS builds its search directions only from past steps and gradients, so it
provably never moves along a flat direction — then reports ratings in the
canonical gauge `mean(attack) == 0`, which is what makes two fits comparable.

### Shin's method, written so it does not lose precision

The textbook inversion for Shin's insider fraction is

```
p_i = (√(z² + 4(1−z)π_i²/B) − z) / (2(1−z))
```

in which numerator and denominator both vanish as *z* → 1. Multiplying by the
conjugate gives the algebraically identical

```
p_i = 2π_i² / (B · (√(z² + 4(1−z)π_i²/B) + z))
```

— a ratio of well-scaled quantities across the whole range.

**Where the root exists.** Bisection is guaranteed only on a book that carries a
margin, `B = Σ 1/odds ≥ 1`. There, at *z* = 0 the implied total is `√B ≥ 1`, and
as *z* → 1 it tends to `Σπ² / B`, which is strictly below 1 because every
`π_i < 1` forces `Σπ² < Σπ = B`; the total is continuous in between, so a root
exists. **When `B < 1` — an arbitrage book such as 3.50 / 3.50 / 3.50, where
`B = 0.857` — there is no such root**, because `√B < 1` already. The code does
not attempt one: `shin_insider_fraction` returns `z = 0` for `B ≤ 1`, which
reduces Shin to plain normalisation. An earlier edition of this README claimed
existence "for genuine decimal odds" without that condition; the claim was too
broad, the implementation was always correct, and
`test_market.py::test_shin_has_no_root_on_an_arbitrage_book` now pins the
behaviour.

### Simultaneous Kelly

Betting a football match is not three independent bets: money on the draw is
money unavailable if the home side wins. Maximising `Σ pᵢ log(1 − S + fᵢoᵢ)`
gives a closed form — the reserve `b = (1 − Σ_S pᵢ)/(1 − Σ_S 1/oᵢ)` and stakes
`fᵢ = pᵢ − b/oᵢ` over the set that beats the reserve. `foot` implements that
closed form **and checks it against direct numerical maximisation of expected log
growth** over hundreds of random markets, agreeing to below 1e-9.

### Elo, and where draws come from

Elo yields one expected score, not a three-way split. The classical answer is
Davidson's (1970) extension of Bradley-Terry:

```
P(home) = x / (x + 1 + ν√x)      x = 10^(rating difference / 400)
P(draw) = ν√x / (x + 1 + ν√x)
P(away) = 1 / (x + 1 + ν√x)
```

The win/loss odds ratio stays exactly *x*, so the tie parameter redistributes
probability without disturbing Elo's core claim — and ν can be estimated by
maximum likelihood from the same match log.

---

## Verifying it

The claims above are not asserted, they are tested. **304 tests (303 passing, 1 deliberately skipped), no dependencies** — including one that reads every import in the package to prove that second claim. The 8 that touch a remote host are separated behind `-m network`, so the deterministic suite runs, and fails, with no network at all.

```console
$ pytest -q -m "not network"      # 295 passed, 1 skipped
$ python3 tests/run_tests.py --sans-reseau   # if you have nothing at all
$ ruff check . && mypy .          # clean under strict settings
```

The tests worth reading first, because they are the ones that could actually
catch a wrong model:

| Claim | Test |
|---|---|
| The hand-derived gradient is correct | `test_dixon_coles.py::test_analytic_gradient_matches_finite_differences` — central differences at four points, including near the optimum, for the decayed, undecayed, ridge-penalised and ρ-free variants. |
| The likelihood is the right one | `test_dixon_coles.py::test_parameter_recovery_improves_with_more_data` — fit data generated from *known* parameters; the error must shrink as the sample grows (RMS attack error 0.139 -> 0.069 -> 0.030 over 2, 8 and 30 seasons). |
| The optimiser actually converges | `test_dixon_coles.py::test_poisson_fit_reproduces_the_observed_goal_totals_exactly` — an exact identity of the Poisson MLE: fitted goals must equal observed goals, per team, for and against. |
| τ conserves probability | `test_models.py::test_dixon_coles_tau_conserves_total_probability_exactly` |
| The backtest cannot see the future | `test_evaluation.py::test_the_backtester_never_shows_a_forecaster_the_future` — a spy forecaster records what it was shown; every match in its history must be **strictly older** than the fixture. |
| The metrics are proper | `test_evaluation.py::test_every_rule_is_proper` — for RPS, Brier and log loss, lying is never rewarded. |
| Kelly's closed form is right | `test_market.py::test_kelly_portfolio_matches_direct_numerical_optimisation` |
| De-vigging is self-consistent | `test_evaluation.py::test_a_book_cannot_find_value_against_its_own_prices` — betting the de-vigged market back into the margined book must stake **nothing**. |
| The simulator is sound | `test_simulation.py::test_expected_points_match_the_analytic_value_for_one_fixture` — with one match left the answer is `3P(win) + P(draw)`, computable by hand. |
| The optimiser is not a toy | `test_numerics.py::test_optimizer_solves_rosenbrock_in_many_dimensions` — 100-dimensional Rosenbrock to `f < 1e-10`. |
| Every documented example works | `test_doctests.py` runs every docstring example in the package. |

Every number in this README is reproducible from a seed.

---

## Command line

```console
$ foot demo                                   # end-to-end tour
$ foot ratings --seed 3                       # fitted attack/defence table
$ foot predict --home "FC Golf" --away "FC Romeo"
$ foot table                                  # league table
$ foot backtest --market                      # compare forecasters, then bet them
$ foot simulate --iterations 20000            # title and relegation probabilities
$ foot devig 2.10 3.40 3.60                   # all four margin models side by side
$ foot kelly --probabilities 0.5 0.3 0.2 --odds 2.5 3.2 4.0
```

Every command falls back to a reproducible synthetic league, so all of it works
with no data files. Point any of them at real data with `--csv results.csv`
(football-data.co.uk layout is auto-detected).

```console
$ foot devig 2.10 3.40 3.60

method               home     draw     away   fair odds
--------------------------------------------------------------
multiplicative     0.4543   0.2806   0.2650   2.201 / 3.563 / 3.773
additive           0.4602   0.2781   0.2617   2.173 / 3.596 / 3.820
power              0.4602   0.2780   0.2618   2.173 / 3.597 / 3.819
shin               0.4587   0.2787   0.2626   2.180 / 3.588 / 3.808
```

---

## Using it on real data

```python
from foot import (
    DixonColesModel, EloForecaster, ModelForecaster, BaseRateForecaster,
    MarketForecaster, load_matches, load_odds, walk_forward,
)

matches = load_matches("E0.csv")          # football-data.co.uk
book = load_odds("E0.csv", prefix="B365")

result = walk_forward(matches, [
    BaseRateForecaster(),
    EloForecaster("elo"),
    ModelForecaster("dixon-coles",
                    lambda: DixonColesModel(half_life_days=180),
                    min_matches=200, refit_interval=20),
    MarketForecaster(book),
])
print(result.summary(baseline="base rate"))
print(result.calibration("dixon-coles"))
```

Nothing in `foot` touches the network. A library that silently downloads data
cannot be reproduced, and reproducibility is the point.

---

## Honest limitations

- **This will not beat a bookmaker.** The synthetic book in the demo is
  deliberately noisy so the staking pipeline can be exercised end to end. Real
  closing prices are sharper than any of these models, and they move against
  you. The demo prints a warning to that effect; believe it.
- **Goals only.** No shots, no xG, no lineups, no injuries, no red cards, no
  fatigue, no motivation. Dixon-Coles is a strong baseline, not a ceiling.
- **The low-score correction helps the scoreline distribution more than the 1X2
  market.** On some samples Dixon-Coles and plain Poisson are indistinguishable
  on RPS while Dixon-Coles is clearly better on log loss. That is expected —
  collapsing to three outcomes discards most of what τ fixes — and `foot` reports
  it rather than hiding it.
- **Pure Python is pure Python.** A 380-match fit takes milliseconds and 10,000
  season simulations take a second or two, which is ample; a 100,000-match
  cross-validation sweep would want a compiled backend.
- **Elo's ratings and its draw model are separate choices.** Updates use the
  classical logistic expectation; the tie parameter only shapes the 1X2
  projection. That is documented rather than blurred.

---

## References

- Dixon, M.J. and Coles, S.G. (1997). *Modelling Association Football Scores and Inefficiencies in the Football Betting Market*. JRSS-C 46(2), 265-280.
- Maher, M.J. (1982). *Modelling association football scores*. Statistica Neerlandica 36(3), 109-118.
- Davidson, R.R. (1970). *On extending the Bradley-Terry model to accommodate ties*. JASA 65(329), 317-328.
- Shin, H.S. (1993). *Measuring the Incidence of Insider Trading in a Market for State-Contingent Claims*. Economic Journal 103, 1141-1153.
- Štrumbelj, E. (2014). *On determining probability forecasts from betting odds*. IJF 30(4), 934-943.
- Constantinou, A.C. and Fenton, N.E. (2012). *Solving the problem of inadequate scoring rules for assessing probabilistic football forecast models*. JQAS 8(1).
- Kelly, J.L. (1956). *A New Interpretation of Information Rate*. Bell System Technical Journal 35(4), 917-926.
- Smoczyński, P. and Tomkins, D. (2010). *An explicit solution to the problem of optimizing the allocations of a bettor's wealth when wagering on horse races*. Mathematical Scientist 35, 10-17.
- Nocedal, J. and Wright, S.J. (2006). *Numerical Optimization*, 2nd ed. Springer. (Algorithms 3.5, 3.6, 7.4.)

## Licence

MIT.
