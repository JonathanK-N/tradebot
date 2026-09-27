"""Sérialisation JSON des objets échangés entre le cœur (Linux) et le pont MT5 (Windows)."""

from __future__ import annotations

import json
from dataclasses import asdict, fields
from datetime import datetime, timedelta
from typing import Any

from tradebot.core.models import AccountState, Bar, ExitReason, Fill, OrderIntent, Position, Side, Trade
from tradebot.execution.base import OrderResult

VERSION = 1

_DT_FIELDS = {"ts", "entry_ts", "exit_ts", "expire_at"}


def _enc(v: Any) -> Any:
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, timedelta):
        return v.total_seconds()
    if hasattr(v, "value"):
        return v.value
    return v


def encode(obj: Any) -> dict[str, Any]:
    return {k: _enc(v) for k, v in asdict(obj).items()}


def decode[T](cls: type[T], d: dict[str, Any]) -> T:
    kw: dict[str, Any] = {}
    for f in fields(cls):  # type: ignore[arg-type]
        if f.name not in d:
            continue
        v = d[f.name]
        if f.name in _DT_FIELDS and v is not None:
            v = datetime.fromisoformat(v)
        elif f.name == "side":
            v = Side(v)
        elif f.name == "exit_reason":
            v = ExitReason(v)
        kw[f.name] = v
    return cls(**kw)


def dumps(kind: str, payload: Any, **extra: Any) -> str:
    return json.dumps({"v": VERSION, "kind": kind, "payload": payload, **extra})


def loads(raw: str | bytes) -> dict[str, Any]:
    msg = json.loads(raw)
    if msg.get("v") != VERSION:
        raise ValueError(f"version de protocole incompatible : {msg.get('v')}")
    return msg


def encode_result(r: OrderResult) -> dict[str, Any]:
    return {"ok": r.ok, "client_order_id": r.client_order_id, "message": r.message,
            "pending": r.pending, "retryable": r.retryable,
            "fill": encode(r.fill) if r.fill else None}


def decode_result(d: dict[str, Any]) -> OrderResult:
    return OrderResult(d["ok"], d["client_order_id"], d["message"],
                       decode(Fill, d["fill"]) if d.get("fill") else None, d["pending"], d["retryable"])


__all__ = ["AccountState", "Bar", "OrderIntent", "Position", "Trade", "decode", "dumps", "encode", "loads"]
