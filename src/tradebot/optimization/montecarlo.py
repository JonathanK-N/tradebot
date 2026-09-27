"""Monte Carlo sur la séquence des trades.

Question posée : « Avec ces mêmes trades dans un AUTRE ordre (ou un autre tirage),
quel drawdown dois-je être prêt à vivre ? ». Le drawdown historique n'est qu'UN
tirage : le 95e percentile est une bien meilleure base pour fixer les limites.

Les trades sont exprimés en R et rejoués avec un risque fixe de ``risk_pct`` %
de l'equity par trade (capitalisation composée).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class MonteCarloResult:
    n_sims: int
    n_trades: int
    risk_pct: float
    max_dd_p50: float
    max_dd_p95: float
    max_dd_p99: float
    final_return_p5: float
    final_return_p50: float
    prob_loss: float
    prob_dd_over_limit: float
    dd_limit_pct: float

    def to_markdown(self) -> str:
        ok = self.max_dd_p95 < self.dd_limit_pct
        return "\n".join([
            f"# Monte Carlo ({self.n_sims} simulations, {self.n_trades} trades, risque {self.risk_pct} %)",
            "",
            f"- Drawdown max médian : {self.max_dd_p50:.1f} %",
            f"- Drawdown max 95e percentile : **{self.max_dd_p95:.1f} %** (limite : {self.dd_limit_pct} %)",
            f"- Drawdown max 99e percentile : {self.max_dd_p99:.1f} %",
            f"- Rendement final 5e / 50e percentile : {self.final_return_p5:+.1f} % / "
            f"{self.final_return_p50:+.1f} %",
            f"- Probabilité de finir en perte : {self.prob_loss * 100:.1f} %",
            f"- Probabilité de toucher la limite de drawdown : {self.prob_dd_over_limit * 100:.1f} %",
            "",
            f"**Verdict : {'✅ GO' if ok else '❌ NO-GO — réduire le risque par trade ou revoir la stratégie'}**",
        ])


def monte_carlo(
    r_multiples: list[float] | np.ndarray,
    *,
    risk_pct: float = 0.5,
    n_sims: int = 5000,
    dd_limit_pct: float = 10.0,
    method: str = "bootstrap",  # bootstrap (avec remise) | shuffle (permutation)
    seed: int = 11,
) -> MonteCarloResult:
    r = np.asarray(r_multiples, dtype=float)
    if len(r) < 2:
        raise ValueError("au moins 2 trades nécessaires")
    rng = np.random.default_rng(seed)
    if method == "bootstrap":
        samples = rng.choice(r, size=(n_sims, len(r)), replace=True)
    elif method == "shuffle":
        samples = np.array([rng.permutation(r) for _ in range(n_sims)])
    else:
        raise ValueError(method)
    growth = np.clip(1 + samples * risk_pct / 100, 1e-9, None)
    equity = np.cumprod(growth, axis=1)
    equity = np.concatenate([np.ones((n_sims, 1)), equity], axis=1)
    peak = np.maximum.accumulate(equity, axis=1)
    max_dd = ((peak - equity) / peak).max(axis=1) * 100
    final = (equity[:, -1] - 1) * 100
    return MonteCarloResult(
        n_sims=n_sims,
        n_trades=len(r),
        risk_pct=risk_pct,
        max_dd_p50=float(np.percentile(max_dd, 50)),
        max_dd_p95=float(np.percentile(max_dd, 95)),
        max_dd_p99=float(np.percentile(max_dd, 99)),
        final_return_p5=float(np.percentile(final, 5)),
        final_return_p50=float(np.percentile(final, 50)),
        prob_loss=float((final < 0).mean()),
        prob_dd_over_limit=float((max_dd >= dd_limit_pct).mean()),
        dd_limit_pct=dd_limit_pct,
    )
