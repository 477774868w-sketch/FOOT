"""Rating systems that summarise strength without a full likelihood."""

from foot.ratings.elo import EloRatingSystem, EloSnapshot, EloTable, MarginRule

__all__ = ["EloRatingSystem", "EloSnapshot", "EloTable", "MarginRule"]
