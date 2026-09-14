"""The Dixon-Coles model: the reference statistical model of football scores.

Dixon, M.J. and Coles, S.G. (1997), *Modelling Association Football Scores and
Inefficiencies in the Football Betting Market*, Journal of the Royal
Statistical Society Series C, 46(2), 265-280.

The model gives each team an attack and a defence rating and, for a match
between home team *i* and away team *j*,

    log lambda = h + attack_i - defence_j      (home goals rate)
    log mu     =     attack_j - defence_i      (away goals rate)

with goals drawn from a pair of Poisson distributions coupled at low scores by
the correction ``tau``.  Two refinements from the paper are implemented here in
full:

* **The low-score dependence correction.**  Independent Poissons understate
  0-0 and 1-1 and overstate 1-0 and 0-1.  ``tau`` reweights exactly those four
  cells.  It is constructed so that the total probability is *unchanged* — a
  property this module proves numerically in its test suite.
* **Exponential time decay.**  Recent matches say more about a team than old
  ones, so match *m* enters the likelihood with weight ``exp(-xi * age)``,
  parameterised here by an interpretable half-life in days.

The weighted log-likelihood is maximised with the in-house L-BFGS optimiser
using a hand-derived analytic gradient, which is roughly an order of magnitude
faster and far more accurate than finite differences.  The derivation is set
out in :func:`_match_terms`.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Sequence
from dataclasses import dataclass, field

from foot.domain import Fixture, MatchLog, OutcomeProbabilities
from foot.models.base import ScoreMatrix
from foot.numerics.optimize import OptimizeResult, minimize_with_gradient
from foot.numerics.stable import log_factorial

__all__ = [
    "DixonColesModel",
    "DixonColesParameters",
    "FitDiagnostics",
    "TeamRating",
    "dixon_coles_tau",
]

_LN2 = math.log(2.0)
_MIN_TAU = 1e-12


def dixon_coles_tau(
    home_goals: int, away_goals: int, home_rate: float, away_rate: float, rho: float
) -> float:
    """The Dixon-Coles low-score dependence factor.

    Only the four scorelines with both teams on 0 or 1 are affected::

        tau(0, 0) = 1 - lambda * mu * rho
        tau(0, 1) = 1 + lambda * rho
        tau(1, 0) = 1 + mu * rho
        tau(1, 1) = 1 - rho

    A negative ``rho`` — the empirically usual case — inflates 0-0 and 1-1 and
    deflates 1-0 and 0-1.  The four adjustments cancel exactly against the
    Poisson weights, so ``sum(tau * P) == sum(P)``.
    """
    if home_goals == 0:
        if away_goals == 0:
            return 1.0 - home_rate * away_rate * rho
        if away_goals == 1:
            return 1.0 + home_rate * rho
    elif home_goals == 1:
        if away_goals == 0:
            return 1.0 + away_rate * rho
        if away_goals == 1:
            return 1.0 - rho
    return 1.0


@dataclass(frozen=True, slots=True)
class TeamRating:
    """One team's fitted ratings, with their practical interpretation."""

    team: str
    attack: float
    defence: float
    strength: float
    expected_scored_vs_average: float
    """Goals scored relative to a league-average attack."""

    expected_conceded_vs_average: float
    """Goals conceded relative to a league-average defence."""


@dataclass(frozen=True, slots=True)
class FitDiagnostics:
    """Everything needed to judge whether a fit can be trusted."""

    log_likelihood: float
    weighted_log_likelihood: float
    matches: int
    effective_matches: float
    parameters: int
    converged: bool
    iterations: int
    evaluations: int
    gradient_norm: float
    message: str

    @property
    def aic(self) -> float:
        """Akaike information criterion, on the unweighted log-likelihood."""
        return 2.0 * self.parameters - 2.0 * self.log_likelihood

    @property
    def bic(self) -> float:
        """Bayesian information criterion."""
        if self.matches <= 0:
            return math.inf
        return self.parameters * math.log(self.matches) - 2.0 * self.log_likelihood

    def __str__(self) -> str:
        state = "converged" if self.converged else "NOT CONVERGED"
        return (
            f"{state} | logL = {self.log_likelihood:.3f} | AIC = {self.aic:.1f} | "
            f"{self.matches} matches ({self.effective_matches:.1f} effective) | "
            f"{self.parameters} parameters | |grad|inf = {self.gradient_norm:.2e}"
        )


