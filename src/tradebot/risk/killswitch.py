"""Drapeaux de contrôle partagés entre processus (bot Telegram, API, moteur live).

Stockage : un fichier JSON sur un volume partagé (``ControlFile``, VPS unique) ou
Redis (``ControlStore`` + ``RedisKV``, plateformes sans disque partagé comme Railway).
Si le stockage est illisible ou injoignable, la lecture renvoie PAUSE (fail-closed).

- ``pause``  : plus de NOUVELLES entrées ; les positions ouvertes gardent leurs SL/TP.
- ``kill``   : plus de nouvelles entrées ET fermeture immédiate de toutes les positions.
- ``resume`` : lève pause/kill (exige une confirmation TOTP côté bot/API).
- ``reset_halt`` : demande de levée du HALT drawdown (TOTP), appliquée par le moteur.

Le kill switch ULTIME reste l'application MT5 officielle sur ton téléphone :
elle ne dépend d'aucune ligne de ce code.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from tradebot.core.kv import KV, FileKV, StateUnavailable


@dataclass(slots=True)
class ControlFlags:
    paused: bool = False
    flatten_requested: bool = False
    reason: str = ""
    updated_at: str = ""
    updated_by: str = ""
    reset_halt_requested: bool = False


class ControlStore:
    def __init__(self, kv: KV, key: str = "control") -> None:
        self.kv = kv
        self.key = key

    def read(self) -> ControlFlags:
        try:
            raw = self.kv.get(self.key)
        except StateUnavailable as e:
            # Stockage injoignable : PAUSE par sécurité (fail-closed).
            return ControlFlags(paused=True, reason=f"stockage de contrôle injoignable : {e}")
        if raw is None:
            return ControlFlags()
        try:
            return ControlFlags(**json.loads(raw))
        except (json.JSONDecodeError, TypeError):
            return ControlFlags(paused=True, reason="état de contrôle illisible")

    def _write(self, flags: ControlFlags) -> None:
        """Lève ``StateUnavailable`` : le bot / l'API doivent signaler l'échec à l'utilisateur."""
        self.kv.set(self.key, json.dumps(asdict(flags), indent=2))

    def _now(self) -> str:
        return datetime.now(UTC).isoformat()

    def pause(self, reason: str, by: str) -> ControlFlags:
        f = self.read()
        f.paused, f.reason, f.updated_at, f.updated_by = True, reason, self._now(), by
        self._write(f)
        return f

    def kill(self, reason: str, by: str) -> ControlFlags:
        f = ControlFlags(paused=True, flatten_requested=True, reason=reason, updated_at=self._now(),
                         updated_by=by)
        self._write(f)
        return f

    def ack_flatten(self) -> None:
        """Le moteur confirme avoir fermé les positions ; la pause reste active."""
        f = self.read()
        f.flatten_requested = False
        self._write(f)

    def request_reset_halt(self, by: str) -> ControlFlags:
        """Demande de levée du HALT drawdown : appliquée par le moteur (seul
        propriétaire de l'état du risque), puis acquittée."""
        f = self.read()
        f.reset_halt_requested, f.updated_at, f.updated_by = True, self._now(), by
        self._write(f)
        return f

    def ack_reset_halt(self) -> None:
        f = self.read()
        f.reset_halt_requested = False
        self._write(f)

    def resume(self, by: str) -> ControlFlags:
        f = ControlFlags(reason="reprise manuelle", updated_at=self._now(), updated_by=by)
        self._write(f)
        return f


class ControlFile(ControlStore):
    """Variante fichier (compatibilité) : ``ControlFile("var/state/control.json")``."""

    def __init__(self, path: str | Path) -> None:
        path = Path(path)
        super().__init__(FileKV(path.parent), path.stem)
        self.path = path
