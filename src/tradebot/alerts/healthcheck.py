"""Dead man's switch via healthchecks.io (gratuit).

Le moteur « pingue » une URL à chaque cycle. S'il se tait (crash, VPS éteint,
boucle bloquée), c'est healthchecks.io — un service EXTÉRIEUR à ton infra — qui
t'envoie l'alerte (e-mail, Telegram, SMS). Un système ne peut pas signaler sa
propre mort : c'est pourquoi ce contrôle doit vivre ailleurs.
"""

from __future__ import annotations

import time

import httpx

from tradebot.core.logging import get_logger

log = get_logger(__name__)


class Healthcheck:
    def __init__(self, url: str | None, min_interval_s: float = 60.0) -> None:
        self.url = url
        self.min_interval_s = min_interval_s
        self._last = 0.0

    def ping(self, fail: bool = False) -> None:
        if not self.url or (time.monotonic() - self._last < self.min_interval_s and not fail):
            return
        try:
            httpx.get(self.url + ("/fail" if fail else ""), timeout=10)
            self._last = time.monotonic()
        except httpx.HTTPError as e:
            log.warning("healthcheck_ping_failed", error=str(e))
