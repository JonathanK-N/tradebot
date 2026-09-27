"""Module de risque : tests d'exemples + tests par propriétés (hypothesis).

Invariants vérifiés sur des milliers d'entrées aléatoires :
- un ordre approuvé a TOUJOURS un stop du bon côté ;
- le risque d'un ordre approuvé ne dépasse JAMAIS le budget par trade ;
- le volume respecte les bornes du contrat et de la config.
"""

from datetime import timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tests.conftest import utc
from tradebot.core.config import RiskConfig
from tradebot.core.instrument import XAUUSD
from tradebot.core.models import AccountState, MarketSnapshot, Side, Signal
from tradebot.fundamental.calendar import BlackoutCheck
from tradebot.risk.manager import RiskManager
from tradebot.risk.state import MemoryStateStore, StateStore

TS = utc(2024, 6, 11, 9, 0)  # mardi, session de Londres
OK_NEWS = BlackoutCheck(False)


def signal(side=Side.BUY, entry=2000.0, stop=1990.0, tp=2020.0, ts=TS):
    return Signal(ts, "XAUUSD", side, entry, stop, tp, "test", "test")


def rm(**kw) -> RiskManager:
    return RiskManager(RiskConfig(**kw), XAUUSD, MemoryStateStore(), 10_000)


def market(spread=0.2, median=0.2, ts=TS):
    return MarketSnapshot(ts, 2000.0, spread, median)


def test_sizing_basic():
    r = rm()
    d = r.evaluate(signal(), AccountState(10_000, 10_000), market(), [], OK_NEWS)
    assert d.approved
    # budget 50 USD ; stop effectif 10 + 0.1 + 0.2 = 10.3 -> 50/(10.3*100)=0.0485 -> 0.04
    assert d.intent.volume == pytest.approx(0.04)
    assert d.intent.risk_amount <= 50


def test_insufficient_capital_rejected():
    d = rm().evaluate(signal(stop=1950), AccountState(2_000, 2_000), market(), [], OK_NEWS)
    assert not d.approved
    assert any(r.startswith("capital_insuffisant") for r in d.reasons)


def test_config_hard_limits():
    with pytest.raises(ValueError):
        RiskConfig(risk_per_trade_pct=50)
    with pytest.raises(ValueError):
        RiskConfig(max_daily_loss_pct=5, max_weekly_loss_pct=4)


@pytest.mark.parametrize(
    "kwargs,code",
    [
        ({"sig": signal(stop=2010)}, "stop_invalide"),
        ({"sig": signal(side=Side.SELL, stop=1990, tp=1980)}, "stop_invalide"),
        ({"sig": signal(stop=1999.8)}, "stop_trop_proche"),
        ({"sig": signal(tp=2005)}, "rr"),
        ({"mk": market(spread=1.5)}, "spread"),
        ({"mk": market(spread=0.7, median=0.2)}, "spread_median"),
        ({"news": BlackoutCheck(True, "NFP")}, "news"),
        ({"sig": signal(ts=utc(2024, 6, 8, 12)), "mk": market(ts=utc(2024, 6, 8, 12))}, "marche_ferme"),
        ({"paused": True}, "pause"),
    ],
)
def test_rejections(kwargs, code):
    d = rm().evaluate(kwargs.get("sig", signal()), AccountState(10_000, 10_000), kwargs.get("mk", market()),
                      [], kwargs.get("news", OK_NEWS), paused=kwargs.get("paused", False))
    assert not d.approved
    assert any(r.startswith(code) for r in d.reasons), d.reasons


def test_daily_lock_and_reset_next_day():
    r = rm()
    r.on_equity(TS, 10_000)
    events = r.on_equity(TS + timedelta(hours=1), 9_790)  # -2.1 %
    assert any("DAY_LOCK" in e for e in events)
    d = r.evaluate(signal(ts=TS + timedelta(hours=1)), AccountState(9_790, 9_790), market(), [], OK_NEWS)
    assert any(x.startswith("limite_jour") for x in d.reasons)
    r.on_equity(TS + timedelta(days=1), 9_790)  # nouvelle journée de trading
    assert not r.state.day_locked


def test_drawdown_halt_is_permanent_until_manual_reset():
    r = rm()
    r.on_equity(TS, 10_000)
    t = TS
    for eq in (9_850, 9_700, 9_550, 9_400, 9_250, 9_100, 8_990):  # sur plusieurs jours
        t += timedelta(days=1)
        r.on_equity(t, eq)
    assert r.state.halted
    r.on_equity(t + timedelta(days=5), 9_500)  # même si ça remonte
    assert r.state.halted
    r.reset_halt("test")
    r.on_equity(t + timedelta(days=6), 9_500)
    assert not r.state.halted and r.state.peak_equity == 9_500


def test_state_persists_across_restart(tmp_path):
    store = StateStore(tmp_path / "risk.json")
    r1 = RiskManager(RiskConfig(), XAUUSD, store, 10_000)
    r1.on_equity(TS, 10_000)
    r1.on_equity(TS + timedelta(hours=1), 9_700)
    r2 = RiskManager(RiskConfig(), XAUUSD, StateStore(tmp_path / "risk.json"), 10_000)
    assert r2.state.day_locked  # le redémarrage n'efface pas la perte du jour


@settings(max_examples=500, deadline=None)
@given(
    side=st.sampled_from([Side.BUY, Side.SELL]),
    entry=st.floats(500, 5000),
    stop_dist=st.floats(0.01, 200),
    rr=st.floats(0.1, 5),
    equity=st.floats(100, 1_000_000),
    risk_pct=st.floats(0.05, 2.0),
    spread=st.floats(0.01, 1.0),
)
def test_invariants(side, entry, stop_dist, rr, equity, risk_pct, spread):
    r = rm(risk_per_trade_pct=risk_pct, max_volume_lots=5.0)
    stop = entry - side.sign * stop_dist
    tp = entry + side.sign * stop_dist * rr
    d = r.evaluate(signal(side, entry, stop, tp), AccountState(equity, equity), market(spread, spread),
                   [], OK_NEWS)
    if d.approved:
        i = d.intent
        assert (i.stop_loss - entry) * side.sign < 0  # stop du bon côté
        assert i.risk_amount <= equity * risk_pct / 100 + 1e-6  # budget respecté
        assert XAUUSD.volume_min <= i.volume <= 5.0
        steps = i.volume / XAUUSD.volume_step
        assert abs(steps - round(steps)) < 1e-6
