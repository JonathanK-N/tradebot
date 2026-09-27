"""Calendrier économique et filtre des annonces à fort impact.

Principe FAIL-CLOSED : en réel, si le calendrier n'a pas été rafraîchi depuis
``max_calendar_age_hours``, le système considère qu'il est aveugle et n'ouvre
AUCUNE nouvelle position. Mieux vaut rater un trade que d'entrer 30 s avant le NFP.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tradebot.core.config import NewsConfig

IMPACT_RANK = {"low": 1, "medium": 2, "high": 3}


@dataclass(frozen=True, slots=True)
class EconomicEvent:
    ts: datetime  # UTC
    currency: str
    title: str
    impact: str  # low | medium | high

    @property
    def impact_rank(self) -> int:
        return IMPACT_RANK.get(self.impact.lower(), 0)


@dataclass(frozen=True, slots=True)
class BlackoutCheck:
    blocked: bool
    reason: str = ""
    event: EconomicEvent | None = None


class EventCalendar:
    def __init__(
        self,
        cfg: NewsConfig,
        events: list[EconomicEvent] | None = None,
        *,
        require_fresh: bool = False,
    ) -> None:
        self.cfg = cfg
        self.require_fresh = require_fresh  # True en réel, False en backtest
        self.events: list[EconomicEvent] = []
        self.last_refresh: datetime | None = None
        if events:
            self.set_events(events, refreshed_at=None)

    def set_events(self, events: list[EconomicEvent], refreshed_at: datetime | None) -> None:
        min_rank = IMPACT_RANK[self.cfg.min_impact]
        cur = {c.upper() for c in self.cfg.currencies}
        self.events = sorted(
            (e for e in events if e.impact_rank >= min_rank and e.currency.upper() in cur),
            key=lambda e: e.ts,
        )
        self.last_refresh = refreshed_at

    def merge_events(self, events: list[EconomicEvent], refreshed_at: datetime) -> None:
        seen = {(e.ts, e.title) for e in self.events}
        merged = self.events + [e for e in events if (e.ts, e.title) not in seen]
        self.set_events(merged, refreshed_at)

    def _window(self, ev: EconomicEvent) -> tuple[timedelta, timedelta]:
        for w in self.cfg.windows:
            if w.keyword.lower() in ev.title.lower():
                return timedelta(minutes=w.before_min), timedelta(minutes=w.after_min)
        return (
            timedelta(minutes=self.cfg.default_before_min),
            timedelta(minutes=self.cfg.default_after_min),
        )

    def check(self, ts: datetime) -> BlackoutCheck:
        if not self.cfg.enabled:
            return BlackoutCheck(False)
        if self.require_fresh:
            if self.last_refresh is None:
                return BlackoutCheck(True, "calendrier jamais chargé (fail-closed)")
            age = ts - self.last_refresh
            if age > timedelta(hours=self.cfg.max_calendar_age_hours):
                hours = age.total_seconds() / 3600
                return BlackoutCheck(True, f"calendrier périmé ({hours:.0f} h, fail-closed)")
        # fenêtre max 1 jour : on ne parcourt que les événements proches
        for ev in self.events:
            if ev.ts > ts + timedelta(days=1):
                break
            before, after = self._window(ev)
            if ev.ts - before <= ts <= ev.ts + after:
                return BlackoutCheck(True, f"annonce {ev.title} à {ev.ts:%H:%M} UTC", ev)
        return BlackoutCheck(False)

    def upcoming(self, ts: datetime, hours: float = 24) -> list[EconomicEvent]:
        end = ts + timedelta(hours=hours)
        return [e for e in self.events if ts <= e.ts <= end]


def load_events_csv(path: str | Path) -> list[EconomicEvent]:
    """CSV : ts_utc (ISO 8601), currency, title, impact."""
    out: list[EconomicEvent] = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            ts = datetime.fromisoformat(row["ts_utc"])
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            out.append(EconomicEvent(ts.astimezone(UTC), row["currency"], row["title"], row["impact"]))
    return out


def save_events_csv(events: list[EconomicEvent], path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ts_utc", "currency", "title", "impact"])
        for e in sorted(events, key=lambda e: e.ts):
            w.writerow([e.ts.isoformat(), e.currency, e.title, e.impact])
