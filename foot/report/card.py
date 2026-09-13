"""The per-match report card, in French.

Short at the top, consultable underneath.  The order follows what a decision
actually needs: what the match is, what the sport says, what is being backed,
at what price, why that market and not another, how confident the *foundation*
is, what would make it wrong, and what is still outstanding.

Two separations are held throughout, because blurring either is how a report
becomes misleading: **facts are not model output and neither is interpretation**,
and **confidence is not probability**.
"""

from __future__ import annotations

from foot.analysis.dossier import FindingKind
from foot.analysis.engine import MatchAnalysis
from foot.analysis.rubrics import RubricImplementation, RubricStatus
from foot.markets.selection import DecisionStatus

__all__ = ["render_card"]

_WIDTH = 84

_REMEDIES: tuple[tuple[RubricImplementation, str], ...] = (
    (RubricImplementation.NOT_BUILT, "aucun adaptateur écrit à ce jour"),
    (
        RubricImplementation.BUILT_UNREACHABLE,
        "adaptateur écrit, accès réseau à ouvrir",
    ),
    (
        RubricImplementation.OPERATOR_SUPPLIED,
        "à fournir par l'opérateur (import CSV)",
    ),
    (RubricImplementation.OPERATIONAL, "donnée attendue mais absente de la source"),
)
"""Each unavailability state with the action that would actually lift it."""


def _rule(char: str = "─") -> str:
    return char * _WIDTH


def _section(title: str) -> str:
    return f"\n{title}\n{_rule('·')}"