@dataclass(frozen=True, slots=True)
class DixonColesParameters:
    """Fitted parameters — plain, inspectable, serialisable data.

    Ratings are stored in the canonical gauge ``mean(attack) == 0``.  The model
    is invariant under adding a constant to every attack *and* every defence
    rating, so fixing that constant is what makes ratings comparable between
    fits.
    """

    teams: tuple[str, ...]
    attack: tuple[float, ...]
    defence: tuple[float, ...]
    home_advantage: float
    rho: float
    reference_date: dt.date
    half_life_days: float | None
    diagnostics: FitDiagnostics
    _index: dict[str, int] = field(init=False, repr=False, compare=False, hash=False)

    def __post_init__(self) -> None:
        if not (len(self.teams) == len(self.attack) == len(self.defence)):
            raise ValueError("teams, attack and defence must have equal length")
        if len(set(self.teams)) != len(self.teams):
            raise ValueError("team names must be unique")
        object.__setattr__(self, "_index", {t: i for i, t in enumerate(self.teams)})

    def _lookup(self, team: str) -> int:
        try:
            return self._index[team]
        except KeyError:
            known = ", ".join(self.teams[:8]) + ("..." if len(self.teams) > 8 else "")
            raise KeyError(f"unknown team {team!r}; the model knows: {known}") from None

    def attack_of(self, team: str) -> float:
        """Attack rating: higher means more goals scored."""
        return self.attack[self._lookup(team)]

    def defence_of(self, team: str) -> float:
        """Defence rating: higher means fewer goals conceded."""
        return self.defence[self._lookup(team)]

    def rates(self, home: str, away: str, *, neutral: bool = False) -> tuple[float, float]:
        """Expected goals ``(home, away)`` for this pairing."""
        i, j = self._lookup(home), self._lookup(away)
        advantage = 0.0 if neutral else self.home_advantage
        return (
            math.exp(advantage + self.attack[i] - self.defence[j]),
            math.exp(self.attack[j] - self.defence[i]),
        )

    def strength(self, team: str) -> float:
        """A single scalar rating, ``attack + defence``, for ranking teams."""
        i = self._lookup(team)
        return self.attack[i] + self.defence[i]

    def ranking(self) -> list[tuple[str, float]]:
        """Teams ordered from strongest to weakest by :meth:`strength`."""
        return sorted(
            ((t, self.strength(t)) for t in self.teams), key=lambda kv: (-kv[1], kv[0])
        )

    def to_dict(self) -> dict[str, object]:
        """A JSON-ready representation of the fit."""
        return {
            "teams": list(self.teams),
            "attack": list(self.attack),
            "defence": list(self.defence),
            "home_advantage": self.home_advantage,
            "rho": self.rho,
            "reference_date": self.reference_date.isoformat(),
            "half_life_days": self.half_life_days,
            "log_likelihood": self.diagnostics.log_likelihood,
            "aic": self.diagnostics.aic,
        }


@dataclass(frozen=True, slots=True)
class _Design:
    """Pre-extracted, index-based view of the training data.

    Building this once keeps the inner loop free of dictionary lookups and
    attribute access, which matters when the objective is evaluated thousands
    of times.
    """

    n_teams: int
    home_index: tuple[int, ...]
    away_index: tuple[int, ...]
    home_goals: tuple[int, ...]
    away_goals: tuple[int, ...]
    weights: tuple[float, ...]
    home_flag: tuple[float, ...]
    log_factorials: tuple[float, ...]
    include_rho: bool
    ridge: float

    @property
    def size(self) -> int:
        return len(self.home_index)

    @property
    def n_parameters(self) -> int:
        return 1 + 2 * self.n_teams + (1 if self.include_rho else 0)


