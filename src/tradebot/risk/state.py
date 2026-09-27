"""État du risque, PERSISTÉ sur disque.

Pourquoi persister : si le bot redémarre après avoir perdu 1,9 % dans la journée,
il ne doit pas « oublier » cette perte et repartir avec une limite neuve.
Écriture atomique (fichier temporaire puis ``os.replace``) : un crash en pleine
écriture ne corrompt jamais l'état.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path

from tradebot.core.sessions import trading_day, trading_week


@dataclass(slots=True)
class RiskState:
    peak_equity: float
    day: str = ""
    day_start_equity: float = 0.0
    week: str = ""
    week_start_equity: float = 0.0
    trades_today: int = 0
    day_locked: bool = False
    day_lock_reason: str = ""
    week_locked: bool = False
    halted: bool = False  # HALT permanent : seule une action humaine le lève
    halt_reason: str = ""

    def roll(self, ts: datetime, equity: float) -> None:
        """Passage à une nouvelle journée / semaine de trading."""
        d: date = trading_day(ts)
        wy, ww = trading_week(ts)
        week_key = f"{wy}-W{ww:02d}"
        if self.day != d.isoformat():
            self.day = d.isoformat()
            self.day_start_equity = equity
            self.trades_today = 0
            self.day_locked = False
            self.day_lock_reason = ""
        if self.week != week_key:
            self.week = week_key
            self.week_start_equity = equity
            self.week_locked = False

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    @classmethod
    def from_json(cls, raw: str) -> RiskState:
        return cls(**json.loads(raw))


class StateStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self, default_equity: float) -> RiskState:
        if self.path.exists():
            return RiskState.from_json(self.path.read_text(encoding="utf-8"))
        return RiskState(peak_equity=default_equity)

    def save(self, state: RiskState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(state.to_json(), encoding="utf-8")
        os.replace(tmp, self.path)


class MemoryStateStore(StateStore):
    """Pour le backtest : pas d'écriture disque."""

    def __init__(self) -> None:
        self._state: RiskState | None = None

    def load(self, default_equity: float) -> RiskState:
        return self._state or RiskState(peak_equity=default_equity)

    def save(self, state: RiskState) -> None:
        self._state = state
