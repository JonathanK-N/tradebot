"""Côté cœur (VPS Linux) : un BrokerAdapter qui parle au pont Windows via Redis."""

from __future__ import annotations

import time
from datetime import datetime

from tradebot.bridge import protocol as P
from tradebot.bridge.transport import Transport
from tradebot.core.models import AccountState, Bar, ExitReason, OrderIntent, Position, Trade, new_id
from tradebot.execution.base import BrokerAdapter, OrderResult


class BridgeUnavailable(ConnectionError):
    pass


class RemoteBroker(BrokerAdapter):
    name = "remote"

    def __init__(self, transport: Transport, *, reply_timeout_s: float = 20.0,
                 max_state_age_s: float = 60.0, clock=time.time) -> None:
        self.t = transport
        self.reply_timeout_s = reply_timeout_s
        self.max_state_age_s = max_state_age_s
        self.clock = clock

    def heartbeat(self) -> None:
        self.t.set("core_hb", str(self.clock()), ttl_s=600)

    def bridge_alive(self) -> bool:
        hb = self.t.get("bridge_hb")
        return hb is not None and self.clock() - float(hb) < self.max_state_age_s

    def _state(self, key: str) -> dict:
        raw = self.t.get(key)
        if raw is None:
            raise BridgeUnavailable(f"aucun état « {key} » publié par le pont")
        msg = P.loads(raw)
        if self.clock() - float(msg.get("at", 0)) > self.max_state_age_s:
            raise BridgeUnavailable(f"état « {key} » périmé : pont ou MT5 bloqué ?")
        return msg

    def account(self) -> AccountState:
        return P.decode(AccountState, self._state("account")["payload"])

    def positions(self) -> list[Position]:
        return [P.decode(Position, p) for p in self._state("positions")["payload"]]

    def _call(self, kind: str, payload: dict) -> OrderResult:
        cid = new_id()
        self.t.push("cmd", P.dumps(kind, payload, id=cid))
        raw = self.t.pop(f"reply:{cid}", timeout=self.reply_timeout_s)
        if raw is None:
            # Résultat INCONNU : l'ordre a peut-être été exécuté. L'idempotence côté pont
            # (client_order_id) permet de le renvoyer sans risque de doublon.
            return OrderResult(False, payload.get("client_order_id", ""), "pas de réponse du pont",
                               retryable=True)
        return P.decode_result(P.loads(raw)["payload"])

    def submit(self, intent: OrderIntent) -> OrderResult:
        return self._call("submit", P.encode(intent))

    def close_position(self, position_id: str, reason: ExitReason, ts: datetime) -> OrderResult:
        return self._call("close", {"position_id": position_id, "reason": reason.value})

    def close_all(self, reason: ExitReason, ts: datetime) -> list[OrderResult]:
        return [self._call("close_all", {"reason": reason.value})]

    def drain_closed_trades(self) -> list[Trade]:
        out = []
        while (raw := self.t.pop("trades")) is not None:
            out.append(P.decode(Trade, P.loads(raw)["payload"]))
        return out


class RemoteFeed:
    def __init__(self, transport: Transport) -> None:
        self.t = transport

    def history(self) -> list[Bar]:
        raw = self.t.get("history")
        if raw is None:
            raise BridgeUnavailable("historique non publié : le pont MT5 tourne-t-il ?")
        return [P.decode(Bar, b) for b in P.loads(raw)["payload"]]

    def poll(self) -> list[Bar]:
        out = []
        while (raw := self.t.pop("bars")) is not None:
            out.append(P.decode(Bar, P.loads(raw)["payload"]))
        return out
