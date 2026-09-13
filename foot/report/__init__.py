"""Reporting: match cards, the summary table and the web interface."""

from foot.report.card import render_card, render_rubric_grid
from foot.report.table import render_summary
from foot.report.web import build_page, serve

__all__ = ["build_page", "render_card", "render_rubric_grid", "render_summary", "serve"]
