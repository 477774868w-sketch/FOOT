"""Le parcours complet, en un seul endroit, pour les deux surfaces.

Le terminal et le navigateur posent la même question et doivent recevoir la même
réponse.  La version auditée le *documentait* sans le faire : `analyse_form`
transmettait le fuseau choisi au chargeur de contexte, `command_analyser` ne le
transmettait pas.  Une absence publiée à 13 h UTC était donc exclue au navigateur
et intégrée en terminal, pour la même analyse datée de 12 h UTC.

Ce module est la réponse structurelle : **une seule fonction** lit les entrées,
horodate le contexte et appelle le moteur. Les deux surfaces l'appellent ; il n'y
a plus rien à garder synchronisé, donc plus rien qui puisse diverger.

Ce qu'elle garantit, et que des tests exercent depuis les deux côtés :

* les horodatages de publication sont lus **dans le fuseau de l'analyse**, jamais
  dans un fuseau par défaut ;
* les lignes refusées remontent avec leur motif, en terminal comme au
  navigateur ;
* ce qui a réellement été retenu est énuméré, pour que l'opérateur voie sur quoi
  la recommandation repose.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from foot.analysis.engine import AnalysisRun, Engine
from foot.analysis.request import DEFAULT_TIMEZONE, resolve_timezone
from foot.collect.supplements import (
    SupplementSet,
    load_supplements,
    supplements_from_text,
)

__all__ = ["JourneyResult", "load_context", "run_journey"]


@dataclass(frozen=True, slots=True)
class JourneyResult:
    """One analysis, with what was refused and what was actually used."""

    run: AnalysisRun
    rejected: tuple[str, ...] = ()
    """Lines the loaders refused, each with its reason."""

    used: tuple[str, ...] = ()
    """What the context imports actually **fed the analysis**, counted.

    Counts the lines the engine could use at ``as_of``, not the lines read from
    the file: reporting "2 absences" when one of them was published after the
    analysis overstates what the recommendation rests on.
    """

    notes: tuple[str, ...] = ()
    """Lines read but not usable for this analysis, each with its cause.

    These live here rather than in the sealed dossier: they describe the import,
    not the match.
    """

    supplements: SupplementSet = field(default_factory=SupplementSet)
    """The context as loaded — before the engine's availability cut."""

    def render_context(self) -> str:
        """A short French paragraph naming what was kept and what was not."""
        lines: list[str] = []
        if self.used:
            lines.append(
                "Contexte retenu : "
                + ", ".join(self.used)
                + ". Ce qui n'apparaît pas ici n'a pas servi à l'analyse."
            )
        if self.notes:
            lines.append(
                f"Lignes lues mais écartées de cette analyse ({len(self.notes)}) — "
                f"le dossier sportif n'en dépend pas :"
            )
            lines.extend(f"  ⚠ {note}" for note in self.notes)
        if self.rejected:
            lines.append(
                f"Lignes non retenues ({len(self.rejected)}) — corrigez-les et "
                f"relancez ; rien n'a été deviné à leur place :"
            )
            lines.extend(f"  ✗ {reason}" for reason in self.rejected)
        return "\n".join(lines)


def load_context(
    *,
    timezone: str = DEFAULT_TIMEZONE,
    pasted: Mapping[str, str] | None = None,
    files: Mapping[str, str | Path | None] | None = None,
    source: str = "import opérateur",
) -> SupplementSet:
    """Read the operator's context, from pasted text or from files.

    Both routes go through the same parsers **with the same timezone**, which is
    the whole point: a publication hour must mean the same thing whichever way
    the operator supplied it.
    """
    tzinfo = resolve_timezone(timezone)
    paths = {key: value for key, value in (files or {}).items() if value}
    if paths:
        return load_supplements(
            xg_csv=paths.get("xg"),
            absences_csv=paths.get("absences"),
            lineups_csv=paths.get("compositions"),
            source=source,
            tzinfo=tzinfo,
        )
    blocks = dict(pasted or {})
    if not any(text.strip() for text in blocks.values()):
        return SupplementSet()
    return supplements_from_text(
        xg=blocks.get("xg", ""),
        absences=blocks.get("absences", ""),
        lineups=blocks.get("compositions", ""),
        source=source,
        tzinfo=tzinfo,
    )


def run_journey(
    engine: Engine,
    *,
    matches: str,
    as_of: dt.datetime,
    timezone: str = DEFAULT_TIMEZONE,
    bookmaker: str | None = None,
    quoted_at: dt.datetime | None = None,
    pasted: Mapping[str, str] | None = None,
    files: Mapping[str, str | Path | None] | None = None,
    supplements: SupplementSet | None = None,
    watching: bool = False,
) -> JourneyResult:
    """Run one request end to end — the single path both surfaces take.

    ``quoted_at`` dates only the prices **typed on the match line**; a price a
    provider imported keeps the hour it was actually observed.
    """
    context = supplements
    if context is None:
        context = load_context(timezone=timezone, pasted=pasted, files=files)
    run = engine.run(
        matches,
        as_of=as_of,
        timezone=timezone,
        bookmaker=bookmaker,
        quoted_at=quoted_at if quoted_at is not None else as_of,
        supplements=context,
        watching=watching,
    )
    # Count what the engine could actually see at `as_of`, not what the file
    # contained: a line published after the analysis is read, reported, and not
    # used — saying otherwise inflates what the recommendation rests on.
    usable = context.available_at(as_of)
    used: list[str] = []
    if usable.xg:
        used.append(f"{len(usable.xg)} ligne(s) xG")
    if usable.absences:
        used.append(f"{len(usable.absences)} absence(s)")
    if usable.lineups:
        used.append(f"{len(usable.lineups)} ligne(s) de composition")
    notes = tuple(
        dict.fromkeys(
            note for analysis in run.analyses for note in analysis.import_notes
        )
    )
    return JourneyResult(
        run=run,
        rejected=context.rejected,
        used=tuple(used),
        notes=notes,
        supplements=context,
    )
