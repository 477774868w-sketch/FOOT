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

import datetime as dt
from zoneinfo import ZoneInfo

from foot.analysis.dossier import FindingKind
from foot.analysis.engine import MatchAnalysis
from foot.analysis.request import resolve_timezone
from foot.analysis.rubrics import RubricImplementation, RubricStatus
from foot.markets.selection import DecisionStatus
from foot.provenance import utcnow

__all__ = ["render_card", "render_rubric_grid", "render_rubric_provenance"]

_WIDTH = 84

_REMEDIES: tuple[tuple[RubricImplementation, str], ...] = (
    (RubricImplementation.NOT_BUILT, "aucun adaptateur écrit à ce jour"),
    (
        RubricImplementation.BUILT_UNREACHABLE,
        "adaptateur écrit ; une clé ou un accès réseau l'activerait "
        "(« foot config » et « foot fournisseurs --couverture » disent lequel)",
    ),
    (
        RubricImplementation.OPERATOR_SUPPLIED,
        "à fournir par l'opérateur (import CSV)",
    ),
    (RubricImplementation.OPERATIONAL, "donnée attendue mais absente de la source"),
)
"""Each unavailability state with the action that would actually lift it."""


def _zone(name: str) -> ZoneInfo:
    """The run's timezone, so a quoting hour is shown where the operator lives."""
    return resolve_timezone(name)


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
            lines.append(
                "  pour trancher : reprenez le nom complet d'un candidat, ou "
                "retirez la date pour laisser le calendrier la fixer."
            )
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
    # A declaration refused for want of a publication hour must be named here,
    # beside the state it fails to update — otherwise the card asserts an
    # absence the operator's own file has already lifted.
    for finding in dossier.findings:
        if "non encore exploitables" in finding.statement:
            lines.append(f"  ⚠ {finding.statement}")
            lines.append(f"    → {finding.effect}")
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
        book = main.offer.bookmaker or "bookmaker non précisé"
        quoted = main.priced.quoted_at
        moment = (
            quoted.astimezone(_zone(resolved.timezone)).strftime("%d/%m %H:%M %Z")
            if quoted
            else "heure de relevé inconnue"
        )
        lines.append(
            f"  cote               {odds:.2f}  chez {book}  (relevée {moment})"
            if odds
            else "  cote               —"
        )
        floor = main.priced.profile.odds_for_expected_value(
            decision.criteria.min_expected_value
        )
        lines.append(
            f"  cote minimale      {floor:.2f} — en dessous, ce pari ne remplit "
            f"plus les critères déclarés"
        )
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
        # A price the engine refused is named with its reason. Dropping it into
        # the "no price supplied" pile would tell the operator their file was
        # empty when in fact it was rejected.
        for item in analysis.priced:
            if item.exclusion:
                lines.append(f"  {item.offer.label} : {item.exclusion}")
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
    lines.append(f"  recoupement : {_corroboration(analysis)}")
    if analysis.ledger.entries:
        lines.append("  sources décisives :")
        for entry in analysis.ledger.entries[:6]:
            lines.append(f"    {entry.render()}")
    return "\n".join(lines)


def _corroboration(analysis: MatchAnalysis) -> str:
    """Say how many decisive facts two independent providers actually agree on.

    Silence here would be read as agreement.  It is not: with one reachable
    provider there is nothing to cross-check, and the absence of a contradiction
    proves nothing at all.  Distinct *providers* are counted, never distinct
    URLs — one feed republished five times is one source.
    """
    ledger = analysis.ledger
    keys = ledger.keys()
    if not keys:
        return "aucun fait au registre"
    crossed = sum(1 for key in keys if ledger.independent_sources(key) > 1)
    alone = len(keys) - crossed
    if not crossed:
        return (
            f"aucun des {len(keys)} faits ne repose sur deux fournisseurs "
            f"indépendants — l'absence de contradiction n'est donc pas une "
            f"confirmation"
        )
    return (
        f"{crossed} fait(s) confirmé(s) par deux fournisseurs indépendants, "
        f"{alone} sur une source unique"
    )


def render_rubric_provenance(analysis: MatchAnalysis) -> str:
    """Per rubric: the datum used, **where it came from and how old it is**.

    The grid says whether a rubric is covered; this says on what. A rubric
    answered from a source retrieved three weeks ago is not in the same state as
    the same rubric answered from this morning's fetch, and only one of the two
    deserves to carry a decision.
    """
    if not analysis.rubrics:
        return "Aucune grille évaluée."
    ledger = analysis.ledger
    lines = [
        "PROVENANCE ET FRAÎCHEUR, RUBRIQUE PAR RUBRIQUE",
        "  état / donnée / source / relevé — « — » signifie : rien, et rien n'est supposé",
    ]
    for assessment in analysis.rubrics:
        mark = f"{assessment.status.symbol} {assessment.implementation.symbol}"
        head = f"  {mark} R{assessment.rubric.number:02d} {assessment.rubric.title}"
        lines.append(head)
        if assessment.blocker:
            lines.append(f"        indisponible — {assessment.blocker}")
            continue
        lines.append(f"        donnée   : {assessment.data_used or '—'}")
        entries = [e for key in assessment.evidence_keys for e in ledger.for_key(key)]
        if not entries:
            lines.append(
                "        source   : aucune preuve citée — rubrique traitée par le "
                "modèle lui-même, pas par une donnée externe"
            )
            continue
        for entry in entries[:3]:
            age = _freshness(entry.retrieved_at)
            lines.append(
                f"        source   : {entry.source} · relevé {age} · "
                f"[{entry.status.value}]"
            )
    return "\n".join(lines)


def _freshness(retrieved_at: dt.datetime) -> str:
    """How long ago, in words an operator can act on."""
    delta = utcnow() - retrieved_at
    hours = delta.total_seconds() / 3600.0
    if hours < 1.0:
        return f"il y a {int(delta.total_seconds() // 60)} min"
    if hours < 48.0:
        return f"il y a {hours:.0f} h"
    return f"il y a {delta.days} j ({retrieved_at:%d/%m %H:%M %Z})"


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
