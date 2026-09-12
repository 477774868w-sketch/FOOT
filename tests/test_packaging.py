"""Architectural invariants — the promises the README makes about the package.

A README claim that nothing enforces is a claim that quietly stops being true.
"""

from __future__ import annotations

import ast
import importlib
import pkgutil
import sys
from pathlib import Path

import foot

PACKAGE = Path(foot.__file__).resolve().parent


def _modules() -> list[str]:
    names = ["foot"]
    names.extend(
        info.name
        for info in pkgutil.walk_packages(foot.__path__, prefix="foot.")
        if not info.name.endswith(".__main__")
    )
    return names


def test_the_package_imports_nothing_outside_the_standard_library() -> None:
    """The headline claim: no dependencies, checked by reading every import."""
    stdlib = sys.stdlib_module_names
    foreign: list[str] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots = [node.module.split(".")[0]]
            else:
                continue
            foreign.extend(
                f"{path.relative_to(PACKAGE)}: {root}"
                for root in roots
                if root not in stdlib and root != "foot"
            )
    assert not foreign, f"third-party imports found: {foreign}"


def test_every_module_is_documented_and_declares_its_exports() -> None:
    for name in _modules():
        module = importlib.import_module(name)
        assert module.__doc__, f"{name} has no module docstring"
        exported = getattr(module, "__all__", None)
        assert exported is not None, f"{name} does not declare __all__"
        for symbol in exported:
            assert hasattr(module, symbol), f"{name}.__all__ names missing {symbol}"


def test_the_top_level_api_is_importable_and_sorted() -> None:
    assert foot.__all__ == sorted(foot.__all__), "foot.__all__ is not sorted"
    assert len(set(foot.__all__)) == len(foot.__all__), "foot.__all__ repeats a name"
    for symbol in foot.__all__:
        assert hasattr(foot, symbol), f"foot.__all__ names missing {symbol}"


def test_the_package_ships_its_type_marker() -> None:
    assert (PACKAGE / "py.typed").exists(), "py.typed is missing, so callers get no types"


def test_public_callables_carry_docstrings() -> None:
    undocumented: list[str] = []
    for name in _modules():
        module = importlib.import_module(name)
        for symbol in getattr(module, "__all__", []):
            value = getattr(module, symbol)
            if callable(value) and not getattr(value, "__doc__", None):
                undocumented.append(f"{name}.{symbol}")
    assert not undocumented, f"undocumented public API: {undocumented}"
