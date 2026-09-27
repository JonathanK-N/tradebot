"""Abstraction du broker : le reste du système ne sait pas s'il parle à MT5,
à un simulateur, ou à un pont distant. Changer de broker = écrire un adaptateur.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from tradebot.core.models import AccountState, Bar, ExitReason, Fill, OrderIntent, Position, Trade


@dataclass(frozen=True, slots=True)
class OrderResult:
    ok: bool
    client_order_id: str
    message: str = ""
    fill: Fill | None = None
    pending: bool = False  # accepté, exécution différée (simulateur : ouverture suivante)
    retryable: bool = False


class BrokerAdapter(ABC):
    name: str = "abstract"

    @abstractmethod
    def account(self) -> AccountState: ...

    @abstractmethod
    def positions(self) -> list[Position]: ...

    @abstractmethod
    def submit(self, intent: OrderIntent) -> OrderResult: ...

    @abstractmethod
    def close_position(self, position_id: str, reason: ExitReason, ts: datetime) -> OrderResult: ...

    def on_bar(self, bar: Bar) -> None:  # noqa: B027 — utile au simulateur seulement
        """Le simulateur s'en sert pour exécuter ordres / stops ; no-op en réel."""

    @abstractmethod
    def drain_closed_trades(self) -> list[Trade]:
        """Trades clôturés depuis le dernier appel (SL/TP côté broker inclus)."""

    def close_all(self, reason: ExitReason, ts: datetime) -> list[OrderResult]:
        return [self.close_position(p.position_id, reason, ts) for p in self.positions()]