def _match_terms(  # noqa: PLR0911 - a flat dispatch over five cases reads best
    home_goals: int, away_goals: int, home_rate: float, away_rate: float, rho: float
) -> tuple[float, float, float, float]:
    """Per-match log-likelihood contribution and its derivatives.

    Returns ``(log_tau, d/dlog(lambda), d/dlog(mu), d/drho)`` of ``log tau``,
    or ``(-inf, 0, 0, 0)`` when the correction is infeasible.

    Derivation — with ``t`` denoting ``tau`` and only the four low-score cells
    contributing anything non-zero:

    ===========  ==================  =======================  ==============
    (x, y)       lambda dt/dlambda   mu dt/dmu                dt/drho
    ===========  ==================  =======================  ==============
    (0, 0)       -lambda*mu*rho      -lambda*mu*rho           -lambda*mu
    (0, 1)       +lambda*rho         0                        +lambda
    (1, 0)       0                   +mu*rho                  +mu
    (1, 1)       0                   0                        -1
    otherwise    0                   0                        0
    ===========  ==================  =======================  ==============

    Each entry is then divided by ``tau`` to differentiate ``log tau``.
    """
    if home_goals > 1 or away_goals > 1:
        return (0.0, 0.0, 0.0, 0.0)
    product = home_rate * away_rate
    if home_goals == 0 and away_goals == 0:
        tau = 1.0 - product * rho
        if tau <= _MIN_TAU:
            return (-math.inf, 0.0, 0.0, 0.0)
        term = -product * rho / tau
        return (math.log(tau), term, term, -product / tau)
    if home_goals == 0 and away_goals == 1:
        tau = 1.0 + home_rate * rho
        if tau <= _MIN_TAU:
            return (-math.inf, 0.0, 0.0, 0.0)
        return (math.log(tau), home_rate * rho / tau, 0.0, home_rate / tau)
    if home_goals == 1 and away_goals == 0:
        tau = 1.0 + away_rate * rho
        if tau <= _MIN_TAU:
            return (-math.inf, 0.0, 0.0, 0.0)
        return (math.log(tau), 0.0, away_rate * rho / tau, away_rate / tau)
    tau = 1.0 - rho  # (1, 1)
    if tau <= _MIN_TAU:
        return (-math.inf, 0.0, 0.0, 0.0)
    return (math.log(tau), 0.0, 0.0, -1.0 / tau)


def _objective(
    vector: Sequence[float], design: _Design
) -> tuple[float, list[float]]:
    """Weighted negative log-likelihood and its exact gradient."""
    n = design.n_teams
    home_advantage = vector[0]
    attack = vector[1 : 1 + n]
    defence = vector[1 + n : 1 + 2 * n]
    rho = vector[1 + 2 * n] if design.include_rho else 0.0

    grad_home = 0.0
    grad_attack = [0.0] * n
    grad_defence = [0.0] * n
    grad_rho = 0.0
    total = 0.0

    for m in range(design.size):
        i = design.home_index[m]
        j = design.away_index[m]
        flag = design.home_flag[m]
        log_home_rate = flag * home_advantage + attack[i] - defence[j]
        log_away_rate = attack[j] - defence[i]
        if log_home_rate > 30.0 or log_away_rate > 30.0:  # runaway step: reject it
            return (math.inf, [0.0] * len(vector))
        home_rate = math.exp(log_home_rate)
        away_rate = math.exp(log_away_rate)
        x = design.home_goals[m]
        y = design.away_goals[m]

        log_tau, d_log_home, d_log_away, d_rho = _match_terms(x, y, home_rate, away_rate, rho)
        if log_tau == -math.inf:
            return (math.inf, [0.0] * len(vector))

        weight = design.weights[m]
        total += weight * (
            log_tau
            + x * log_home_rate - home_rate - design.log_factorials[x]
            + y * log_away_rate - away_rate - design.log_factorials[y]
        )

        # d(log-likelihood)/d(log rate) for the Poisson part is (observed - rate).
        a = (x - home_rate) + d_log_home
        b = (y - away_rate) + d_log_away
        wa, wb = weight * a, weight * b
        grad_home += wa * flag
        grad_attack[i] += wa
        grad_defence[j] -= wa
        grad_attack[j] += wb
        grad_defence[i] -= wb
        grad_rho += weight * d_rho

    value = -total
    gradient = [-grad_home]
    gradient.extend(-g for g in grad_attack)
    gradient.extend(-g for g in grad_defence)
    if design.include_rho:
        gradient.append(-grad_rho)

    if design.ridge > 0.0:
        penalty = 0.0
        for k in range(n):
            penalty += attack[k] * attack[k] + defence[k] * defence[k]
            gradient[1 + k] += design.ridge * attack[k]
            gradient[1 + n + k] += design.ridge * defence[k]
        value += 0.5 * design.ridge * penalty

    return (value, gradient)


