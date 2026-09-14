"""Le suivi qui continue quand l'onglet se ferme.

Un contrôle T−75/T−60 lancé depuis un téléphone ne peut pas dépendre de l'onglet
qui l'a demandé : l'écran s'éteint, le navigateur met la page en veille, et le
contrôle n'a pas lieu — précisément à l'heure où il compte.

Ce module tient donc les suivis **côté serveur**, dans des fils de travail qui
survivent à la requête qui les a créés :

* ``start`` accepte une rencontre et son coup d'envoi, et rend un identifiant ;
* ``status`` rend l'état courant — tentatives faites, dernière vérification
  réussie, décision — que la page consulte au chargement, sans rien piloter ;
* ``stop`` interrompt un suivi.

Rien n'est promis de plus : si le processus serveur s'arrête, les suivis
s'arrêtent avec lui, et :meth:`Supervisor.status` le dira plutôt que de laisser
croire qu'un contrôle a eu lieu. C'est une différence que l'opérateur doit
pouvoir lire.
"""

from __future__ import annotations

import datetime as dt
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from foot.analysis.engine import Engine
from foot.analysis.ledgerbook import ForecastBook, record_run
from foot.analysis.request import DEFAULT_TIMEZONE
from foot.analysis.watch import WatchPlan, WatchReport, watch_until_kickoff

__all__ = ["Supervisor", "WatchHandle"]


@dataclass
class WatchHandle:
    """One server-side watch, and what it has done so far."""

    identifier: str
    matches: str
    kickoff: dt.datetime
    timezone: str = DEFAULT_TIMEZONE
    bookmaker: str = ""
    started_at: dt.datetime | None = None
    report: WatchReport | None = None
    finished: bool = False
    error: str = ""
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    """Guards the fields the watch thread writes while a request reads them."""

    def summary(self) -> str:
        """One paragraph an operator can read on a phone, at a glance."""
        with self.lock:
            report, finished, error = self.report, self.finished, self.error
        head = f"{self.matches.strip().splitlines()[0][:60]} — {self.identifier[:8]}"
        if error:
            return f"{head}\n  ARRÊTÉ : {error}"
        if report is None:
            return f"{head}\n  démarré, aucun contrôle encore effectué"
        success = report.last_success
        state = "terminé" if finished else "en cours"
        if success is None:
            return (
                f"{head}\n  {state} · {len(report.attempts)} tentative(s) · "
                f"aucune vérification réussie"
            )
        return (
            f"{head}\n  {state} · {len(report.attempts)} tentative(s) · "
            f"dernière réussite {success.at:%d/%m %H:%M} → {success.decision}"
        )


class Supervisor:
    """Holds the running watches for one server process.

    Deliberately in memory: a durable scheduler is a different promise, and
    claiming one without a restart-safe store would be exactly the kind of
    announced-but-absent mechanism this project refuses.  What is promised is
    narrow and true — a watch outlives the request and the browser tab.
    """

    __slots__ = ("_book", "_engine", "_handles", "_lock", "_threads")

    def __init__(self, engine: Engine, *, book: ForecastBook | None = None) -> None:
        self._engine = engine
        self._book = book
        self._handles: dict[str, WatchHandle] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()

    def start(
        self,
        *,
        matches: str,
        kickoff: dt.datetime,
        timezone: str = DEFAULT_TIMEZONE,
        bookmaker: str = "",
        runner: Callable[[WatchHandle], None] | None = None,
    ) -> WatchHandle:
        """Begin a watch and return immediately, with its handle."""
        handle = WatchHandle(
            identifier=uuid.uuid4().hex,
            matches=matches,
            kickoff=kickoff,
            timezone=timezone,
            bookmaker=bookmaker,
            started_at=dt.datetime.now(dt.timezone.utc),
        )
        with self._lock:
            self._handles[handle.identifier] = handle
        work = runner or self._run
        thread = threading.Thread(
            target=work, args=(handle,), name=f"suivi-{handle.identifier[:8]}",
            daemon=True,
        )
        with self._lock:
            self._threads[handle.identifier] = thread
        thread.start()
        return handle

    def _run(self, handle: WatchHandle) -> None:
        """The watch itself, on its own thread."""
        try:
            report = watch_until_kickoff(
                self._engine,
                matches=handle.matches,
                plan=WatchPlan(kickoff=handle.kickoff, timezone=handle.timezone),
                bookmaker=handle.bookmaker or None,
                on_attempt=lambda attempt, result: self._observe(handle, attempt, result),
            )
        except Exception as error:  # a failed watch is reported, never silent
            with handle.lock:
                handle.error = f"{type(error).__name__}: {error}"
                handle.finished = True
            return
        with handle.lock:
            handle.report = report
            handle.finished = True

    def _observe(self, handle: WatchHandle, attempt: object, result: object) -> None:
        """Publish progress after **each** attempt, and journal it when asked.

        Updating only at the end would leave the phone showing « démarré, aucun
        contrôle » for an hour while checks were actually happening — which is
        the same screen as a watch that died, and must not look like it.
        """
        with handle.lock:
            if handle.report is None:
                handle.report = WatchReport(
                    fixture=handle.matches.strip().splitlines()[0][:60],
                    plan=WatchPlan(kickoff=handle.kickoff, timezone=handle.timezone),
                )
            handle.report.attempts.append(attempt)  # type: ignore[arg-type]
        if self._book is not None:
            record_run(
                result.run.analyses,  # type: ignore[attr-defined]
                book=self._book,
                as_of=result.run.as_of,  # type: ignore[attr-defined]
                reason="suivi serveur",
            )

    def handles(self) -> tuple[WatchHandle, ...]:
        with self._lock:
            return tuple(self._handles.values())

    def get(self, identifier: str) -> WatchHandle | None:
        with self._lock:
            return self._handles.get(identifier)

    def render(self) -> str:
        running = self.handles()
        if not running:
            return (
                "Aucun suivi en cours. Un suivi lancé ici continue même si vous "
                "fermez l'onglet ; il s'arrête si le serveur s'arrête."
            )
        lines = [f"{len(running)} suivi(s) côté serveur :"]
        lines.extend(handle.summary() for handle in running)
        return "\n".join(lines)
