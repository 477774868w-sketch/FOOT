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

Un redémarrage du serveur — un déploiement, une mise en veille de l'hébergeur —
arrête les fils. Pour que le contrôle de T−75 ne disparaisse pas avec eux, chaque
suivi est **écrit** au moment où il démarre, dans un fichier en ajout seul, et
:meth:`Supervisor.resume` le relance au démarrage suivant.

Ce qui est repris et ce qui ne l'est pas se lit à l'écran, sans ambiguïté :

* un suivi dont le coup d'envoi est encore devant nous **repart**, avec ses
  contrôles restants ;
* un suivi dont le coup d'envoi est passé pendant l'arrêt est déclaré
  **manqué** — il n'a pas eu lieu, et aucun écran ne prétendra le contraire ;
* sans fichier de suivis, rien n'est repris, et :meth:`Supervisor.render` le dit
  plutôt que de laisser croire à une reprise.
"""

from __future__ import annotations

import datetime as dt
import json
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from foot.analysis.engine import Engine
from foot.analysis.ledgerbook import ForecastBook, record_run
from foot.analysis.request import DEFAULT_TIMEZONE
from foot.analysis.watch import WatchPlan, WatchReport, watch_until_kickoff

__all__ = ["DEFAULT_WATCHES", "LedgerJob", "Supervisor", "WatchHandle", "WatchStore"]

DEFAULT_WATCHES = ".foot-suivis.jsonl"
"""Where the watches are written when « --suivis » is given without a path."""


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
    resumed: bool = False
    """Whether this watch was restored from the store rather than just started."""

    missed: bool = False
    """The kick-off went by while the server was down: nothing was checked."""

    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    """Guards the fields the watch thread writes while a request reads them."""

    def summary(self) -> str:
        """One paragraph an operator can read on a phone, at a glance."""
        with self.lock:
            report, finished, error = self.report, self.finished, self.error
        head = f"{self.matches.strip().splitlines()[0][:60]} — {self.identifier[:8]}"
        if self.resumed:
            head += " · repris après redémarrage"
        if self.missed:
            return (
                f"{head}\n  MANQUÉ : le coup d'envoi "
                f"({self.kickoff:%d/%m %H:%M}) est passé pendant l'arrêt du "
                f"serveur. Aucun contrôle n'a eu lieu."
            )
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


class WatchStore:
    """The watches on disk, so a restart does not silently cancel them.

    An append-only file, like the forecast journal and for the same reason:
    nothing is ever rewritten, so the history of what was asked and what became
    of it survives a crash in the middle of a write. The last line about a watch
    is what counts.

    Backing it up is copying the file; restoring it is putting it back.
    """

    __slots__ = ("_lock", "_path")

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def record(self, handle: WatchHandle, state: str) -> None:
        """Append one event about one watch: « lancé », « terminé », « manqué »."""
        entry = {
            "identifiant": handle.identifier,
            "état": state,
            "rencontres": handle.matches,
            "coup_denvoi": handle.kickoff.isoformat(),
            "fuseau": handle.timezone,
            "bookmaker": handle.bookmaker,
            "écrit_le": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as handle_file:
                handle_file.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def pending(self) -> tuple[WatchHandle, ...]:
        """Watches whose last recorded state is « lancé ».

        A corrupt line is skipped rather than fatal: the point of this file is to
        rescue the watches it *can* rescue after an unclean stop.
        """
        if not self._path.exists():
            return ()
        latest: dict[str, dict[str, object]] = {}
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            identifier = entry.get("identifiant")
            if isinstance(identifier, str) and identifier:
                latest[identifier] = entry
        handles: list[WatchHandle] = []
        for identifier, entry in latest.items():
            if entry.get("état") != "lancé":
                continue
            try:
                kickoff = dt.datetime.fromisoformat(str(entry.get("coup_denvoi", "")))
            except ValueError:
                continue
            if kickoff.tzinfo is None:
                continue
            handles.append(
                WatchHandle(
                    identifier=identifier,
                    matches=str(entry.get("rencontres", "")),
                    kickoff=kickoff,
                    timezone=str(entry.get("fuseau", DEFAULT_TIMEZONE)),
                    bookmaker=str(entry.get("bookmaker", "")),
                    resumed=True,
                )
            )
        return tuple(handles)


class Supervisor:
    """Holds the running watches for one server process.

    In memory while the process lives, and — when a :class:`WatchStore` is given
    — written down, so that a restart resumes what is still ahead and declares
    what it missed. Without a store the old, narrower promise holds unchanged: a
    watch outlives the request and the browser tab, and nothing more.
    """

    __slots__ = (
        "_book",
        "_engine",
        "_handles",
        "_ledger_job",
        "_lock",
        "_store",
        "_threads",
        "_watch_engine",
    )

    def __init__(
        self,
        engine: Engine,
        *,
        book: ForecastBook | None = None,
        watch_engine: Engine | None = None,
        store: WatchStore | None = None,
    ) -> None:
        self._engine = engine
        # Watches read team sheets minutes apart; the day-to-day engine reads
        # them through a six-hour cache. Handing the watch that engine meant the
        # T−60 check replayed T−75's answer — the exact defect the CLI already
        # avoided, reintroduced by the button. One policy, both paths.
        self._watch_engine = watch_engine or engine
        self._book = book
        self._store = store
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
        return self._launch(handle, runner=runner)

    def _launch(
        self,
        handle: WatchHandle,
        *,
        runner: Callable[[WatchHandle], None] | None = None,
    ) -> WatchHandle:
        """Put one handle on its own thread, after writing it down."""
        handle.started_at = handle.started_at or dt.datetime.now(dt.timezone.utc)
        if self._store is not None:
            # Written **before** the thread starts: a crash between the two
            # loses a watch that never ran, which the next start will resume.
            # Writing afterwards could lose one that did.
            self._store.record(handle, "lancé")
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
            if self._store is not None:
                self._store.record(handle, "terminé")
            return
        with handle.lock:
            handle.report = report
            handle.finished = True
        if self._store is not None:
            self._store.record(handle, "terminé")

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

    def resume(
        self, *, now: dt.datetime | None = None
    ) -> tuple[tuple[WatchHandle, ...], tuple[WatchHandle, ...]]:
        """Restart what the store still owes, and declare what was missed.

        Returns ``(repris, manqués)``. A watch whose kick-off went by while the
        server was down is **not** restarted and **not** forgotten: it is written
        back as « manqué » and shown as such, because a watch that did not happen
        must never be displayed as one that did.

        Safe to call twice: a watch already held by this process is left alone.
        """
        if self._store is None:
            return ((), ())
        instant = now or dt.datetime.now(dt.timezone.utc)
        resumed: list[WatchHandle] = []
        missed: list[WatchHandle] = []
        for handle in self._store.pending():
            with self._lock:
                if handle.identifier in self._handles:
                    continue
            if handle.kickoff <= instant:
                handle.finished = True
                handle.missed = True
                with self._lock:
                    self._handles[handle.identifier] = handle
                self._store.record(handle, "manqué")
                missed.append(handle)
                continue
            resumed.append(self._launch(handle))
        return (tuple(resumed), tuple(missed))

    def handles(self) -> tuple[WatchHandle, ...]:
        with self._lock:
            return tuple(self._handles.values())

    def get(self, identifier: str) -> WatchHandle | None:
        with self._lock:
            return self._handles.get(identifier)

    def render(self) -> str:
        running = self.handles()
        durable = (
            f"Les suivis sont écrits dans {self._store.path} : un redémarrage du "
            f"serveur les reprend, sauf ceux dont le coup d'envoi est passé "
            f"entre-temps, marqués « manqué »."
            if self._store is not None
            else "Aucun fichier de suivis n'est configuré : un redémarrage du "
            "serveur perd les suivis en cours. Démarrez le serveur avec "
            "« --suivis » pour qu'ils soient repris."
        )
        if not running:
            return (
                "Aucun suivi en cours. Un suivi lancé ici continue même si vous "
                f"fermez l'onglet.\n{durable}"
            )
        lines = [f"{len(running)} suivi(s) côté serveur :"]
        lines.extend(handle.summary() for handle in running)
        lines.append("")
        lines.append(durable)
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