class DixonColesModel:
    """Estimator and forecaster for the Dixon-Coles model.

    Instances are immutable: :meth:`fit` returns a *new* model carrying the
    fitted parameters, so a configured-but-unfitted model can be reused safely
    across the folds of a backtest.

    >>> from foot.data.synthetic import synthetic_league
    >>> log, _ = synthetic_league(seed=7)
    >>> model = DixonColesModel(half_life_days=180).fit(log)
    >>> model.parameters.diagnostics.converged
    True
    """

    __slots__ = (
        "_low_score_correction",
        "_max_goals",
        "_parameters",
        "_ridge",
        "half_life_days",
    )

    def __init__(
        self,
        *,
        half_life_days: float | None = None,
        low_score_correction: bool = True,
        ridge: float = 0.0,
        max_goals: int | None = None,
        parameters: DixonColesParameters | None = None,
    ) -> None:
        """
        Args:
            half_life_days: weight of a match halves every this many days.
                ``None`` disables time decay.  Dixon and Coles found the
                optimal horizon to be a matter of months, not seasons.
            low_score_correction: enable ``tau``.  Disabling it reduces the
                model to independent Poisson.
            ridge: optional L2 penalty on ratings, useful for small samples.
            max_goals: scoreline grid size; chosen automatically when omitted.
            parameters: pre-fitted parameters, mainly for deserialisation.
        """
        if half_life_days is not None and half_life_days <= 0.0:
            raise ValueError(f"half_life_days must be positive, got {half_life_days!r}")
        if ridge < 0.0:
            raise ValueError(f"ridge must be non-negative, got {ridge!r}")
        self.half_life_days = half_life_days
        self._low_score_correction = low_score_correction
        self._ridge = ridge
        self._max_goals = max_goals
        self._parameters = parameters

    # -- State -------------------------------------------------------------
    @property
    def is_fitted(self) -> bool:
        return self._parameters is not None

    @property
    def parameters(self) -> DixonColesParameters:
        if self._parameters is None:
            raise RuntimeError("model is not fitted; call fit(matches) first")
        return self._parameters

    @property
    def teams(self) -> tuple[str, ...]:
        return self.parameters.teams

    def __repr__(self) -> str:
        state = (
            f"fitted on {self.parameters.diagnostics.matches} matches"
            if self.is_fitted
            else "unfitted"
        )
        return (
            f"DixonColesModel(half_life_days={self.half_life_days!r}, "
            f"low_score_correction={self._low_score_correction}, {state})"
        )

    # -- Fitting -----------------------------------------------------------
    def fit(self, matches: MatchLog, *, reference_date: dt.date | None = None) -> DixonColesModel:
        """Maximise the (time-weighted) log-likelihood on ``matches``.

        Args:
            matches: training data.  Must contain at least one match, and every
                team must appear at least once.
            reference_date: the "today" against which match ages are measured.
                Defaults to the date of the most recent training match, which
                is the only choice that cannot leak future information.
        """
        if len(matches) == 0:
            raise ValueError("cannot fit a model on an empty MatchLog")
        teams = matches.teams
        if len(teams) < 2:
            raise ValueError("need at least two distinct teams")

        anchor = reference_date if reference_date is not None else matches.end
        design = self._build_design(matches, teams, anchor)
        start = self._initial_vector(matches, teams, design)

        result = minimize_with_gradient(
            lambda v: _objective(v, design),
            start,
            memory=15,
            max_iterations=2000,
            gtol=1e-7,
            ftol=1e-14,
        )
        parameters = self._unpack(result, teams, design, anchor)
        # `type(self)` so that subclasses (PoissonModel) stay themselves.
        return type(self)(
            half_life_days=self.half_life_days,
            low_score_correction=self._low_score_correction,
            ridge=self._ridge,
            max_goals=self._max_goals,
            parameters=parameters,
        )

    def _build_design(
        self, matches: MatchLog, teams: Sequence[str], anchor: dt.date
    ) -> _Design:
        index = {team: i for i, team in enumerate(teams)}
        decay = _LN2 / self.half_life_days if self.half_life_days else 0.0
        home_index: list[int] = []
        away_index: list[int] = []
        home_goals: list[int] = []
        away_goals: list[int] = []
        weights: list[float] = []
        home_flag: list[float] = []
        for match in matches:
            home_index.append(index[match.home])
            away_index.append(index[match.away])
            home_goals.append(match.score.home)
            away_goals.append(match.score.away)
            home_flag.append(0.0 if match.neutral else 1.0)
            age = (anchor - match.date).days
            weights.append(math.exp(-decay * age) if decay else 1.0)
        biggest = max(max(home_goals, default=0), max(away_goals, default=0))
        return _Design(
            n_teams=len(teams),
            home_index=tuple(home_index),
            away_index=tuple(away_index),
            home_goals=tuple(home_goals),
            away_goals=tuple(away_goals),
            weights=tuple(weights),
            home_flag=tuple(home_flag),
            log_factorials=tuple(log_factorial(k) for k in range(biggest + 1)),
            include_rho=self._low_score_correction,
            ridge=self._ridge,
        )

    @staticmethod
    def _initial_vector(
        matches: MatchLog, teams: Sequence[str], design: _Design
    ) -> list[float]:
        """A method-of-moments starting point.

        Starting from per-team scoring and concession rates rather than from
        zeros typically halves the number of L-BFGS iterations, and keeps the
        first steps well inside the region where ``tau`` is feasible.
        """
        stats = matches.goal_stats()
        home_gpg = max(stats["home_goals_per_game"], 1e-3)
        away_gpg = max(stats["away_goals_per_game"], 1e-3)
        mean_gpg = 0.5 * (home_gpg + away_gpg)

        scored = dict.fromkeys(teams, 0.0)
        conceded = dict.fromkeys(teams, 0.0)
        played = dict.fromkeys(teams, 0.0)
        for match in matches:
            scored[match.home] += match.score.home
            conceded[match.home] += match.score.away
            scored[match.away] += match.score.away
            conceded[match.away] += match.score.home
            played[match.home] += 1.0
            played[match.away] += 1.0

        vector = [math.log(home_gpg) - math.log(away_gpg)]
        attack: list[float] = []
        defence: list[float] = []
        for team in teams:
            games = played[team]
            # One pseudo-match of league-average form keeps extremes finite.
            scored_rate = (scored[team] + mean_gpg) / (games + 1.0)
            conceded_rate = (conceded[team] + mean_gpg) / (games + 1.0)
            attack.append(math.log(max(scored_rate, 1e-3) / mean_gpg))
            defence.append(-math.log(away_gpg) - math.log(max(conceded_rate, 1e-3) / mean_gpg))
        vector.extend(attack)
        vector.extend(defence)
        if design.include_rho:
            vector.append(0.0)
        return vector

    def _unpack(
        self,
        result: OptimizeResult,
        teams: Sequence[str],
        design: _Design,
        anchor: dt.date,
    ) -> DixonColesParameters:
        n = len(teams)
        vector = list(result.x)
        attack = vector[1 : 1 + n]
        defence = vector[1 + n : 1 + 2 * n]
        rho = vector[1 + 2 * n] if design.include_rho else 0.0

        # Canonical gauge: shifting attack and defence by the same constant
        # leaves every rate untouched, so pin mean(attack) to zero.
        shift = math.fsum(attack) / n
        attack = [a - shift for a in attack]
        defence = [d - shift for d in defence]

        unweighted = _Design(
            n_teams=design.n_teams,
            home_index=design.home_index,
            away_index=design.away_index,
            home_goals=design.home_goals,
            away_goals=design.away_goals,
            weights=(1.0,) * design.size,
            home_flag=design.home_flag,
            log_factorials=design.log_factorials,
            include_rho=design.include_rho,
            ridge=0.0,
        )
        plain_ll = -_objective(result.x, unweighted)[0]

        # `result.fun` carries the ridge penalty when one is configured; the
        # reported log-likelihood must not, or AIC and model comparisons drift.
        weighted = _Design(
            n_teams=design.n_teams,
            home_index=design.home_index,
            away_index=design.away_index,
            home_goals=design.home_goals,
            away_goals=design.away_goals,
            weights=design.weights,
            home_flag=design.home_flag,
            log_factorials=design.log_factorials,
            include_rho=design.include_rho,
            ridge=0.0,
        )
        weighted_ll = -_objective(result.x, weighted)[0]

        diagnostics = FitDiagnostics(
            log_likelihood=plain_ll,
            weighted_log_likelihood=weighted_ll,
            matches=design.size,
            effective_matches=math.fsum(design.weights),
            parameters=design.n_parameters,
            converged=result.converged,
            iterations=result.iterations,
            evaluations=result.evaluations,
            gradient_norm=result.gradient_norm,
            message=result.message,
        )
        return DixonColesParameters(
            teams=tuple(teams),
            attack=tuple(attack),
            defence=tuple(defence),
            home_advantage=vector[0],
            rho=rho,
            reference_date=anchor,
            half_life_days=self.half_life_days,
            diagnostics=diagnostics,
        )

    # -- Prediction --------------------------------------------------------
    def rates(self, fixture: Fixture) -> tuple[float, float]:
        """Expected goals for ``fixture`` as ``(home, away)``."""
        return self.parameters.rates(fixture.home, fixture.away, neutral=fixture.neutral)

    def score_matrix(self, fixture: Fixture) -> ScoreMatrix:
        """The joint scoreline distribution for ``fixture``."""
        params = self.parameters
        home_rate, away_rate = self.rates(fixture)
        rho = params.rho
        if rho == 0.0:
            return ScoreMatrix.from_rates(home_rate, away_rate, max_goals=self._max_goals)

        def correction(h: int, a: int, lam: float, mu: float) -> float:
            # Clamped at zero for safety: unseen pairings could in principle
            # push tau negative even though the fit keeps it feasible on the
            # training data.  The grid is renormalised afterwards regardless.
            return max(dixon_coles_tau(h, a, lam, mu, rho), 0.0)

        return ScoreMatrix.from_rates(
            home_rate, away_rate, max_goals=self._max_goals, correction=correction
        )

    def predict(self, fixture: Fixture) -> OutcomeProbabilities:
        """1X2 probabilities for ``fixture``."""
        return self.score_matrix(fixture).outcome_probabilities()

    def ratings_table(self) -> list[TeamRating]:
        """Team ratings with their implied goal rates, strongest first."""
        params = self.parameters
        return [
            TeamRating(
                team=team,
                attack=params.attack_of(team),
                defence=params.defence_of(team),
                strength=strength,
                expected_scored_vs_average=math.exp(params.attack_of(team)),
                expected_conceded_vs_average=math.exp(-params.defence_of(team)),
            )
            for team, strength in params.ranking()
        ]