def render_card(analysis: MatchAnalysis, *, detailed: bool = True) -> str:
    """Render one match as a readable card."""
    resolved = analysis.resolved
    lines: list[str] = [_rule("═")]

    if resolved.fixture is not None:
        lines.append(f"{resolved.fixture.home}  –  {resolved.fixture.away}")
        lines.append(
            f"{resolved.competition_label} · {resolved.kickoff_local()} "
            f"· statut : {resolved.kickoff_status.value}"
        )
    else:
        lines.append(f"Ligne {resolved.request.line_number} : {resolved.request.raw.strip()}")
    lines.append(_rule("═"))

    # -- Not analysable: say exactly what is missing -----------------------
    if not analysis.analysed:
        lines.append("")
        lines.append("STATUT : analyse non produite")
        reason = analysis.blocked_reason or resolved.explain()
        lines.append(f"  {reason}")
        if resolved.candidates:
            lines.append(f"  candidats possibles : {', '.join(resolved.candidates)}")
        lines.append("")
        lines.append("  Cette rencontre reste au récapitulatif : rien n'est abandonné.")
        return "\n".join(lines)

    assert analysis.sealed is not None
    dossier = analysis.sealed.dossier
    decision = analysis.decision

    # -- 1. Sporting read ---------------------------------------------------
    home_rate, away_rate = dossier.expected_goals
    score, score_p = dossier.score_matrix.most_likely_score()
    lines.append(_section("1 · LECTURE SPORTIVE"))
    lines.append(
        f"  buts attendus      {home_rate:.2f} – {away_rate:.2f}   "
        f"(modèle {dossier.model_version})"
    )
    lines.append(f"  1X2 du modèle      {dossier.probabilities}")
    lines.append(
        f"  score central      {score}  ({score_p * 100:.1f}%) — "
        f"indicatif, jamais converti d'office en pari"
    )
    lines.append(f"  historique         {dossier.history_size} matchs ({dossier.history_span})")
    for finding in dossier.findings:
        if finding.kind is FindingKind.FACT and finding.rubric == 4:
            lines.append(f"  forme              {finding.statement}")

    # -- 2. Squad and context ----------------------------------------------
    lines.append(_section("2 · EFFECTIF ET CONTEXTE"))
    unavailable = [a for a in analysis.rubrics if a.status is RubricStatus.UNAVAILABLE]
    rest = [f for f in dossier.findings if f.rubric == 13]
    for finding in rest:
        lines.append(f"  {finding.statement}")
    if unavailable:
        # Grouped by *why*, because the three states call for three different
        # actions: wait for development, open a network route, supply a file.
        by_state: dict[RubricImplementation, list[str]] = {}
        for assessment in unavailable:
            by_state.setdefault(assessment.implementation, []).append(
                f"R{assessment.rubric.number:02d} {assessment.rubric.title}"
            )
        lines.append(f"  {len(unavailable)} rubriques non renseignées :")
        for state, remedy in _REMEDIES:
            titles = by_state.get(state)
            if not titles:
                continue
            lines.append(f"    {state.value} — {remedy} :")
            for title in titles[:6]:
                lines.append(f"      ✗ {title}")
        lines.append(
            "    → aucune valeur n'a été substituée ; l'incertitude du dossier "
            "en tient compte."
        )

    # -- 3. The bet ---------------------------------------------------------
    lines.append(_section("3 · DÉCISION"))
    if decision is None:
        lines.append("  aucune décision produite")
    elif decision.status is DecisionStatus.RECOMMENDED and decision.main is not None:
        main = decision.main
        odds = main.offer.odds
        lines.append(f"  PARI PRINCIPAL     {main.offer.label}")
        lines.append(f"  cote               {odds:.2f}" if odds else "  cote               —")
        lines.append(
            f"  probabilité        {main.priced.win_probability * 100:.1f}%"
            + (
                f"   (remboursement possible {main.priced.profile.push_probability * 100:.1f}%)"
                if main.priced.profile.push_probability > 1e-9
                else ""
            )
        )
        lines.append(f"  règlement          {main.priced.profile.render()}")
        lines.append(f"  cote équitable     {main.priced.fair_odds:.2f}")
        value = main.expected_value
        if value is not None:
            lines.append(f"  espérance          {value * 100:+.2f}% par unité misée")
        lines.append(f"  CONFIANCE          {decision.confidence.value}")
        lines.append("                     (fondement du dossier, pas probabilité de gain)")
    elif decision.status is DecisionStatus.PRICE_CONDITION:
        lines.append("  PAS DE PARI — cote non fournie")
        lines.append(f"  angle sportif      {decision.rationale}")
        if decision.price_condition:
            lines.append(f"  condition de prix  {decision.price_condition}")
    else:
        lines.append(f"  {decision.status.value.upper()}")
        lines.append(f"  raison             {decision.reason}")
        if decision.rationale:
            lines.append(f"  angle sportif      {decision.rationale}")

    # -- 4. Why this market -------------------------------------------------
    if decision is not None:
        lines.append(_section("4 · MARCHÉS COTÉS COMPARÉS"))
        if decision.compared:
            lines.append(
                f"  {len(decision.compared)} marché(s) portant un prix ont été mis "
                f"en concurrence :"
            )
            for label in decision.compared:
                lines.append(f"    · {label}")
            unpriced = len(analysis.priced) - len(decision.compared)
            if unpriced > 0:
                lines.append(
                    f"  {unpriced} autre(s) offre(s) du catalogue n'ont pas de cote "
                    f"fournie : elles ne peuvent pas être comparées."
                )
        else:
            lines.append("  aucun marché coté : aucune comparaison de prix possible")
        for refused in analysis.resolved.request.refused_markets:
            lines.append(
                f"  {refused} : cote reçue mais écartée — ce marché ne se déduit pas "
                f"de la loi des scores finaux ; un modèle dédié est requis et n'est "
                f"pas fourni ici. Aucune estimation n'a été produite."
            )
        if decision.main is not None:
            lines.append(f"  {decision.rationale}")
        lines.append(f"  {decision.criteria.describe()}")
        if decision.main is not None and decision.alternatives:
            lines.append("  replis (chacun répond à un changement précis) :")
            for trigger, alternative in decision.alternatives:
                lines.append(f"    · {trigger} → {alternative}")
        if decision.main is not None and decision.rejected:
            lines.append("  marchés écartés :")
            for rejected in decision.rejected[:4]:
                lines.append(f"    ✗ {rejected.offer.label} — {rejected.rejection}")

    # -- 5. Risk and cancellation ------------------------------------------
    lines.append(_section("5 · RISQUE, SENSIBILITÉ ET CONTRE-ANALYSE"))
    if analysis.scenarios:
        kinds: dict[str, list[str]] = {}
        for scenario in analysis.scenarios:
            kinds.setdefault(scenario.kind.value, []).append(scenario.name)
        for kind, names in kinds.items():
            lines.append(f"  {kind} : {', '.join(names)}")
        basis = {s.kind.value: s.basis for s in analysis.scenarios if s.basis}
        for kind, why in basis.items():
            lines.append(f"    · {kind} — {why}")
        if not any(
            s.kind.value == "événement sportif documenté" for s in analysis.scenarios
        ):
            lines.append(
                "  aucun événement sportif documenté n'a pu être intégré : les "
                "sources de compositions et d'absences sont inaccessibles. Les "
                "variations ci-dessus mesurent une sensibilité, elles ne "
                "constituent pas un pire cas sportif établi."
            )
    if decision is not None and decision.main_risk:
        lines.append(f"  risque principal   {decision.main_risk}")
    if decision is not None and decision.cancellation:
        lines.append(f"  condition d'annul. {decision.cancellation}")
    for note in dossier.counter_analysis:
        lines.append(f"  · {note}")

    # -- 6. Lineups ---------------------------------------------------------
    if analysis.lineup_plan is not None:
        lines.append(_section("6 · COMPOSITIONS"))
        lines.extend(f"  {line}" for line in analysis.lineup_plan.render().splitlines()[1:])

    if not detailed:
        return "\n".join(lines)

    # -- 7. Traceability ----------------------------------------------------
    lines.append(_section("7 · DE LA SOURCE À LA PRÉVISION"))
    used = dossier.model_findings()
    shown = dossier.display_only_findings()
    lines.append(f"  {len(used)} information(s) utilisée(s) dans la prévision :")
    for finding in used:
        for part in finding.render().splitlines():
            lines.append(f"    {part}")
    if shown:
        lines.append(f"  {len(shown)} information(s) affichée(s) mais NON utilisées :")
        for finding in shown:
            for part in finding.render().splitlines():
                lines.append(f"    {part}")

    # -- 8. Seal, diagnostics, limits --------------------------------------
    lines.append(_section("8 · SCELLEMENT, CONTRÔLES ET LIMITES"))
    lines.extend(f"  {line}" for line in analysis.sealed.render().splitlines())
    if analysis.exposure is not None:
        lines.append(f"  séparation sport/cotes : {analysis.exposure.verdict()}")
    if analysis.diagnostics is not None:
        warnings = analysis.diagnostics.warnings()
        if warnings:
            for warning in warnings:
                lines.append(f"  ⚠ {warning}")
        else:
            lines.append(
                f"  grille des scores saine (masse tronquée "
                f"{analysis.diagnostics.truncated_mass:.1e}, "
                f"τ_min {analysis.diagnostics.tau_floor:.3f})"
                if analysis.diagnostics.tau_floor is not None
                else "  grille des scores saine"
            )
    lines.append(f"  couverture des rubriques : {analysis.rubric_coverage * 100:.0f}%")
    for assumption in dossier.assumptions:
        lines.append(f"  hypothèse : {assumption}")
    contradictions = analysis.ledger.contradictions()
    if contradictions:
        lines.append(f"  ⚠ contradictions non résolues : {', '.join(sorted(contradictions))}")
    if analysis.ledger.entries:
        lines.append("  sources décisives :")
        for entry in analysis.ledger.entries[:6]:
            lines.append(f"    {entry.render()}")
    return "\n".join(lines)


def render_rubric_grid(analysis: MatchAnalysis) -> str:
    """The 22-rubric grid for one match."""
    if not analysis.rubrics:
        return "grille non évaluée"
    lines = [f"Grille des 22 rubriques — {analysis.resolved.request.label()}", _rule("·")]
    lines.extend(a.render() for a in analysis.rubrics)
    counts: dict[RubricStatus, int] = {}
    for assessment in analysis.rubrics:
        counts[assessment.status] = counts.get(assessment.status, 0) + 1
    lines.append(_rule("·"))
    lines.append(
        "  ".join(
            f"{s.symbol} {s.value} : {n}"
            for s, n in sorted(counts.items(), key=lambda kv: kv[0].value)
        )
    )
    return "\n".join(lines)
