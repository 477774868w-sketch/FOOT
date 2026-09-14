"""Rendre le paquet importable, et interdire le réseau hors des essais réseau.

`foot` n'a aucune dépendance : lancer la suite ne doit demander ni construction,
ni environnement virtuel, ni installation éditable.

**Le garde-fou réseau.** Un test « hors réseau » qui ouvre une connexion n'est
pas hors réseau : il est lent, dépendant d'un hôte tiers, et — c'est le cas qui a
motivé ce garde-fou — il peut envoyer une clé factice à un vrai service.

Deux verrous, parce qu'un seul ne suffit pas :

* toute requête **HTTP sortante** est refusée. C'est le verrou qui compte : un
  mandataire configuré sur la machine peut écouter sur la boucle locale et
  relayer vers l'extérieur, si bien qu'un contrôle par adresse laisserait passer
  l'appel ;
* toute connexion **socket** vers autre chose que la boucle locale est refusée
  aussi, ce qui attrape ce qui n'emprunterait pas ``urllib``.

La boucle locale reste ouverte au niveau socket : plusieurs tests lancent un vrai
serveur HTTP sur 127.0.0.1, ce qui est une intégration locale et non un appel au
dehors — et ces tests parlent au serveur par ``urllib``, d'où l'exception ci-
dessous pour les adresses locales.
"""

from __future__ import annotations

import socket
import sys
import urllib.parse
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent
for candidate in (ROOT, ROOT / "tests"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

_LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost", "", "0.0.0.0"})


class OutboundNetworkError(RuntimeError):
    """Raised when an offline test tries to reach the outside world."""


def _is_loopback(address: object) -> bool:
    if isinstance(address, (str, bytes)):
        return True  # a unix socket path: local by construction
    if isinstance(address, tuple) and address:
        host = address[0]
        return isinstance(host, str) and host in _LOOPBACK
    return False


@pytest.fixture(autouse=True)
def _refuse_outbound_network(request: pytest.FixtureRequest) -> Iterator[None]:
    """Forbid outbound connections unless the test is marked ``network``."""
    if request.node.get_closest_marker("network") is not None:
        yield
        return
    original_connect = socket.socket.connect
    original_open = urllib.request.OpenerDirector.open

    def guarded_connect(self: socket.socket, address: Any) -> Any:
        if _is_loopback(address):
            return original_connect(self, address)
        raise OutboundNetworkError(_refusal(address))

    def guarded_open(self: Any, fullurl: Any, *args: Any, **kwargs: Any) -> Any:
        url = fullurl if isinstance(fullurl, str) else getattr(fullurl, "full_url", "")
        host = urllib.parse.urlsplit(url).hostname or ""
        if host in _LOOPBACK:
            return original_open(self, fullurl, *args, **kwargs)
        raise OutboundNetworkError(_refusal(url))

    socket.socket.connect = guarded_connect  # type: ignore[assignment]
    urllib.request.OpenerDirector.open = guarded_open  # type: ignore[method-assign]
    try:
        yield
    finally:
        socket.socket.connect = original_connect  # type: ignore[method-assign]
        urllib.request.OpenerDirector.open = original_open  # type: ignore[method-assign]


def _refusal(target: object) -> str:
    return (
        f"sortie réseau refusée vers {target!r} : ce test n'est pas marqué "
        f"« network ». Injectez une réponse enregistrée, ou marquez le test et "
        f"acceptez qu'il dépende d'un hôte tiers."
    )
