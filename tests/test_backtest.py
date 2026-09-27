"""Tests d'intégrité du backtest, dont le test anti look-ahead (critère semaine 5)."""

import numpy as np
import pandas as pd
import pytest

from tradebot.backtest.runner import run_backtest
from tradebot.core.config import AppConfig
from tradebot.data.synthetic import generate_m1


@pytest.fixture(scope="module")
def data():
    return generate_m1("2024-01-01", "2024-02-15", seed=3)


def _decisions_before(res, cutoff):
    out = []
    for e in res.journal.events:
        if e["kind"] == "decision" and e["ts"] < cutoff:
            s = e["payload"]["signal"]
            out.append((s.ts, s.side, round(s.entry_ref, 6), round(s.stop_loss, 6), e["payload"]["approved"]))
    return out


@pytest.mark.parametrize("strategy", ["asian_breakout", "trend_pullback"])
def test_no_lookahead(data, strategy):
    """Corrompre le FUTUR ne doit rien changer aux décisions du PASSÉ."""
    cfg = AppConfig()
    base = run_backtest(cfg, data, strategy=strategy)
    cutoff = pd.Timestamp("2024-01-25", tz="UTC")
    corrupted = data.copy()
    mask = corrupted["ts"] >= cutoff
    rng = np.random.default_rng(0)
    shock = rng.uniform(0.9, 1.1, mask.sum())
    for col in ("open", "high", "low", "close"):
        corrupted.loc[mask, col] = corrupted.loc[mask, col] * shock
    alt = run_backtest(cfg, corrupted, strategy=strategy)
    before = _decisions_before(base, cutoff.to_pydatetime())
    assert before, "le test n'a de sens que s'il y a des décisions avant la date"
    assert before == _decisions_before(alt, cutoff.to_pydatetime())


def test_random_walk_has_no_edge_after_costs(data):
    """Sur une marche aléatoire, gagner de façon significative trahirait un bug."""
    cfg = AppConfig()
    for s in ("asian_breakout", "trend_pullback"):
        m = run_backtest(cfg, data, strategy=s).metrics
        assert m.t_stat_r < 2.0, f"{s}: edge suspect sur données aléatoires"


def test_accounting_consistency(data):
    cfg = AppConfig()
    res = run_backtest(cfg, data, strategy="asian_breakout")
    total = sum(t.pnl for t in res.trades)
    assert res.equity.iloc[-1] == pytest.approx(cfg.account.initial_balance + total)
    for t in res.trades:
        assert t.pnl == pytest.approx(t.gross_pnl - t.commission + t.swap)
