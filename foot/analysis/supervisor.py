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

__all__ = ["LedgerJob", "Supervisor", "WatchHandle"]


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


@dataclass
class LedgerJob:
    """One server-side measurement, and how far along it is.

    Measuring means fetching results for every competition in the journal, which
    takes seconds to minutes. A phone cannot hold a request open for that, so the
    work happens here and the page reads the state — « en cours », « terminé »,
    or the reason it failed.
    """

    started_at: dt.datetime
    progress: str = "démarré"
    report: str = ""
    finished: bool = False
    error: str = ""
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def snapshot(self) -> tuple[bool, str, str, str]:
        with self.lock:
            return (self.finished, self.progress, self.report, self.error)


class Supervisor:
    """Holds the running watches for one server process.

    Deliberately in memory: a durable scheduler is a different promise, and
    claiming one without a restart-safe store would be exactly the kind of
    announced-but-absent mechanism this project refuses.  What is promised is
    narrow and true — a watch outlives the request and the browser tab.
    """

    __slots__ = (
        "_book",
        "_engine",
        "_handles",
        "_ledger_job",
        "_lock",
        "_threads",
        "_watch_engine",
    )

    def __init__(
        self,
        engine: Engine,
        *,
        book: ForecastBook | None = None,
        watch_engine: Engine | None = None,
    ) -> None:
        self._engine = engine
        # Watches read team sheets minutes apart; the day-to-day engine reads
        # them through a six-hour cache. Handing the watch that engine meant the
        # T−60 check replayed T−75's answer — the exact defect the CLI already
        # avoided, reintroduced by the button. One policy, both paths.
        self._watch_engine = watch_engine or engine
        self._book = book
        self._handles: dict[str, WatchHandle] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._ledger_job: LedgerJob | None = None
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
                self._watch_engine,
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

    # -- Bilan -------------------------------------------------------------
    def ledger_job(self) -> LedgerJob | None:
        """The current or last measurement, if one was ever asked for."""
        with self._lock:
            return self._ledger_job

    def measure_async(
        self, work: Callable[[LedgerJob], str], *, force: bool = False
    ) -> LedgerJob:
        """Run one measurement in the background, unless one is already running.

        ``work`` receives the job so it can publish progress as it goes, and
        returns the finished report. Keeping the collection outside this class is
        deliberate: the supervisor knows about threads and state, not about which
        provider serves which competition.
        """
        with self._lock:
            running = self._ledger_job
            if running is not None and not running.finished and not force:
                return running
            job = LedgerJob(started_at=dt.datetime.now(dt.timezone.utc))
            self._ledger_job = job

        def run() -> None:
            try:
                report = work(job)
            except Exception as error:  # a failed measurement says so
                with job.lock:
                    job.error = f"{type(error).__name__}: {error}"
                    job.finished = True
                return
            with job.lock:
                job.report = report
                job.progress = "terminé"
                job.finished = True

        threading.Thread(target=run, name="bilan", daemon=True).start()
        return job
