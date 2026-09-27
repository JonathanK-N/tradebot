"""Intégration avec de VRAIS PostgreSQL et Redis (mêmes moteurs que Railway).

Ignorés localement ; exécutés dans GitHub Actions (conteneurs de service), où
TEST_POSTGRES_URL et TEST_REDIS_URL sont définis.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from tradebot.bridge.client import RemoteBroker, RemoteFeed
from tradebot.bridge.transport import RedisTransport
from tradebot.core.config import normalize_database_url
from tradebot.core.kv import RedisKV
from tradebot.core.models import ExitReason, Side, Trade
from tradebot.journal.store import SqlJournal
from tradebot.live.status import StatusStore
from tradebot.risk.killswitch import ControlStore

PG = os.environ.get("TEST_POSTGRES_URL")
RD = os.environ.get("TEST_REDIS_URL")


@pytest.mark.skipif(not PG, reason="TEST_POSTGRES_URL non défini (CI uniquement)")
def test_journal_on_real_postgres():
    j = SqlJournal(normalize_database_url(PG))  # URL au format Railway « postgresql:// »
    t0 = datetime(2024, 6, 11, 9, tzinfo=UTC)
    pid = uuid.uuid4().hex
    j.trade(Trade(pid, "XAUUSD", Side.SELL, 0.05, t0, 2000.0, t0 + timedelta(hours=2), 1990.0,
                  ExitReason.TAKE_PROFIT, 50.0, 0.3, -0.1, 49.6, 25.0, "h1", "corr"))
    j.equity_point(t0, 10_000, 10_000)
    j.equity_point(t0 + timedelta(minutes=1), 10_000, 10_010)
    rows = [r for r in j.recent_trades(50) if r["position_id"] == pid]
    assert rows and rows[0]["r_multiple"] == pytest.approx(49.6 / 25.0)
    assert rows[0]["exit_ts"].tzinfo is not None  # horodatages en UTC conservés
    assert any(e["payload"]["position_id"] == pid for e in j.recent_events(20, kind="trade"))
    j.close()


@pytest.mark.skipif(not RD, reason="TEST_REDIS_URL non défini (CI uniquement)")
def test_control_status_on_real_redis():
    prefix = f"tb:test:{uuid.uuid4().hex}:"
    control = ControlStore(RedisKV(RD, prefix=prefix))
    control.kill("ci", "pytest")
    other_process = ControlStore(RedisKV(RD, prefix=prefix))
    assert other_process.read().flatten_requested
    StatusStore(RedisKV(RD, prefix=prefix)).write({"mode": "remote"})
    assert StatusStore(RedisKV(RD, prefix=prefix)).read()["mode"] == "remote"


@pytest.mark.skipif(not RD, reason="TEST_REDIS_URL non défini (CI uniquement)")
def test_bridge_transport_on_real_redis():
    t = RedisTransport(RD, prefix=f"tb:test:{uuid.uuid4().hex}:")
    remote = RemoteBroker(t, reply_timeout_s=1)
    remote.heartbeat()
    assert t.get("core_hb") is not None
    assert not remote.bridge_alive()  # aucun pont : doit être détecté
    assert RemoteFeed(t).poll() == []
    r = remote.close_all(ExitReason.KILL_SWITCH, datetime.now(UTC))[0]
    assert not r.ok and r.retryable  # pas de réponse du pont -> échec explicite, rejouable
