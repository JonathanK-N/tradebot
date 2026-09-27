"""Journal automatique : traçabilité complète signal -> décision -> ordre -> résultat.

- ``events`` : flux append-only (jamais de UPDATE ni de DELETE). Chaque ligne a un
  type, un horodatage UTC, un ``correlation_id`` et un payload JSON.
- ``trades`` : table dénormalisée pour les requêtes rapides (dashboard, rapports, fiscalité).
- ``equity`` : courbe d'equity (une ligne par minute en live).

SQLite en local/backtest, PostgreSQL en production (même code, URL différente).
"""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    insert,
    select,
)

from tradebot.core.models import RiskDecision, Trade

metadata = MetaData()

events_t = Table(
    "events", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("ts", DateTime(timezone=True), index=True, nullable=False),
    Column("kind", String(32), index=True, nullable=False),
    Column("correlation_id", String(32), index=True),
    Column("payload", Text, nullable=False),
)

trades_t = Table(
    "trades", metadata,
    Column("position_id", String(64), primary_key=True),
    Column("symbol", String(16)),
    Column("strategy", String(32), index=True),
    Column("side", String(4)),
    Column("volume", Float),
    Column("entry_ts", DateTime(timezone=True)),
    Column("entry_price", Float),
    Column("exit_ts", DateTime(timezone=True), index=True),
    Column("exit_price", Float),
    Column("exit_reason", String(16)),
    Column("gross_pnl", Float),
    Column("commission", Float),
    Column("swap", Float),
    Column("pnl", Float),
    Column("initial_risk", Float),
    Column("r_multiple", Float),
    Column("correlation_id", String(32), index=True),
)

equity_t = Table(
    "equity", metadata,
    Column("ts", DateTime(timezone=True), primary_key=True),
    Column("balance", Float),
    Column("equity", Float),
)


def _default(o: Any) -> Any:
    if isinstance(o, datetime):
        return o.isoformat()
    if isinstance(o, Enum):
        return o.value
    if is_dataclass(o) and not isinstance(o, type):
        return asdict(o)
    if hasattr(o, "total_seconds"):
        return o.total_seconds()
    return str(o)


def to_json(obj: Any) -> str:
    if is_dataclass(obj) and not isinstance(obj, type):
        obj = asdict(obj)
    return json.dumps(obj, default=_default, ensure_ascii=False)


class Journal:
    """Interface + implémentation mémoire (backtest)."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.trades: list[Trade] = []
        self.equity: list[tuple[datetime, float, float]] = []
        self.record_equity_every_min = 60  # backtest : 1 point par heure suffit

    def event(self, ts: datetime, kind: str, payload: Any, correlation_id: str = "") -> None:
        self.events.append({"ts": ts, "kind": kind, "correlation_id": correlation_id,
                            "payload": payload})

    def decision(self, d: RiskDecision) -> None:
        self.event(d.signal.ts, "decision", {
            "approved": d.approved, "reasons": list(d.reasons), "signal": d.signal,
            "intent": d.intent,
        }, d.signal.correlation_id)

    def trade(self, t: Trade) -> None:
        self.trades.append(t)
        self.event(t.exit_ts, "trade", t, t.correlation_id)

    def equity_point(self, ts: datetime, balance: float, equity: float) -> None:
        if not self.equity or (ts - self.equity[-1][0]).total_seconds() >= \
                self.record_equity_every_min * 60:
            self.equity.append((ts, balance, equity))

    def close(self) -> None:
        pass


class SqlJournal(Journal):
    def __init__(self, url: str) -> None:
        super().__init__()
        if url.startswith("sqlite:///"):
            Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(url, future=True, pool_pre_ping=True)
        metadata.create_all(self.engine)
        self.record_equity_every_min = 1

    def event(self, ts: datetime, kind: str, payload: Any, correlation_id: str = "") -> None:
        with self.engine.begin() as c:
            c.execute(insert(events_t).values(ts=ts, kind=kind, correlation_id=correlation_id,
                                              payload=to_json(payload)))

    def trade(self, t: Trade) -> None:
        with self.engine.begin() as c:
            row = {k: v for k, v in asdict(t).items()}
            row["side"] = t.side.value
            row["exit_reason"] = t.exit_reason.value
            row["r_multiple"] = t.r_multiple
            c.execute(insert(trades_t).values(**row))
        self.event(t.exit_ts, "trade", t, t.correlation_id)

    def equity_point(self, ts: datetime, balance: float, equity: float) -> None:
        with self.engine.begin() as c:
            last = c.execute(select(equity_t.c.ts).order_by(equity_t.c.ts.desc()).limit(1)).scalar()
            if last is not None and last.tzinfo is None:
                last = last.replace(tzinfo=ts.tzinfo)
            if last is None or (ts - last).total_seconds() >= self.record_equity_every_min * 60:
                c.execute(insert(equity_t).values(ts=ts, balance=balance, equity=equity))

    # ---------------------------------------------------------- lectures
    def recent_events(self, limit: int = 50, kind: str | None = None) -> list[dict[str, Any]]:
        q = select(events_t).order_by(events_t.c.id.desc()).limit(limit)
        if kind:
            q = q.where(events_t.c.kind == kind)
        with self.engine.connect() as c:
            return [
                {**dict(r._mapping), "payload": json.loads(r.payload)}
                for r in c.execute(q)
            ]

    def recent_trades(self, limit: int = 50) -> list[dict[str, Any]]:
        q = select(trades_t).order_by(trades_t.c.exit_ts.desc()).limit(limit)
        with self.engine.connect() as c:
            return [dict(r._mapping) for r in c.execute(q)]

    def equity_curve(self, limit: int = 2000) -> list[dict[str, Any]]:
        q = select(equity_t).order_by(equity_t.c.ts.desc()).limit(limit)
        with self.engine.connect() as c:
            return [dict(r._mapping) for r in c.execute(q)][::-1]

    def close(self) -> None:
        self.engine.dispose()
