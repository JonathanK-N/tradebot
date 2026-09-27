import numpy as np
import pytest

from tradebot.core.config import AppConfig
from tradebot.data.synthetic import generate_m1
from tradebot.optimization.montecarlo import monte_carlo
from tradebot.optimization.sensitivity import sensitivity
from tradebot.optimization.walkforward import param_grid, walk_forward


def test_param_grid():
    assert len(param_grid({"a": [1, 2], "b": [3, 4, 5]})) == 6


def test_monte_carlo_known_cases():
    # que des gains : aucun drawdown
    res = monte_carlo([1.0] * 50, risk_pct=1, n_sims=200)
    assert res.max_dd_p95 == 0 and res.prob_loss == 0
    # que des pertes de 1R à 1 % : 10 pertes -> DD = 1 - 0.99^10
    res = monte_carlo([-1.0] * 10, risk_pct=1, n_sims=50, method="shuffle")
    assert res.max_dd_p50 == pytest.approx((1 - 0.99**10) * 100)


def test_monte_carlo_dd_grows_with_risk():
    r = np.random.default_rng(1).normal(0.1, 1.2, 300)
    assert monte_carlo(r, risk_pct=2).max_dd_p95 > monte_carlo(r, risk_pct=0.5).max_dd_p95


@pytest.fixture(scope="module")
def bars():
    return generate_m1("2024-01-01", "2024-05-01", seed=5)


def test_walk_forward_runs(bars):
    res = walk_forward(AppConfig(), bars, "asian_breakout", {"reward_risk": [1.0, 2.0]},
                       train_months=2, test_months=1, min_trades=5)
    assert len(res.windows) >= 1
    for w in res.windows:  # aucun trade OOS ne commence avant la fin du train
        assert all(t.entry_ts >= w.train_end for t in w.oos_trades)
    assert "Walk-forward" in res.to_markdown()


def test_sensitivity_runs(bars):
    res = sensitivity(AppConfig(), bars.iloc[:40000], "asian_breakout",
                      {"reward_risk": [1.0, 1.5, 2.0], "buffer_atr": [0.1, 0.2]})
    assert len(res.table) == 6 and "Sensibilité" in res.to_markdown()
