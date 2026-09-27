"""Drapeaux de contrôle partagés entre processus (bot Telegram, API, moteur live).

Mécanisme volontairement simple : un fichier JSON sur un volume partagé. Aucun
serveur à maintenir en vie pour que le kill switch fonctionne.

- ``pause``  : plus de NOUVELLES entrées ; les positions ouvertes gardent leurs SL/TP.
- ``kill``   : plus de nouvelles entrées ET fermeture immédiate de toutes les positions.
- ``resume`` : lève pause/kill (exige une confirmation TOTP côté bot/API).
- ``reset_halt`` : demande de levée du HALT drawdown (TOTP), appliquée par le moteur.

Le kill switch ULTIME reste l'application MT5 officielle sur ton téléphone :
elle ne dépend d'aucune ligne de ce code.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(slots=True)
class ControlFlags:
    paused: bool = False
    flatten_requested: bool = False
    reason: str = ""
    updated_at: str = ""
    updated_by: str = ""
    reset_halt_requested: bool = False


class ControlFile:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def read(self) -> ControlFlags:
        try:
            return ControlFlags(**json.loads(self.path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            return ControlFlags()
        except (json.JSONDecodeError, TypeError):
            # Fichier corrompu : on se met en PAUSE par sécurité (fail-closed).
            return ControlFlags(paused=True, reason="fichier de contrôle illisible")

    def _write(self, flags: ControlFlags) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(flags), indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

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
