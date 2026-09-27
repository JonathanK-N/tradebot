from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tradebot.core.config import AppConfig, CostConfig
from tradebot.core.instrument import XAUUSD
from tradebot.core.models import Bar


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


def make_bar(ts: datetime, o: float, h: float | None = None, lo: float | None = None,
             c: float | None = None, spread: float = 0.2) -> Bar:
    c = o if c is None else c
    h = max(o, c) if h is None else h
    lo = min(o, c) if lo is None else lo
    return Bar(ts, o, h, lo, c, spread, 1.0)


def flat_bars(start: datetime, n: int, price: float = 2000.0, spread: float = 0.2) -> list[Bar]:
    return [make_bar(start + timedelta(minutes=i), price, spread=spread) for i in range(n)]


@pytest.fixture
def cfg() -> AppConfig:
    return AppConfig()


@pytest.fixture
def zero_costs() -> CostConfig:
    return CostConfig(extra_spread=0, commission_per_lot_side=0, entry_slippage=0, stop_slippage=0,
                      swap_long_per_lot=0, swap_short_per_lot=0)


@pytest.fixture
def inst():
    return XAUUSD
