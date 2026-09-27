from datetime import timedelta

import pandas as pd
import pytest

from tests.conftest import make_bar, utc
from tradebot.analysis.technical import ATR, EMA, BarAggregator


def test_ema_matches_pandas_after_seed():
    xs = [float(i % 7 + i * 0.1) for i in range(100)]
    e = EMA(10)
    vals = [e.update(x) for x in xs]
    assert vals[8] is None and vals[9] == pytest.approx(sum(xs[:10]) / 10)
    s = pd.Series(xs)
    seeded = pd.concat([pd.Series([s[:10].mean()]), s[10:]]).ewm(span=10, adjust=False).mean()
    assert vals[-1] == pytest.approx(seeded.iloc[-1])


def test_atr_constant_range():
    a = ATR(5)
    t = utc(2024, 1, 2)
    for i in range(10):
        v = a.update(make_bar(t + timedelta(minutes=i), 100, 101, 99, 100))
    assert v == pytest.approx(2.0)


def test_aggregator_emits_only_complete_bars():
    agg = BarAggregator(15)
    t0 = utc(2024, 1, 2, 10, 0)
    out = []
    for i in range(30):
        b = make_bar(t0 + timedelta(minutes=i), 100 + i, c=100 + i + 0.5)
        assert agg.flush_if_new_period(b) is None
        r = agg.add(b)
        if r:
            out.append((i, r))
    assert [i for i, _ in out] == [14, 29]
    first = out[0][1]
    assert first.ts == t0 and first.open == 100 and first.close == 114.5 and first.high == 114.5


def test_aggregator_flushes_on_gap():
    agg = BarAggregator(15)
    t0 = utc(2024, 1, 2, 10, 0)
    for i in range(5):
        agg.add(make_bar(t0 + timedelta(minutes=i), 100))
    nxt = make_bar(t0 + timedelta(minutes=20), 101)  # trou de données
    flushed = agg.flush_if_new_period(nxt)
    assert flushed is not None and flushed.ts == t0
