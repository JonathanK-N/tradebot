"""Instantané d'état publié par le moteur live, lu par le bot Telegram et l'API.

Découplage volontaire : le bot et le dashboard ne parlent JAMAIS directement au
broker. Ils lisent cet instantané et écrivent des drapeaux de contrôle.
Si l'instantané est trop vieux, c'est lui-même une alerte (« moteur arrêté ? »).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tradebot.core.kv import KV, FileKV, StateUnavailable


class StatusStore:
    def __init__(self, kv: KV, key: str = "status") -> None:
        self.kv = kv
        self.key = key

    def write(self, data: dict[str, Any]) -> None:
        data = {**data, "written_at": datetime.now(UTC).isoformat()}
        self.kv.set(self.key, json.dumps(data, indent=2, default=str, ensure_ascii=False))

    def read(self) -> dict[str, Any]:
        try:
            raw = self.kv.get(self.key)
            data = json.loads(raw) if raw else None
        except (StateUnavailable, json.JSONDecodeError) as e:
            return {"available": False, "error": str(e)}
        if data is None:
            return {"available": False}
        written = datetime.fromisoformat(data["written_at"])
        data["age_s"] = (datetime.now(UTC) - written).total_seconds()
        data["available"] = True
        return data


class StatusFile(StatusStore):
    """Variante fichier (compatibilité) : ``StatusFile("var/state/status.json")``."""

    def __init__(self, path: str | Path) -> None:
        path = Path(path)
        super().__init__(FileKV(path.parent), path.stem)
        self.path = path
