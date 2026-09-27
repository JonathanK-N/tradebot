"""Simulateur : trades vérifiés À LA MAIN (critère go/no-go semaine 5)."""

from datetime import timedelta

import pytest

from tests.conftest import make_bar, utc
from tradebot.core.config import CostConfig
from tradebot.core.instrument import XAUUSD
from tradebot.core.models import ExitReason, OrderIntent, Side
from tradebot.execution.sim import SimBroker

T = utc(2024, 6, 11, 9, 0)


def intent(side=Side.BUY, sl=1990.0, tp=2020.0, vol=0.10, expire=None):
    return OrderIntent(T, "XAUUSD", side, vol, sl, tp, "t", "cid", expire_at=expire)


def test_long_take_profit_hand_calculated(zero_costs):
    b = SimBroker(XAUUSD, zero_costs, 10_000)
    b.submit(intent())
    b.on_bar(make_bar(T, 2000, spread=0.3))  # entrée à l'ask = 2000.3
    b.on_bar(make_bar(T + timedelta(minutes=1), 2010, 2021, 2009, 2020))
    [t] = b.drain_closed_trades()
    assert t.entry_price == pytest.approx(2000.3)
    assert t.exit_reason is ExitReason.TAKE_PROFIT and t.exit_price == 2020
    assert t.pnl == pytest.approx((2020 - 2000.3) * 0.10 * 100)  # 197 USD
    assert b.balance == pytest.approx(10_197)


def test_short_stop_uses_ask_and_costs():
    costs = CostConfig(extra_spread=0.0, commission_per_lot_side=5, entry_slippage=0.1,
                       stop_slippage=0.2, swap_long_per_lot=0, swap_short_per_lot=0)
    b = SimBroker(XAUUSD, costs, 10_000)
    b.submit(intent(Side.SELL, sl=2010, tp=1980))
    b.on_bar(make_bar(T, 2000, spread=0.3))  # entrée au bid - slippage = 1999.9
    # le bid monte à 2009.8 : l'ask (2010.1) touche le stop
    b.on_bar(make_bar(T + timedelta(minutes=1), 2005, 2009.8, 2004, 2009, spread=0.3))
    [t] = b.drain_closed_trades()
    assert t.exit_reason is ExitReason.STOP_LOSS
    assert t.exit_price == pytest.approx(2010.2)
    gross = (1999.9 - 2010.2) * 0.1 * 100
    assert t.pnl == pytest.approx(gross - 2 * 5 * 0.1)


def test_gap_through_stop_fills_at_open(zero_costs):
    b = SimBroker(XAUUSD, zero_costs, 10_000)
    b.submit(intent(sl=1990))
    b.on_bar(make_bar(T, 2000, spread=0))
    b.on_bar(make_bar(T + timedelta(minutes=1), 1980, 1985, 1975, 1982, spread=0))  # gap
    [t] = b.drain_closed_trades()
    assert t.exit_price == 1980  # pire que le stop : réaliste


def test_stop_and_tp_same_bar_assumes_stop(zero_costs):
    b = SimBroker(XAUUSD, zero_costs, 10_000)
    b.submit(intent(sl=1995, tp=2005))
    b.on_bar(make_bar(T, 2000, spread=0))
    b.on_bar(make_bar(T + timedelta(minutes=1), 2000, 2006, 1994, 2000, spread=0))
    [t] = b.drain_closed_trades()
    assert t.exit_reason is ExitReason.STOP_LOSS


def test_order_rejected_if_open_beyond_stop(zero_costs):
    b = SimBroker(XAUUSD, zero_costs, 10_000)
    b.submit(intent(sl=1990))
    b.on_bar(make_bar(T, 1985, spread=0))
    assert not b.positions() and b.rejected


def test_time_exit(zero_costs):
    b = SimBroker(XAUUSD, zero_costs, 10_000)
    b.submit(intent(expire=T + timedelta(minutes=2)))
    for i in range(4):
        b.on_bar(make_bar(T + timedelta(minutes=i), 2000, spread=0))
    [t] = b.drain_closed_trades()
    assert t.exit_reason is ExitReason.TIME_EXIT


def test_swaps_triple_wednesday():
    costs = CostConfig(extra_spread=0, commission_per_lot_side=0, entry_slippage=0, stop_slippage=0,
                       swap_long_per_lot=-10, swap_short_per_lot=0, triple_swap_weekday=2)
    b = SimBroker(XAUUSD, costs, 10_000)
    tue = utc(2024, 6, 11, 20, 0)  # mardi 16:00 NY
    b.submit(OrderIntent(tue, "XAUUSD", Side.BUY, 1.0, 1900, 2100, "t", "c"))
    b.on_bar(make_bar(tue, 2000, spread=0))
    b.on_bar(make_bar(utc(2024, 6, 11, 22, 0), 2000, spread=0))  # passe le rollover de mardi
    b.on_bar(make_bar(utc(2024, 6, 12, 22, 0), 2000, spread=0))  # passe celui de mercredi (x3)
    assert b.positions()[0].swap_accrued == pytest.approx(-10 - 30)
