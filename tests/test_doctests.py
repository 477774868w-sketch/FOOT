"""Every example in the documentation is executed as a test.

Documentation that is not run is documentation that rots.  Walking the package
and running ``doctest`` over each module means a docstring example cannot drift
away from the behaviour it claims to describe.
"""

from __future__ import annotations

import doctest
import importlib
import pkgutil

import foot


def test_every_docstring_example_still_works() -> None:
    checked = 0
    failed: list[str] = []
    for info in pkgutil.walk_packages(foot.__path__, prefix="foot."):
        if info.name.endswith(".__main__"):
            continue
        module = importlib.import_module(info.name)
        result = doctest.testmod(module, verbose=False, report=False)
        checked += result.attempted
        if result.failed:
            failed.append(f"{info.name}: {result.failed} of {result.attempted}")
    package = doctest.testmod(foot, verbose=False, report=False)
    checked += package.attempted
    if package.failed:
        failed.append(f"foot: {package.failed} of {package.attempted}")

    assert not failed, "failing doctests: " + "; ".join(failed)
    assert checked > 10, f"expected the package to carry runnable examples, found {checked}"
