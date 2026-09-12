"""Reading and exploiting the betting market."""

from foot.market.devig import (
    DevigMethod,
    fair_probabilities,
    remove_margin,
    shin_insider_fraction,
)
from foot.market.kelly import (
    KellyAllocation,
    expected_log_growth,
    expected_value,
    kelly_fraction,
    kelly_portfolio,
)
from foot.market.odds import (
    MatchOdds,
    american_to_decimal,
    decimal_to_american,
    decimal_to_fractional,
    fractional_to_decimal,
    implied_probability,
)

__all__ = [
    "DevigMethod",
    "KellyAllocation",
    "MatchOdds",
    "american_to_decimal",
    "decimal_to_american",
    "decimal_to_fractional",
    "expected_log_growth",
    "expected_value",
    "fair_probabilities",
    "fractional_to_decimal",
    "implied_probability",
    "kelly_fraction",
    "kelly_portfolio",
    "remove_margin",
    "shin_insider_fraction",
]
