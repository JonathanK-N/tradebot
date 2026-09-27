"""Exécution d'un backtest : assemble les MÊMES composants que le live.

Seules différences avec le réel : SimBroker au lieu de MT5, bougies historiques
au lieu du flux, état du risque en mémoire, calendrier historique (non fail-closed).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from tradebot.backtest.metrics import Metrics, compute_metrics
from tradebot.core.config import AppConfig
from tradebot.core.engine import EngineStats, TradingEngine
from tradebot.core.instrument import InstrumentSpec
from tradebot.core.models import Trade
from tradebot.data.store import iter_bars
from tradebot.execution.sim import SimBroker
from tradebot.fundamental.calendar import EconomicEvent, EventCalendar
from tradebot.journal.store import Journal
from tradebot.risk.manager import RiskManager
from tradebot.risk.state import MemoryStateStore
from tradebot.strategy.registry import build_strategy


def instrument_from(cfg: AppConfig) -> InstrumentSpec:
    i = cfg.instrument
    return InstrumentSpec(i.symbol, i.contract_size, i.tick_size, i.volume_min, i.volume_step,
                          i.volume_max, i.leverage)


@dataclass
class BacktestResult:
    strategy: str
    params: dict[str, Any]
    metrics: Metrics
    trades: list[Trade]
    equity: pd.Series
    stats: EngineStats
    journal: Journal = field(repr=False)


def run_backtest(
    cfg: AppConfig,
    bars: pd.DataFrame,
    *,
    strategy: str | None = None,
    params: dict[str, Any] | None = None,
    events: list[EconomicEvent] | None = None,
) -> BacktestResult:
    name = strategy or cfg.strategy.name
    # les paramètres du YAML ne s'appliquent qu'à la stratégie configurée
    base = cfg.strategy.params if name == cfg.strategy.name else {}
    p = {**base, **(params or {})}
    inst = instrument_from(cfg)
    strat = build_strategy(name, inst.symbol, p)
    broker = SimBroker(inst, cfg.costs, cfg.account.initial_balance)
    risk = RiskManager(cfg.risk, inst, MemoryStateStore(), cfg.account.initial_balance)
    calendar = EventCalendar(cfg.news, events or [], require_fresh=False)
    journal = Journal()
    engine = TradingEngine(strat, risk, broker, calendar, journal, extra_spread=cfg.costs.extra_spread)

    for bar in iter_bars(bars):
        engine.on_m1_bar(bar)
    engine.finish()

    if journal.equity:
        eq = pd.Series([e[2] for e in journal.equity], index=pd.DatetimeIndex([e[0] for e in journal.equity]))
    else:
        eq = pd.Series([cfg.account.initial_balance], index=pd.DatetimeIndex([pd.Timestamp.now(tz="UTC")]))
    # point final après clôture des positions restantes
    if engine.last_bar is not None:
        eq.loc[pd.Timestamp(engine.last_bar.ts) + pd.Timedelta(minutes=1)] = broker.balance
    metrics = compute_metrics(journal.trades, eq, cfg.account.initial_balance)
    return BacktestResult(name, strat.params, metrics, journal.trades, eq, engine.stats, journal)
