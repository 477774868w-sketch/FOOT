#!/usr/bin/env python3
"""A zero-dependency runner for the test suite.

``pytest tests`` is the normal way in.  This script exists so that the suite can
also be verified on a machine with nothing installed at all:

    python3 tests/run_tests.py [-v] [pattern ...]

It discovers ``test_*`` functions in ``tests/test_*.py`` and runs them, which is
the entire feature set of pytest that this suite uses.
"""

from __future__ import annotations

import importlib.util
import sys
import time
import traceback
from pathlib import Path

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent


def load(path: Path) -> object:
    spec = importlib.util.spec_from_file_location(f"footests.{path.stem}", path)
    if spec is None or spec.loader is None:  # pragma: no cover - import machinery
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main(argv: list[str]) -> int:
    verbose = "-v" in argv
    patterns = [a for a in argv if not a.startswith("-")]
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(TESTS))

    passed = failed = 0
    failures: list[tuple[str, str]] = []
    started = time.time()

    for path in sorted(TESTS.glob("test_*.py")):
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
            except Exception:
                failed += 1
                failures.append((label, traceback.format_exc()))
                print("F", end="", flush=True)
                if verbose:
                    print(f" {label}")
            else:
                passed += 1
                print(".", end="", flush=True)
                if verbose:
                    print(f" {label}")

    elapsed = time.time() - started
    print()
    for label, trace in failures:
        print(f"\n{'=' * 70}\nFAILED {label}\n{'=' * 70}\n{trace}")
    print(f"{passed} passed, {failed} failed in {elapsed:.2f}s")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
