from __future__ import annotations

from typing import Any

from tradebot.strategy.asian_breakout import AsianBreakout
from tradebot.strategy.base import Strategy
from tradebot.strategy.trend_pullback import TrendPullback

STRATEGIES: dict[str, type[Strategy]] = {
    AsianBreakout.name: AsianBreakout,
    TrendPullback.name: TrendPullback,
}


def build_strategy(name: str, symbol: str, params: dict[str, Any] | None = None) -> Strategy:
    try:
        cls = STRATEGIES[name]
    except KeyError:
        raise ValueError(f"stratégie inconnue : {name} (dispo : {sorted(STRATEGIES)})") from None
    return cls(symbol, **(params or {}))
