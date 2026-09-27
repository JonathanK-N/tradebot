"""Walk-forward : optimiser sur le passé, juger UNIQUEMENT sur la période suivante.

    |---- train 12 mois ----|-- test 3 mois --|
                  |---- train 12 mois ----|-- test 3 mois --|   ...

Seuls les trades des fenêtres de TEST (hors échantillon) comptent pour le verdict.
Walk-Forward Efficiency (WFE) = espérance OOS / espérance IS. Une WFE < 0,5
signifie que l'optimisation « apprend le bruit ».

Chaque fenêtre de test est précédée d'un préchauffage (indicateurs) dont les
trades sont exclus.
"""

from __future__ import annotations

import itertools
import math
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from tradebot.backtest.runner import run_backtest
from tradebot.core.config import AppConfig
from tradebot.core.models import Trade
from tradebot.fundamental.calendar import EconomicEvent


def param_grid(grid: dict[str, list[Any]]) -> list[dict[str, Any]]:
    keys = list(grid)
    return [dict(zip(keys, combo, strict=True)) for combo in itertools.product(*grid.values())]


def objective(trades: list[Trade], min_trades: int) -> float:
    """Espérance en R pénalisée par la taille d'échantillon (≈ t-stat).
    Favorise les résultats à la fois positifs ET réguliers, pas les coups de chance."""
    if len(trades) < min_trades:
        return -math.inf
    r = np.array([t.r_multiple for t in trades])
    sd = r.std(ddof=1)
    return float(r.mean() / sd * math.sqrt(len(r))) if sd > 0 else 0.0


@dataclass
class WindowResult:
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_end: pd.Timestamp
    best_params: dict[str, Any]
    is_score: float
    is_expectancy_r: float
    oos_trades: list[Trade] = field(repr=False)

    @property
    def oos_expectancy_r(self) -> float:
        return float(np.mean([t.r_multiple for t in self.oos_trades])) if self.oos_trades else 0.0


@dataclass
class WalkForwardResult:
    strategy: str
    windows: list[WindowResult]

    @property
    def oos_trades(self) -> list[Trade]:
        return [t for w in self.windows for t in w.oos_trades]

    @property
    def oos_expectancy_r(self) -> float:
        tr = self.oos_trades
        return float(np.mean([t.r_multiple for t in tr])) if tr else 0.0

    @property
    def is_expectancy_r(self) -> float:
        vals = [w.is_expectancy_r for w in self.windows if np.isfinite(w.is_expectancy_r)]
        return float(np.mean(vals)) if vals else 0.0

    @property
    def wfe(self) -> float:
        return self.oos_expectancy_r / self.is_expectancy_r if self.is_expectancy_r > 0 else 0.0

    def to_markdown(self) -> str:
        tr = self.oos_trades
        r = np.array([t.r_multiple for t in tr]) if tr else np.array([0.0])
        t_stat = float(r.mean() / (r.std(ddof=1) / math.sqrt(len(r)))) if len(r) > 2 and r.std() > 0 else 0.0
        lines = [
            f"# Walk-forward — {self.strategy}",
            "",
            f"- Fenêtres : {len(self.windows)}",
            f"- Trades hors échantillon : {len(tr)}",
            f"- Espérance OOS : {self.oos_expectancy_r:+.3f} R (t = {t_stat:.2f})",
            f"- Espérance IS moyenne : {self.is_expectancy_r:+.3f} R",
            f"- WFE : {self.wfe:.2f}  (critère : >= 0,5)",
            f"- Fenêtres OOS positives : {sum(w.oos_expectancy_r > 0 for w in self.windows)}"
            f"/{len(self.windows)}",
            "",
            "| Test jusqu'au | Paramètres retenus | IS (R) | OOS (R) | Trades OOS |",
            "|---|---|---|---|---|",
        ]
        for w in self.windows:
            lines.append(f"| {w.test_end:%Y-%m-%d} | `{w.best_params}` | {w.is_expectancy_r:+.3f} | "
                         f"{w.oos_expectancy_r:+.3f} | {len(w.oos_trades)} |")
        go = self.wfe >= 0.5 and self.oos_expectancy_r > 0 and len(tr) >= 200 and t_stat >= 2
        lines += ["", f"**Verdict : {'✅ GO (étape suivante : Monte Carlo)' if go else '❌ NO-GO'}**"]
        return "\n".join(lines)


def _eval(args: tuple) -> tuple[dict[str, Any], float, float]:
    cfg, bars, strategy, params, events, min_trades = args
    res = run_backtest(cfg, bars, strategy=strategy, params=params, events=events)
    return params, objective(res.trades, min_trades), res.metrics.expectancy_r


def walk_forward(
    cfg: AppConfig,
    bars: pd.DataFrame,
    strategy: str,
    grid: dict[str, list[Any]],
    *,
    train_months: int = 12,
    test_months: int = 3,
    warmup_days: int = 30,
    min_trades: int = 30,
    events: list[EconomicEvent] | None = None,
    workers: int = 1,
) -> WalkForwardResult:
    ts = pd.to_datetime(bars["ts"], utc=True)
    start, end = ts.min().normalize(), ts.max()
    combos = param_grid(grid)
    windows: list[WindowResult] = []
    t0 = start
    while True:
        tr_end = t0 + pd.DateOffset(months=train_months)
        te_end = tr_end + pd.DateOffset(months=test_months)
        if tr_end >= end:
            break
        train = bars[(ts >= t0) & (ts < tr_end)]
        jobs = [(cfg, train, strategy, p, events, min_trades) for p in combos]
        if workers > 1:
            with ProcessPoolExecutor(workers) as ex:
                scored = list(ex.map(_eval, jobs))
        else:
            scored = [_eval(j) for j in jobs]
        best_params, best_score, best_exp = max(scored, key=lambda x: x[1])
        if not np.isfinite(best_score):  # aucun jeu de paramètres n'a assez de trades
            best_params = combos[0]
        test = bars[(ts >= tr_end - pd.Timedelta(days=warmup_days)) & (ts < te_end)]
        res = run_backtest(cfg, test, strategy=strategy, params=best_params, events=events)
        oos = [t for t in res.trades if pd.Timestamp(t.entry_ts) >= tr_end]
        windows.append(WindowResult(t0, tr_end, min(te_end, end), best_params, best_score, best_exp, oos))
        t0 = t0 + pd.DateOffset(months=test_months)
    return WalkForwardResult(strategy, windows)
