"""Objets métier partagés par tous les modules.

Ce sont des dataclasses immuables (rapides dans les boucles de backtest, sûres
à passer entre modules). Toute date est un ``datetime`` *aware* en UTC.

Chaîne de traçabilité :  Bar -> Signal -> RiskDecision -> OrderIntent -> Fill -> Trade
Le ``correlation_id`` du Signal est propagé jusqu'au Trade.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"

    @property
    def sign(self) -> int:
        return 1 if self is Side.BUY else -1

    @property
    def opposite(self) -> Side:
        return Side.SELL if self is Side.BUY else Side.BUY


class ExitReason(StrEnum):
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"
    TIME_EXIT = "time_exit"
    KILL_SWITCH = "kill_switch"
    MANUAL = "manual"
    END_OF_DATA = "end_of_data"


def new_id() -> str:
    return uuid.uuid4().hex[:16]


@dataclass(frozen=True, slots=True)
class Bar:
    """Bougie OHLC en prix BID + spread moyen (ask ≈ bid + spread).

    ``ts`` est l'heure d'OUVERTURE de la bougie. Une bougie n'est exploitable
    par une stratégie qu'une fois close, c.-à-d. à ``ts + timeframe``.
    """

    ts: datetime
    open: float
    high: float
    low: float
    close: float
    spread: float = 0.0
    volume: float = 0.0

    @property
    def ask_open(self) -> float:
        return self.open + self.spread

    @property
    def ask_high(self) -> float:
        return self.high + self.spread

    @property
    def ask_low(self) -> float:
        return self.low + self.spread

    @property
    def ask_close(self) -> float:
        return self.close + self.spread


@dataclass(frozen=True, slots=True)
class Signal:
    """Intention de la stratégie, AVANT tout contrôle de risque.

    ``stop_loss`` est obligatoire : un signal sans invalidation est rejeté.
    ``reason`` est une justification lisible par un humain (journal, Telegram).
    """

    ts: datetime
    symbol: str
    side: Side
    entry_ref: float
    stop_loss: float
    take_profit: float | None
    strategy: str
    reason: str
    expire_after: timedelta | None = None
    features: dict[str, float] = field(default_factory=dict)
    correlation_id: str = field(default_factory=new_id)

    @property
    def stop_distance(self) -> float:
        return abs(self.entry_ref - self.stop_loss)


@dataclass(frozen=True, slots=True)
class OrderIntent:
    """Ordre approuvé et dimensionné par le module de risque."""

    ts: datetime
    symbol: str
    side: Side
    volume: float
    stop_loss: float
    take_profit: float | None
    strategy: str
    correlation_id: str
    expire_at: datetime | None = None
    risk_amount: float = 0.0
    client_order_id: str = field(default_factory=new_id)


@dataclass(frozen=True, slots=True)
class RiskDecision:
    approved: bool
    signal: Signal
    reasons: tuple[str, ...]
    intent: OrderIntent | None = None


@dataclass(frozen=True, slots=True)
class Fill:
    ts: datetime
    client_order_id: str
    position_id: str
    symbol: str
    side: Side
    volume: float
    price: float
    commission: float
    slippage: float
    is_entry: bool
    correlation_id: str = ""


@dataclass(slots=True)
class Position:
    position_id: str
    symbol: str
    side: Side
    volume: float
    entry_price: float
    entry_ts: datetime
    stop_loss: float
    take_profit: float | None
    strategy: str
    correlation_id: str
    client_order_id: str
    expire_at: datetime | None = None
    initial_risk: float = 0.0
    swap_accrued: float = 0.0
    commission_paid: float = 0.0


@dataclass(frozen=True, slots=True)
class Trade:
    """Position clôturée. ``pnl`` est net (commissions et swaps inclus)."""

    position_id: str
    symbol: str
    side: Side
    volume: float
    entry_ts: datetime
    entry_price: float
    exit_ts: datetime
    exit_price: float
    exit_reason: ExitReason
    gross_pnl: float
    commission: float
    swap: float
    pnl: float
    initial_risk: float
    strategy: str
    correlation_id: str

    @property
    def r_multiple(self) -> float:
        """Résultat exprimé en multiples du risque initial (R)."""
        return self.pnl / self.initial_risk if self.initial_risk > 0 else 0.0


@dataclass(frozen=True, slots=True)
class AccountState:
    balance: float
    equity: float
    margin_used: float = 0.0
    currency: str = "USD"

    @property
    def free_margin(self) -> float:
        return self.equity - self.margin_used


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    """Ce que le module de risque sait du marché au moment de décider."""

    ts: datetime
    bid: float
    spread: float
    median_spread: float | None = None
