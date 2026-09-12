"""Make the in-tree package importable without installing it.

`foot` has no dependencies, so running the suite should not require a build
step, a virtualenv or an editable install.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for candidate in (ROOT, ROOT / "tests"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))
