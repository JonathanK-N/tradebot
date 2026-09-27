"""Pont d'exécution (tourne sur le VPS WINDOWS, à côté du terminal MT5).

Rôle : traduire les commandes du cœur en ordres MT5 et publier l'état du compte.
C'est le DEUXIÈME niveau de défense : même si le cœur envoie un ordre aberrant
(bug, compromission), le pont applique ses propres limites codées ici, sans
dépendre de la configuration du cœur :

- stop-loss obligatoire et du bon côté ;
- volume <= ``max_volume`` ; positions ouvertes <= ``max_positions`` ;
- si le cœur ne donne plus signe de vie depuis ``core_timeout_s`` : AUCUNE nouvelle
  entrée (les fermetures restent autorisées). Les positions existantes gardent
  leurs SL/TP chez le broker.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime

from tradebot.bridge import protocol as P
from tradebot.bridge.transport import Transport
from tradebot.core.logging import get_logger
from tradebot.core.models import ExitReason, OrderIntent, Side
from tradebot.execution.base import OrderResult
from tradebot.execution.mt5_adapter import MT5Adapter

log = get_logger(__name__)


@dataclass(frozen=True)
class BridgeGuard:
    max_volume: float = 1.0
    max_positions: int = 2
    core_timeout_s: float = 90.0


class BridgeServer:
    def __init__(self, adapter: MT5Adapter, transport: Transport, guard: BridgeGuard,
                 clock=time.time) -> None:
        self.a = adapter
        self.t = transport
        self.guard = guard
        self.clock = clock
        self.last_bar_ts: datetime | None = None
        self._next_history = 0.0

    def publish_history(self, count: int = 15_000) -> None:
        """Historique pour le préchauffage des indicateurs au démarrage du cœur
        (EMA200 H1 = ~9 jours ; 15 000 M1 ≈ 11 jours de marché)."""
        bars = self.a.completed_m1_bars(count)
        self.t.set("history", P.dumps("history", [P.encode(b) for b in bars], at=self.clock()))

    def core_alive(self) -> bool:
        hb = self.t.get("core_hb")
        return hb is not None and self.clock() - float(hb) < self.guard.core_timeout_s

    def check_intent(self, i: OrderIntent) -> str | None:
        if not i.stop_loss:
            return "refus pont : pas de stop-loss"
        tick = self.a.mt5.symbol_info_tick(self.a.symbol)
        ref = tick.ask if i.side is Side.BUY else tick.bid
        if (i.stop_loss - ref) * i.side.sign >= 0:
            return "refus pont : stop du mauvais côté du prix"
        if i.volume > self.guard.max_volume:
            return f"refus pont : volume {i.volume} > {self.guard.max_volume}"
        if len(self.a.positions()) >= self.guard.max_positions:
            return "refus pont : trop de positions ouvertes"
        if not self.core_alive():
            return "refus pont : cœur injoignable (heartbeat périmé)"
        return None

    def handle(self, msg: dict) -> OrderResult:
        kind, payload = msg["kind"], msg["payload"]
        now = datetime.now(UTC)
        if kind == "submit":
            intent = P.decode(OrderIntent, payload)
            if (why := self.check_intent(intent)) is not None:
                log.warning("bridge_guard_reject", reason=why)
                return OrderResult(False, intent.client_order_id, why)
            return self.a.submit(intent)
        if kind == "close":
            return self.a.close_position(payload["position_id"], ExitReason(payload["reason"]), now)
        if kind == "close_all":
            results = self.a.close_all(ExitReason(payload["reason"]), now)
            ok = all(r.ok for r in results)
            return OrderResult(ok, "", f"{len(results)} position(s) : {'ok' if ok else 'ÉCHECS'}")
        return OrderResult(False, "", f"commande inconnue : {kind}")

    def publish_state(self) -> None:
        bars = self.a.completed_m1_bars(120)
        for b in bars:
            if self.last_bar_ts is None or b.ts > self.last_bar_ts:
                self.t.push("bars", P.dumps("bar", P.encode(b)))
                self.last_bar_ts = b.ts
        self.t.set("account", P.dumps("account", P.encode(self.a.account()), at=self.clock()))
        self.t.set("positions", P.dumps("positions", [P.encode(p) for p in self.a.positions()],
                                        at=self.clock()))
        for tr in self.a.drain_closed_trades():
            self.t.push("trades", P.dumps("trade", P.encode(tr)))
        self.t.set("bridge_hb", str(self.clock()), ttl_s=600)

    def step(self, cmd_wait_s: float = 1.0) -> None:
        if self.clock() >= self._next_history:
            self.publish_history()
            self._next_history = self.clock() + 1800
        self.publish_state()
        deadline = time.monotonic() + cmd_wait_s
        while (remaining := deadline - time.monotonic()) > 0:
            raw = self.t.pop("cmd", timeout=remaining)
            if raw is None:
                break
            msg = P.loads(raw)
            try:
                res = self.handle(msg)
            except Exception as e:  # le pont ne doit jamais mourir sur une commande
                log.exception("bridge_command_error")
                res = OrderResult(False, "", f"erreur pont : {e}")
            self.t.push(f"reply:{msg['id']}", P.dumps("result", P.encode_result(res)))

    def run_forever(self, cycle_s: float = 2.0) -> None:  # pragma: no cover - boucle infinie
        log.info("bridge_started", symbol=self.a.symbol, guard=self.guard)
        while True:
            try:
                self.step(cmd_wait_s=cycle_s)
            except Exception:
                log.exception("bridge_cycle_error")
                time.sleep(5)
