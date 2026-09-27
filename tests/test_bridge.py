"""Pont cœur <-> MT5 : aller-retour complet, sur transport mémoire et faux terminal."""

import threading
import time
from datetime import UTC, datetime

import pytest

from tests.fake_mt5 import FakeMT5
from tradebot.bridge.client import BridgeUnavailable, RemoteBroker, RemoteFeed
from tradebot.bridge.server import BridgeGuard, BridgeServer
from tradebot.bridge.transport import MemoryTransport
from tradebot.core.instrument import XAUUSD
from tradebot.core.models import ExitReason, OrderIntent, Side
from tradebot.execution import mt5_adapter
from tradebot.execution.mt5_adapter import MT5Adapter


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setattr(mt5_adapter._time, "sleep", lambda s: None)
    fake = FakeMT5()
    a = MT5Adapter("XAUUSD", 7, XAUUSD, mt5=fake)
    a.connect(None, None, None)
    t = MemoryTransport()
    server = BridgeServer(a, t, BridgeGuard(max_volume=0.5, max_positions=1))
    client = RemoteBroker(t, reply_timeout_s=3)
    return fake, t, server, client


def serve_once(server):
    th = threading.Thread(target=server.step, kwargs={"cmd_wait_s": 1.5})
    th.start()
    return th


def intent(vol=0.1, sl=1990.0):
    return OrderIntent(datetime.now(UTC), "XAUUSD", Side.BUY, vol, sl, 2020.0, "t", "c")


def test_state_and_bars(setup):
    _fake, t, server, client = setup
    with pytest.raises(BridgeUnavailable):
        client.account()
    server.publish_state()
    assert client.account().equity == 10_000
    assert len(RemoteFeed(t).poll()) == 120
    server.publish_state()
    assert RemoteFeed(t).poll() == []  # pas de doublon de bougies
    server.publish_history(500)
    assert len(RemoteFeed(t).history()) == 500


def test_submit_roundtrip(setup):
    fake, _t, server, client = setup
    client.heartbeat()
    th = serve_once(server)
    time.sleep(0.05)
    r = client.submit(intent())
    th.join()
    assert r.ok and len(fake.positions) == 1


def test_guard_rejects_without_core_heartbeat(setup):
    fake, _t, server, client = setup
    th = serve_once(server)
    r = client.submit(intent())  # pas de heartbeat du cœur
    th.join()
    assert not r.ok and "heartbeat" in r.message and not fake.positions


@pytest.mark.parametrize("kw,msg", [({"vol": 2.0}, "volume"), ({"sl": 2005.0}, "mauvais côté")])
def test_guard_hard_limits(setup, kw, msg):
    _fake, _t, server, client = setup
    client.heartbeat()
    th = serve_once(server)
    r = client.submit(intent(**kw))
    th.join()
    assert not r.ok and msg in r.message


def test_close_all_and_trades(setup):
    fake, _t, server, client = setup
    client.heartbeat()
    th = serve_once(server)
    client.submit(intent())
    th.join()
    th = serve_once(server)
    [res] = client.close_all(ExitReason.KILL_SWITCH, datetime.now(UTC))
    th.join()
    assert res.ok and not fake.positions
    server.publish_state()
    assert len(client.drain_closed_trades()) == 1


def test_no_reply_is_retryable(setup):
    _, t, _, _ = setup
    client = RemoteBroker(t, reply_timeout_s=0.2)
    r = client.submit(intent())
    assert not r.ok and r.retryable
