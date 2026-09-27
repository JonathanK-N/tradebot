"""Critère go/no-go semaine 9 (en démo réelle : 100 ordres, 0 doublon, 0 orphelin, 100 % avec SL).
Ici : les mêmes scénarios de panne, rejoués sur un faux terminal."""

from datetime import UTC, datetime, timedelta

import pytest

from tests.fake_mt5 import FakeMT5
from tradebot.core.instrument import XAUUSD
from tradebot.core.models import ExitReason, OrderIntent, Side
from tradebot.execution import mt5_adapter
from tradebot.execution.mt5_adapter import MT5Adapter


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(mt5_adapter._time, "sleep", lambda s: None)


def make(fake=None):
    fake = fake or FakeMT5()
    a = MT5Adapter("XAUUSD", 42, XAUUSD, mt5=fake)
    a.connect(None, None, None)
    return a, fake


def intent(cid="abc123"):
    return OrderIntent(datetime.now(UTC), "XAUUSD", Side.BUY, 0.05, 1990.0, 2020.0, "t", "corr",
                       client_order_id=cid)


def test_server_offset_detected_and_bars_in_utc():
    a, _ = make(FakeMT5(server_offset_h=3))
    assert a.server_offset == timedelta(hours=3)
    bars = a.completed_m1_bars(3)
    assert bars[0].ts == datetime(2024, 6, 11, 9, 0, tzinfo=UTC)
    assert bars[0].spread == pytest.approx(0.25)


def test_simple_order():
    a, f = make()
    r = a.submit(intent())
    assert r.ok and len(f.positions) == 1
    req = f.sent[0]
    assert req["sl"] == 1990.0 and req["magic"] == 42 and req["type_filling"] == f.ORDER_FILLING_IOC


def test_requote_then_success():
    a, f = make()
    f.script = [(10004, False), (10009, True)]
    assert a.submit(intent()).ok and len(f.positions) == 1 and len(f.sent) == 2


def test_timeout_but_executed_no_duplicate():
    a, f = make()
    f.script = [(10012, True)]  # le serveur a exécuté mais répond « timeout »
    r = a.submit(intent())
    assert r.ok and len(f.positions) == 1  # AUCUN doublon


def test_resubmit_same_intent_is_idempotent():
    a, f = make()
    i = intent()
    a.submit(i)
    a.submit(i)
    assert len(f.positions) == 1


def test_non_retryable_error():
    a, f = make()
    f.script = [(10019, False)]  # pas assez de marge
    r = a.submit(intent())
    assert not r.ok and not f.positions


def test_missing_sl_gets_fixed():
    a, f = make()
    f.drop_sl = True
    assert a.submit(intent()).ok
    assert next(iter(f.positions.values())).sl == 1990.0


def test_missing_sl_unfixable_closes_position():
    a, f = make()
    f.drop_sl, f.sltp_ok = True, False
    r = a.submit(intent())
    assert not r.ok and not f.positions  # jamais de position sans stop


def test_close_and_trade_reconstruction():
    a, f = make()
    a.submit(intent())
    [pos] = a.positions()
    f.bid = 2010.0
    assert a.close_position(pos.position_id, ExitReason.MANUAL, datetime.now(UTC)).ok
    [t] = a.drain_closed_trades()
    assert t.gross_pnl == pytest.approx((2010.0 - 2000.3) * 0.05 * 100)
    assert t.commission == pytest.approx(6.0) and t.swap == -1.0


def test_ignores_foreign_positions():
    a, f = make()
    a.submit(intent())
    next(iter(f.positions.values())).magic = 999  # position manuelle
    assert a.positions() == []
