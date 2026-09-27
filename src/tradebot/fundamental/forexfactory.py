"""Récupération du calendrier de la semaine (flux JSON public de ForexFactory).

⚠️ Flux NON OFFICIEL : il peut changer de format ou disparaître sans préavis, et il
limite la fréquence des requêtes (1 requête / heure largement suffit). C'est
précisément pour cela que le calendrier est fail-closed. Si ce flux casse, les
alternatives payantes sont Trading Economics ou Financial Modeling Prep : il suffit
d'écrire un autre ``fetch_*`` qui renvoie des ``EconomicEvent``.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from tradebot.fundamental.calendar import EconomicEvent

FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"


def parse_ff(payload: list[dict]) -> list[EconomicEvent]:
    out: list[EconomicEvent] = []
    for item in payload:
        try:
            ts = datetime.fromisoformat(item["date"]).astimezone(UTC)
        except (KeyError, ValueError):
            continue  # « All Day », « Tentative » : ignorés (pas d'heure exploitable)
        impact = str(item.get("impact", "")).lower()
        if impact not in {"low", "medium", "high"}:
            continue
        out.append(EconomicEvent(ts, str(item.get("country", "")), str(item.get("title", "")), impact))
    return out


def fetch_ff_week(timeout: float = 15.0) -> list[EconomicEvent]:
    r = httpx.get(FF_URL, timeout=timeout, headers={"User-Agent": "tradebot/0.1"})
    r.raise_for_status()
    return parse_ff(r.json())
