"""Allow ``python -m foot``."""

from __future__ import annotations

from foot.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
