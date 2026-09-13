"""The summary table: every requested match, its pick, confidence and risk.

The operator's rule is absolute — the table carries **all** the matches asked
for, including the ones that could not be identified.  A match missing from the
table is indistinguishable from a match that was quietly abandoned, so the
census line at the bottom reconciles the count against what was typed.
"""

from __future__ import annotations

from foot.analysis.engine import AnalysisRun
from foot.markets.selection import DecisionStatus

__all__ = ["render_summary"]

_COLUMNS = (
    ("#", 3),
    ("Rencontre", 34),
    ("Choix principal", 30),
    ("Cote", 6),
    ("Prob", 6),
    ("EV", 8),
    ("Conf", 5),
    ("Décision", 22),
)


def _row(values: list[str]) -> str:
    return "  ".join(
        value[:width].ljust(width) for value, (_, width) in zip(values, _COLUMNS, strict=True)
    )


def render_summary(run: AnalysisRun) -> str:
    """Render the final table for a whole run."""
    header = _row([name for name, _ in _COLUMNS])
    lines = [
        f"RÉCAPITULATIF — {run.requested} rencontre(s) demandée(s)",
        f"as_of {run.as_of.astimezone().strftime('%Y-%m-%d %H:%M %Z')} "
        f"· fuseau {run.timezone} · critères {run.criteria.version}",
        "",
        header,
        "─" * len(header),
    ]

    for analysis in run.analyses:
        resolved = analysis.resolved
        number = str(resolved.request.line_number)
        label = (
            f"{resolved.fixture.home[:15]} – {resolved.fixture.away[:15]}"
            if resolved.fixture
            else resolved.request.label()
        )
        pick = odds = probability = value = confidence = "—"
        verdict = resolved.status.value

        decision = analysis.decision
        if analysis.analysed and decision is not None:
            verdict = decision.status.value
            confidence = decision.confidence.letter
            if decision.main is not None:
                main = decision.main
                pick = main.offer.label
                odds = f"{main.offer.odds:.2f}" if main.offer.odds else "—"
                probability = f"{main.priced.win_probability * 100:.0f}%"
                expected = main.expected_value
                value = f"{expected * 100:+.1f}%" if expected is not None else "—"
            elif decision.status is DecisionStatus.PRICE_CONDITION:
                pick = "angle sportif, cote requise"
        elif analysis.blocked_reason:
            verdict = "hors prématch" if not resolved.bettable else "bloqué"

        lines.append(_row([number, label, pick, odds, probability, value, confidence, verdict]))

    lines.append("─" * len(header))
    lines.append(
        f"Total : {run.requested} demandée(s) · {len(run.analysed())} analysée(s) · "
        f"{len(run.recommendations())} recommandation(s) · "
        f"{len(run.unresolved())} à préciser · "
        f"{len(run.started_matches())} hors prématch"
    )
    if run.requested != len(run.analyses):  # pragma: no cover - invariant
        lines.append("⚠ INCOHÉRENCE : des rencontres manquent au récapitulatif")
    else:
        lines.append("Contrôle : chaque ligne saisie apparaît bien ci-dessus.")

    missing = run.registry_report.missing_capabilities()
    if missing:
        lines.append("")
        lines.append(
            "Capacités sans fournisseur accessible : "
            + ", ".join(c.value for c in missing)
        )
    return "\n".join(lines)
