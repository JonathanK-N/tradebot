"""Métriques de performance professionnelles.

Lecture honnête :
- Moins de ~100 trades : les métriques sont très bruitées, on ne conclut rien.
- La t-stat de l'espérance (en R) teste « espérance > 0 ». t < 2 : non significatif.
  Et même t > 2 ne protège pas du data snooping si on a testé 50 variantes.
- Le Sharpe est calculé sur les rendements QUOTIDIENS de l'equity (√252).
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

from tradebot.core.models import Trade
from tradebot.core.sessions import session_of


@dataclass
class Metrics:
    n_trades: int
    win_rate: float
    avg_win: float
    avg_loss: float
    profit_factor: float
    expectancy: float  # par trade, en devise
    expectancy_r: float
    t_stat_r: float
    total_pnl: float
    total_return_pct: float
    cagr_pct: float
    max_drawdown_pct: float
    max_drawdown_days: float
    sharpe: float
    sortino: float
    mar: float
    exposure_pct: float
    avg_hold_hours: float
    total_commission: float
    total_swap: float
    expectancy_r_ci95: tuple[float, float] = (0.0, 0.0)
    by_session: dict[str, dict[str, float]] = field(default_factory=dict)
    by_exit: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _max_drawdown(equity: pd.Series) -> tuple[float, float]:
    if equity.empty:
        return 0.0, 0.0
    peak = equity.cummax()
    dd = (equity - peak) / peak
    max_dd = float(-dd.min() * 100)
    # durée max sous l'eau
    under = dd < 0
    longest = 0.0
    start: datetime | None = None
    for ts, u in under.items():
        if u and start is None:
            start = ts  # type: ignore[assignment]
        elif not u and start is not None:
            longest = max(longest, (ts - start).total_seconds() / 86400)  # type: ignore[operator]
            start = None
    if start is not None:
        longest = max(longest, (equity.index[-1] - start).total_seconds() / 86400)
    return max_dd, longest


def bootstrap_ci(values: np.ndarray, n: int = 2000, seed: int = 7) -> tuple[float, float]:
    if len(values) < 2:
        return (0.0, 0.0)
    rng = np.random.default_rng(seed)
    means = rng.choice(values, size=(n, len(values)), replace=True).mean(axis=1)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def compute_metrics(trades: list[Trade], equity: pd.Series, initial: float) -> Metrics:
    pnl = np.array([t.pnl for t in trades], dtype=float)
    rs = np.array([t.r_multiple for t in trades], dtype=float)
    wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
    n = len(trades)
    gross_win, gross_loss = wins.sum(), -losses.sum()
    pf = float(gross_win / gross_loss) if gross_loss > 0 else (math.inf if gross_win > 0 else 0.0)
    t_stat = float(rs.mean() / (rs.std(ddof=1) / math.sqrt(n))) if n > 2 and rs.std(ddof=1) > 0 else 0.0

    equity = equity.sort_index()
    daily = equity.resample("1D").last().dropna()
    rets = daily.pct_change().dropna()
    sharpe = float(rets.mean() / rets.std() * math.sqrt(252)) if len(rets) > 2 and rets.std() > 0 else 0.0
    downside = rets[rets < 0]
    sortino = (
        float(rets.mean() / downside.std() * math.sqrt(252))
        if len(downside) > 2 and downside.std() > 0 else 0.0
    )
    final = float(equity.iloc[-1]) if not equity.empty else initial
    years = max((equity.index[-1] - equity.index[0]).total_seconds() / (365.25 * 86400), 1e-9) \
        if len(equity) > 1 else 1e-9
    cagr = ((final / initial) ** (1 / years) - 1) * 100 if final > 0 and years > 0.05 else 0.0
    max_dd, dd_days = _max_drawdown(equity)

    held = sum((t.exit_ts - t.entry_ts).total_seconds() for t in trades)
    span = (equity.index[-1] - equity.index[0]).total_seconds() if len(equity) > 1 else 0
    by_session: dict[str, list[float]] = defaultdict(list)
    by_exit: dict[str, int] = defaultdict(int)
    for t in trades:
        by_session[session_of(t.entry_ts).value].append(t.r_multiple)
        by_exit[t.exit_reason.value] += 1

    return Metrics(
        n_trades=n,
        win_rate=float(len(wins) / n * 100) if n else 0.0,
        avg_win=float(wins.mean()) if len(wins) else 0.0,
        avg_loss=float(losses.mean()) if len(losses) else 0.0,
        profit_factor=pf,
        expectancy=float(pnl.mean()) if n else 0.0,
        expectancy_r=float(rs.mean()) if n else 0.0,
        t_stat_r=t_stat,
        total_pnl=float(pnl.sum()),
        total_return_pct=(final / initial - 1) * 100,
        cagr_pct=cagr,
        max_drawdown_pct=max_dd,
        max_drawdown_days=dd_days,
        sharpe=sharpe,
        sortino=sortino,
        mar=cagr / max_dd if max_dd > 0 else 0.0,
        exposure_pct=held / span * 100 if span else 0.0,
        avg_hold_hours=held / n / 3600 if n else 0.0,
        total_commission=float(sum(t.commission for t in trades)),
        total_swap=float(sum(t.swap for t in trades)),
        expectancy_r_ci95=bootstrap_ci(rs),
        by_session={k: {"n": len(v), "expectancy_r": float(np.mean(v))} for k, v in by_session.items()},
        by_exit=dict(by_exit),
    )


def trades_frame(trades: list[Trade]) -> pd.DataFrame:
    rows = []
    for t in trades:
        rows.append({
            "entry_ts": t.entry_ts, "exit_ts": t.exit_ts, "side": t.side.value, "volume": t.volume,
            "entry": t.entry_price, "exit": t.exit_price, "reason": t.exit_reason.value,
            "pnl": t.pnl, "r": t.r_multiple, "commission": t.commission, "swap": t.swap,
            "strategy": t.strategy,
        })
    return pd.DataFrame(rows)
