"""Rapport de backtest en Markdown (lisible sur téléphone : GitHub, Telegram, Termius)."""

from __future__ import annotations

import json
from pathlib import Path

from tradebot.backtest.metrics import Metrics, trades_frame
from tradebot.backtest.runner import BacktestResult


def _fmt(m: Metrics) -> list[str]:
    lo, hi = m.expectancy_r_ci95
    return [
        "| Métrique | Valeur |",
        "|---|---|",
        f"| Trades | {m.n_trades} |",
        f"| Taux de réussite | {m.win_rate:.1f} % |",
        f"| Espérance / trade | {m.expectancy:+.2f} USD ({m.expectancy_r:+.3f} R) |",
        f"| IC 95 % espérance (bootstrap) | [{lo:+.3f} ; {hi:+.3f}] R |",
        f"| t-stat espérance | {m.t_stat_r:.2f} |",
        f"| Profit factor | {m.profit_factor:.2f} |",
        f"| Rendement total | {m.total_return_pct:+.2f} % |",
        f"| CAGR | {m.cagr_pct:+.2f} % |",
        f"| Drawdown max | {m.max_drawdown_pct:.2f} % ({m.max_drawdown_days:.0f} j) |",
        f"| Sharpe / Sortino | {m.sharpe:.2f} / {m.sortino:.2f} |",
        f"| MAR (CAGR/DD) | {m.mar:.2f} |",
        f"| Exposition | {m.exposure_pct:.1f} % du temps |",
        f"| Durée moyenne | {m.avg_hold_hours:.1f} h |",
        f"| Commissions / swaps | {m.total_commission:.2f} / {m.total_swap:.2f} USD |",
    ]


def verdict(m: Metrics) -> list[str]:
    """Lecture critique automatique. Ce n'est PAS un feu vert pour le réel."""
    out = []
    if m.n_trades < 100:
        out.append(f"⚠️ {m.n_trades} trades seulement : échantillon trop petit pour conclure.")
    lo, _ = m.expectancy_r_ci95
    if lo <= 0:
        out.append("⚠️ L'intervalle de confiance de l'espérance inclut 0 : edge non démontré.")
    if m.t_stat_r < 2:
        out.append("⚠️ t-stat < 2 : résultat compatible avec le hasard.")
    if m.expectancy <= 0:
        out.append("❌ Espérance négative ou nulle après coûts.")
    if not out:
        out.append("✅ Critères statistiques de base atteints — étape suivante : walk-forward + "
                   "Monte Carlo. Ce n'est PAS une validation pour le réel.")
    return out


def write_report(res: BacktestResult, out_dir: str | Path, title: str = "") -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    m = res.metrics
    s = res.stats
    lines = [
        f"# Backtest — {res.strategy} {title}".strip(),
        "",
        f"Période : {res.equity.index[0]:%Y-%m-%d} → {res.equity.index[-1]:%Y-%m-%d}",
        "",
        "## Verdict",
        *[f"- {v}" for v in verdict(m)],
        "",
        "## Métriques (après spread, commissions, slippage et swaps)",
        *_fmt(m),
        "",
        "## Par session d'entrée",
        "| Session | Trades | Espérance (R) |",
        "|---|---|---|",
        *[f"| {k} | {v['n']:.0f} | {v['expectancy_r']:+.3f} |" for k, v in sorted(m.by_session.items())],
        "",
        "## Sorties",
        *[f"- {k} : {v}" for k, v in sorted(m.by_exit.items())],
        "",
        "## Entonnoir des signaux",
        f"- Signaux : {s.signals} — approuvés : {s.approved} — refusés : {s.rejected} "
        f"— périmés : {s.stale_signals}",
        *[f"- refus « {k} » : {v}" for k, v in sorted(s.reject_reasons.items(), key=lambda x: -x[1])],
        "",
        "## Paramètres",
        "```json",
        json.dumps(res.params, indent=2, default=str),
        "```",
    ]
    md = out / "report.md"
    md.write_text("\n".join(lines), encoding="utf-8")
    trades_frame(res.trades).to_csv(out / "trades.csv", index=False)
    res.equity.rename("equity").to_csv(out / "equity.csv")
    (out / "metrics.json").write_text(json.dumps(m.to_dict(), indent=2, default=str), encoding="utf-8")
    return md
