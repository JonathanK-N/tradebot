"""Stockage clé-valeur minimal pour l'état partagé entre processus (contrôle, statut).

- ``FileKV``  : un fichier JSON par clé, écriture atomique. Pour un seul hôte
  (VPS avec volume Docker partagé).
- ``RedisKV`` : pour les plateformes où les services ne partagent AUCUN disque
  (Railway : un volume ne s'attache qu'à un seul service).

Les deux lèvent ``StateUnavailable`` si le stockage est injoignable ; c'est à
l'appelant de décider du comportement sûr (fail-closed pour le contrôle).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol


class StateUnavailable(ConnectionError):
    pass


class KV(Protocol):
    def get(self, key: str) -> str | None: ...
    def set(self, key: str, value: str) -> None: ...


class FileKV:
    def __init__(self, directory: str | Path) -> None:
        self.dir = Path(directory)

    def _path(self, key: str) -> Path:
        return self.dir / f"{key}.json"

    def get(self, key: str) -> str | None:
        try:
            return self._path(key).read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as e:
            raise StateUnavailable(str(e)) from e

    def set(self, key: str, value: str) -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = self._path(key).with_suffix(".tmp")
            tmp.write_text(value, encoding="utf-8")
            os.replace(tmp, self._path(key))
        except OSError as e:
            raise StateUnavailable(str(e)) from e


class RedisKV:
    def __init__(self, url: str, prefix: str = "tb:state:", client=None) -> None:
        if client is None:
            import redis

            client = redis.Redis.from_url(url, decode_responses=True, socket_timeout=5,
                                          socket_connect_timeout=5, health_check_interval=30)
        self.r = client
        self.prefix = prefix

    def get(self, key: str) -> str | None:
        import redis

        try:
            return self.r.get(self.prefix + key)
        except redis.RedisError as e:
            raise StateUnavailable(str(e)) from e

    def set(self, key: str, value: str) -> None:
        import redis

        try:
            self.r.set(self.prefix + key, value)
        except redis.RedisError as e:
            raise StateUnavailable(str(e)) from e
