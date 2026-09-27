"""Broker simulé, volontairement PESSIMISTE.

Hypothèses d'exécution (chacune défavorable ou neutre pour la stratégie) :
- Ordre au marché : exécuté à l'OUVERTURE de la minute suivante, au prix ask (achat)
  ou bid (vente), + spread additionnel broker + slippage.
- Stop : déclenché si le prix de sortie (bid pour un long, ask pour un short)
  l'atteint ; exécuté au pire du stop et de l'ouverture (gap) - slippage.
- Take-profit : exécuté exactement au niveau (aucune amélioration supposée).
- Stop ET TP touchés dans la même minute : on suppose le STOP (pessimiste).
- Swaps débités à chaque passage de 17:00 NY ; triple le jour configuré.
- Si le prix d'ouverture a déjà franchi le stop au moment de l'exécution,
  l'ordre est rejeté (MT5 refuserait un stop invalide).
"""

from __future__ import annotations

from datetime import datetime

from tradebot.core.config import CostConfig
from tradebot.core.instrument import InstrumentSpec
from tradebot.core.models import (
    AccountState,
    Bar,
    ExitReason,
    Fill,
    OrderIntent,
    Position,
    Side,
    Trade,
    new_id,
)
from tradebot.core.sessions import trading_day
from tradebot.execution.base import BrokerAdapter, OrderResult


