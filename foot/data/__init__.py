"""Data sources: reproducible synthetic leagues and CSV ingestion."""

from foot.data.csv_source import load_matches, load_odds, write_matches
from foot.data.synthetic import (
    LeagueTruth,
    round_robin_schedule,
    synthetic_league,
    synthetic_odds,
)

__all__ = [
    "LeagueTruth",
    "load_matches",
    "load_odds",
    "round_robin_schedule",
    "synthetic_league",
    "synthetic_odds",
    "write_matches",
]
