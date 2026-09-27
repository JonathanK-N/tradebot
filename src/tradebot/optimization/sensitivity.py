"""Analyse de sensibilité : un bon paramètre est un PLATEAU, pas un pic.

Si l'espérance s'effondre quand on bouge un paramètre d'un cran, le « meilleur »
réglage est très probablement un artefact du passé (surapprentissage).

Score de stabilité = moyenne des voisins immédiats / valeur du meilleur point.
Critère : >= 0,5 (les voisins conservent au moins la moitié de la performance).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from tradebot.backtest.runner import run_backtest
from tradebot.core.config import AppConfig
from tradebot.fundamental.calendar import EconomicEvent
from tradebot.optimization.walkforward import param_grid


@dataclass
class SensitivityResult:
    table: pd.DataFrame  # une ligne par combinaison
    metric: str
    best: dict[str, Any]
    stability: float

    def to_markdown(self) -> str:
        cols = [c for c in self.table.columns if c not in ("n_trades", self.metric)]
        lines = [f"# Sensibilité ({self.metric})", "",
                 f"- Meilleur point : `{self.best}`",
                 f"- Stabilité (voisins / meilleur) : **{self.stability:.2f}** (critère >= 0,5)", "",
                 "| " + " | ".join([*cols, "trades", self.metric]) + " |",
                 "|" + "---|" * (len(cols) + 2)]
        for _, row in self.table.sort_values(cols).iterrows():
            lines.append("| " + " | ".join([*(str(row[c]) for c in cols), str(int(row["n_trades"])),
                                             f"{row[self.metric]:+.3f}"]) + " |")
        return "\n".join(lines)


def sensitivity(cfg: AppConfig, bars: pd.DataFrame, strategy: str, grid: dict[str, list[Any]],
                metric: str = "expectancy_r", events: list[EconomicEvent] | None = None) -> SensitivityResult:
    rows = []
    for p in param_grid(grid):
        m = run_backtest(cfg, bars, strategy=strategy, params=p, events=events).metrics
        rows.append({**p, "n_trades": m.n_trades, metric: getattr(m, metric)})
    table = pd.DataFrame(rows)
    best_idx = int(table[metric].idxmax())
    best = {k: table.loc[best_idx, k] for k in grid}
    # voisins : points qui diffèrent d'un cran sur exactement un paramètre
    pos = {k: {v: i for i, v in enumerate(vals)} for k, vals in grid.items()}
    neigh = []
    for i, row in table.iterrows():
        if i == best_idx:
            continue
        diffs = [abs(pos[k][row[k]] - pos[k][best[k]]) for k in grid]
        if sum(diffs) == 1:
            neigh.append(row[metric])
    best_val = float(table.loc[best_idx, metric])
    stability = float(np.mean(neigh) / best_val) if neigh and best_val > 0 else 0.0
    return SensitivityResult(table, metric, {k: (v.item() if hasattr(v, "item") else v)
                                             for k, v in best.items()}, stability)