class SimBroker(BrokerAdapter):
    name = "sim"

    def __init__(self, instrument: InstrumentSpec, costs: CostConfig, initial_balance: float) -> None:
        self.inst = instrument
        self.costs = costs
        self.balance = initial_balance
        self._positions: dict[str, Position] = {}
        self._pending: list[OrderIntent] = []
        self._closed: list[Trade] = []
        self._fills: list[Fill] = []
        self._last_bar: Bar | None = None
        self.rejected: list[tuple[OrderIntent, str]] = []

    # ------------------------------------------------------------ interface
    def account(self) -> AccountState:
        equity = self.balance
        margin = 0.0
        if self._last_bar is not None:
            for p in self._positions.values():
                equity += self._unrealized(p, self._last_bar) + p.swap_accrued - p.commission_paid
                margin += self.inst.margin_required(p.entry_price, p.volume)
        return AccountState(balance=self.balance, equity=equity, margin_used=margin)

    def positions(self) -> list[Position]:
        return list(self._positions.values())

    def submit(self, intent: OrderIntent) -> OrderResult:
        self._pending.append(intent)
        return OrderResult(True, intent.client_order_id, "en attente d'exécution", pending=True)

    def close_position(self, position_id: str, reason: ExitReason, ts: datetime) -> OrderResult:
        p = self._positions.get(position_id)
        if p is None or self._last_bar is None:
            return OrderResult(False, "", "position inconnue")
        bar = self._last_bar
        spread = bar.spread + self.costs.extra_spread
        # clôture au marché au dernier prix connu (close de la minute), slippage défavorable
        if p.side is Side.BUY:
            price = bar.close - self.costs.entry_slippage
        else:
            price = bar.close + spread + self.costs.entry_slippage
        self._close(p, price, ts, reason)
        return OrderResult(True, p.client_order_id, f"clôturée ({reason})")

    def drain_closed_trades(self) -> list[Trade]:
        out, self._closed = self._closed, []
        return out

    @property
    def fills(self) -> list[Fill]:
        return self._fills

    # ------------------------------------------------------------ simulation
    def on_bar(self, bar: Bar) -> None:
        prev = self._last_bar
        if prev is not None and trading_day(prev.ts) != trading_day(bar.ts):
            self._apply_swaps(prev.ts)
        self._last_bar = bar
        self._fill_pending(bar)
        for p in list(self._positions.values()):
            self._check_exit(p, bar)

    def _spread(self, bar: Bar) -> float:
        return bar.spread + self.costs.extra_spread

    def _fill_pending(self, bar: Bar) -> None:
        pending, self._pending = self._pending, []
        for intent in pending:
            spread = self._spread(bar)
            slip = self.costs.entry_slippage
            if intent.side is Side.BUY:
                price = bar.open + spread + slip
                invalid = price <= intent.stop_loss
            else:
                price = bar.open - slip
                invalid = price >= intent.stop_loss
            if invalid:
                self.rejected.append((intent, "stop déjà franchi à l'exécution"))
                continue
            commission = self.costs.commission_per_lot_side * intent.volume
            pos = Position(
                position_id=new_id(),
                symbol=intent.symbol,
                side=intent.side,
                volume=intent.volume,
                entry_price=price,
                entry_ts=bar.ts,
                stop_loss=intent.stop_loss,
                take_profit=intent.take_profit,
                strategy=intent.strategy,
                correlation_id=intent.correlation_id,
                client_order_id=intent.client_order_id,
                expire_at=intent.expire_at,
                initial_risk=self.inst.risk_for(price - intent.stop_loss, intent.volume),
                commission_paid=commission,
            )
            self._positions[pos.position_id] = pos
            self._fills.append(Fill(bar.ts, intent.client_order_id, pos.position_id, intent.symbol,
                                    intent.side, intent.volume, price, commission, slip, True,
                                    intent.correlation_id))

    def _check_exit(self, p: Position, bar: Bar) -> None:
        spread = self._spread(bar)
        slip = self.costs.stop_slippage
        if p.expire_at is not None and bar.ts >= p.expire_at:
            if p.side is Side.BUY:
                price = bar.open - self.costs.entry_slippage
            else:
                price = bar.open + spread + self.costs.entry_slippage
            self._close(p, price, bar.ts, ExitReason.TIME_EXIT)
            return
        if p.side is Side.BUY:
            o, h, lo = bar.open, bar.high, bar.low  # sortie au bid
            if lo <= p.stop_loss:
                price = (min(o, p.stop_loss)) - slip
                self._close(p, price, bar.ts, ExitReason.STOP_LOSS)
            elif p.take_profit is not None and h >= p.take_profit:
                self._close(p, p.take_profit, bar.ts, ExitReason.TAKE_PROFIT)
        else:
            o, h, lo = bar.open + spread, bar.high + spread, bar.low + spread  # sortie à l'ask
            if h >= p.stop_loss:
                price = max(o, p.stop_loss) + slip
                self._close(p, price, bar.ts, ExitReason.STOP_LOSS)
            elif p.take_profit is not None and lo <= p.take_profit:
                self._close(p, p.take_profit, bar.ts, ExitReason.TAKE_PROFIT)

    def _apply_swaps(self, prev_ts: datetime) -> None:
        mult = 3 if trading_day(prev_ts).weekday() == self.costs.triple_swap_weekday else 1
        for p in self._positions.values():
            rate = self.costs.swap_long_per_lot if p.side is Side.BUY else self.costs.swap_short_per_lot
            p.swap_accrued += rate * p.volume * mult

    def _unrealized(self, p: Position, bar: Bar) -> float:
        if p.side is Side.BUY:
            return self.inst.pnl(bar.close - p.entry_price, p.volume)
        return self.inst.pnl(p.entry_price - (bar.close + self._spread(bar)), p.volume)

    def _close(self, p: Position, price: float, ts: datetime, reason: ExitReason) -> None:
        exit_comm = self.costs.commission_per_lot_side * p.volume
        gross = self.inst.pnl((price - p.entry_price) * p.side.sign, p.volume)
        commission = p.commission_paid + exit_comm
        pnl = gross - commission + p.swap_accrued
        self.balance += pnl
        del self._positions[p.position_id]
        self._fills.append(Fill(ts, p.client_order_id, p.position_id, p.symbol, p.side.opposite,
                                p.volume, price, exit_comm, 0.0, False, p.correlation_id))
        self._closed.append(Trade(
            position_id=p.position_id, symbol=p.symbol, side=p.side, volume=p.volume,
            entry_ts=p.entry_ts, entry_price=p.entry_price, exit_ts=ts, exit_price=price,
            exit_reason=reason, gross_pnl=gross, commission=commission, swap=p.swap_accrued,
            pnl=pnl, initial_risk=p.initial_risk, strategy=p.strategy,
            correlation_id=p.correlation_id,
        ))
