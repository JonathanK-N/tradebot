"""Fichier d'état publié par le moteur live, lu par le bot Telegram et l'API.

Découplage volontaire : le bot et le dashboard ne parlent JAMAIS directement au
broker. Ils lisent cet instantané et écrivent des drapeaux de contrôle.
Si l'instantané est trop vieux, c'est lui-même une alerte (« moteur arrêté ? »).
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class StatusFile:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {**data, "written_at": datetime.now(UTC).isoformat()}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, default=str, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)

    def read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {"available": False}
        written = datetime.fromisoformat(data["written_at"])
        data["age_s"] = (datetime.now(UTC) - written).total_seconds()
        data["available"] = True
        return data
