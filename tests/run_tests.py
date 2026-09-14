#!/usr/bin/env python3
"""A zero-dependency runner for the test suite.

``pytest tests`` is the normal way in.  This script exists so that the suite can
also be verified on a machine with nothing installed at all::

    python3 tests/run_tests.py [-v] [motif ...]

It discovers ``test_*`` functions in ``tests/test_*.py`` and runs them, counting
**passed, failed and skipped separately**.  That last distinction is not
cosmetic: an earlier version let a network test print "IGNORÉ" and return
normally, so an untested path was reported as a success.  A test that cannot run
now raises :class:`unittest.SkipTest` — which pytest also understands natively —
and is counted as skipped, never as passed.
"""

from __future__ import annotations

import importlib.util
import sys
import time
import traceback
import unittest
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent


@dataclass
class Summary:
    """Outcome of a run, with the three counts kept apart.

    Deliberately not ``slots=True``: that variant rebuilds the class and needs
    its module already registered in :data:`sys.modules`, which fails whenever
    this file is loaded dynamically — exactly what the regression test does.
    """

    passed: int = 0
    failed: int = 0
    skipped: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)
    skips: list[tuple[str, str]] = field(default_factory=list)
    elapsed: float = 0.0

    @property
    def total(self) -> int:
        return self.passed + self.failed + self.skipped

    @property
    def ok(self) -> bool:
        """Skipped tests are not failures, but they are not successes either."""
        return self.failed == 0

    def render(self) -> str:
        lines: list[str] = []
        for label, trace in self.failures:
            lines.append(f"\n{'=' * 70}\nÉCHEC {label}\n{'=' * 70}\n{trace}")
        if self.skips:
            lines.append(f"\n{'-' * 70}\nIGNORÉS ({len(self.skips)})")
            lines.extend(f"  {label} : {reason}" for label, reason in self.skips)
        lines.append(
            f"{self.passed} réussi(s), {self.failed} échec(s), "
            f"{self.skipped} ignoré(s) sur {self.total} en {self.elapsed:.2f}s"
        )
        return "\n".join(lines)


def load(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"footests.{path.stem}", path)
    if spec is None or spec.loader is None:  # pragma: no cover - import machinery
        raise ImportError(f"impossible de charger {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _refuse_outbound() -> Any:
    """Forbid outbound HTTP for the whole ``--sans-reseau`` run.

    Without it, "no network" means "no module named *live*" — which a test in any
    other file can quietly break, as one did by probing a real service with a
    fake key. A local proxy makes an address check useless, so the refusal sits
    at the HTTP boundary.
    """
    original = urllib.request.OpenerDirector.open

    def guarded(self: Any, fullurl: Any, *args: Any, **kwargs: Any) -> Any:
        url = fullurl if isinstance(fullurl, str) else getattr(fullurl, "full_url", "")
        host = urllib.parse.urlsplit(url).hostname or ""
        if host in {"127.0.0.1", "::1", "localhost"}:
            return original(self, fullurl, *args, **kwargs)
        raise RuntimeError(
            f"sortie réseau refusée vers {url!r} : ce lanceur tourne en "
            f"--sans-reseau. Injectez une réponse enregistrée."
        )

    urllib.request.OpenerDirector.open = guarded  # type: ignore[method-assign]
    return original


NETWORK_MODULES = ("test_live_sources",)
"""Modules that reach a remote host. Excluded by ``--sans-reseau``.

Kept as a name list rather than a pytest marker so the dependency-free runner
can honour the same separation CI uses: the deterministic suite must be able to
run, and fail, without any network at all.
"""


def run_suite(
    directory: Path,
    *,
    patterns: list[str] | None = None,
    verbose: bool = False,
    echo: bool = False,
    skip_network: bool = False,
) -> Summary:
    """Run every discovered test, returning the three counts.

    Args:
        patterns: substrings matched against ``module::function`` labels.
        echo: print progress dots as the run proceeds.
        skip_network: leave out the modules listed in :data:`NETWORK_MODULES`.
    """
    summary = Summary()
    started = time.time()
    guard = _refuse_outbound() if skip_network else None
    for path in sorted(directory.glob("test_*.py")):
        if skip_network and path.stem in NETWORK_MODULES:
            continue
        module = load(path)
        for name in sorted(dir(module)):
            if not name.startswith("test_"):
                continue
            function = getattr(module, name)
            if not callable(function):
                continue
            label = f"{path.stem}::{name}"
            if patterns and not any(p in label for p in patterns):
                continue
            try:
                function()
            except unittest.SkipTest as skip:
                summary.skipped += 1
                summary.skips.append((label, str(skip)))
                mark = "s"
            except Exception:
                summary.failed += 1
                summary.failures.append((label, traceback.format_exc()))
                mark = "F"
            else:
                summary.passed += 1
                mark = "."
            if echo:
                print(mark, end="", flush=True)
                if verbose:
                    print(f" {label}")
    if guard is not None:
        urllib.request.OpenerDirector.open = guard  # type: ignore[method-assign]
    summary.elapsed = time.time() - started
    return summary


def main(argv: list[str]) -> int:
    verbose = "-v" in argv
    skip_network = "--sans-reseau" in argv
    patterns = [a for a in argv if not a.startswith("-")]
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(TESTS))
    summary = run_suite(
        TESTS,
        patterns=patterns or None,
        verbose=verbose,
        echo=True,
        skip_network=skip_network,
    )
    print()
    print(summary.render())
    return 0 if summary.ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
