"""Où vivent les clés d'API, et comment les déclarer sans les exposer.

Une clé est un secret : elle n'a rien à faire dans le dépôt, dans une ligne de
commande que l'historique du shell conserve, ni dans un rapport.  Ce module lit
donc les clés depuis, dans l'ordre :

1. l'**environnement** — ce que fait un hébergeur, et ce que fait la CI ;
2. un fichier local ``.foot-cles`` au format ``CLÉ=valeur``, hors du dépôt.

Il n'écrit jamais une clé dans un rapport : :func:`describe` ne montre que la
présence, la longueur et les quatre derniers caractères, de quoi vérifier qu'on
a collé la bonne sans la divulguer.

Une clé absente ne bloque rien : le fournisseur concerné est simplement marqué
« clé à fournir », et tout ce qui n'en dépend pas continue de fonctionner.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "CREDENTIALS_FILE",
    "CredentialStatus",
    "describe",
    "load_credentials",
    "sample_file",
]

CREDENTIALS_FILE = ".foot-cles"
"""Local, git-ignored file holding ``CLÉ=valeur`` lines."""

_MIN_VISIBLE = 8
"""Below this length a key is too short to show any tail without leaking it."""


@dataclass(frozen=True, slots=True)
class CredentialStatus:
    """Whether one key is configured, and where it came from — never its value."""

    name: str
    present: bool
    origin: str = ""
    """``environnement``, the file path, or empty when absent."""

    length: int = 0
    tail: str = ""

    def render(self) -> str:
        if not self.present:
            return f"  ✗ {self.name:<28} absente"
        shown = f"…{self.tail}" if self.tail else "(trop courte pour être affichée)"
        return (
            f"  ✓ {self.name:<28} présente ({self.length} caractères, {shown}) "
            f"— {self.origin}"
        )


def load_credentials(
    path: str | Path = CREDENTIALS_FILE, *, apply: bool = True
) -> dict[str, str]:
    """Read ``CLÉ=valeur`` lines, and put them in the environment by default.

    The environment already set wins: an operator exporting a key for one run
    must not be silently overridden by a stale file.
    """
    file = Path(path)
    values: dict[str, str] = {}
    if not file.exists():
        return values
    for raw in file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name, value = name.strip(), value.strip().strip("'\"")
        if not name or not value:
            continue
        values[name] = value
        if apply and not os.environ.get(name, "").strip():
            os.environ[name] = value
    return values


def describe(
    names: Mapping[str, str], *, path: str | Path = CREDENTIALS_FILE
) -> tuple[CredentialStatus, ...]:
    """Report presence and origin for each expected key, never its value."""
    from_file = load_credentials(path, apply=False)
    statuses: list[CredentialStatus] = []
    for name in sorted(names):
        value = os.environ.get(name, "").strip()
        origin = "environnement"
        if not value and name in from_file:
            value, origin = from_file[name], str(path)
        elif value and name in from_file and from_file[name] == value:
            origin = f"environnement (et {path})"
        statuses.append(
            CredentialStatus(
                name=name,
                present=bool(value),
                origin=origin if value else "",
                length=len(value),
                tail=value[-4:] if len(value) >= _MIN_VISIBLE else "",
            )
        )
    return tuple(statuses)


def sample_file(names: Mapping[str, str]) -> str:
    """A ready-to-fill ``.foot-cles``, with what each key unlocks."""
    lines = [
        "# Clés d'API — fichier local, à NE PAS committer.",
        f"# Ajoutez « {CREDENTIALS_FILE} » à votre .gitignore (déjà fait ici).",
        "# Une clé absente retire son fournisseur ; le reste continue de marcher.",
        "",
    ]
    for name in sorted(names):
        lines.append(f"# {names[name]}")
        lines.append(f"{name}=")
        lines.append("")
    return "\n".join(lines)
