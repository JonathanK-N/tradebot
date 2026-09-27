"""Transport minimal : files (listes Redis) + clés. Implémentation mémoire pour les tests.

Pourquoi des listes Redis (RPUSH/BLPOP) plutôt que pub/sub : un message publié
pendant que le destinataire redémarre n'est PAS perdu, il attend dans la file.
Redis n'écoute que sur l'IP Tailscale, avec mot de passe.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from typing import Protocol


class Transport(Protocol):
    def push(self, queue: str, msg: str) -> None: ...
    def pop(self, queue: str, timeout: float = 0.0) -> str | None: ...
    def set(self, key: str, value: str, ttl_s: int | None = None) -> None: ...
    def get(self, key: str) -> str | None: ...


class MemoryTransport:
    def __init__(self) -> None:
        self._q: dict[str, deque[str]] = defaultdict(deque)
        self._kv: dict[str, str] = {}
        self._cv = threading.Condition()

    def push(self, queue: str, msg: str) -> None:
        with self._cv:
            self._q[queue].append(msg)
            self._cv.notify_all()

    def pop(self, queue: str, timeout: float = 0.0) -> str | None:
        deadline = time.monotonic() + timeout
        with self._cv:
            while not self._q[queue]:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._cv.wait(remaining)
            return self._q[queue].popleft()

    def set(self, key: str, value: str, ttl_s: int | None = None) -> None:
        self._kv[key] = value

    def get(self, key: str) -> str | None:
        return self._kv.get(key)


class RedisTransport:
    def __init__(self, url: str, prefix: str = "tb:") -> None:
        import redis

        self.r = redis.Redis.from_url(url, decode_responses=True, socket_timeout=10,
                                      health_check_interval=30)
        self.p = prefix

    def push(self, queue: str, msg: str) -> None:
        self.r.rpush(self.p + queue, msg)
        self.r.ltrim(self.p + queue, -10_000, -1)  # borne mémoire si le consommateur est mort

    def pop(self, queue: str, timeout: float = 0.0) -> str | None:
        if timeout <= 0:
            return self.r.lpop(self.p + queue)  # type: ignore[return-value]
        res = self.r.blpop([self.p + queue], timeout=max(1, int(timeout)))
        return res[1] if res else None  # type: ignore[index]

    def set(self, key: str, value: str, ttl_s: int | None = None) -> None:
        self.r.set(self.p + key, value, ex=ttl_s)

    def get(self, key: str) -> str | None:
        return self.r.get(self.p + key)  # type: ignore[return-value]
