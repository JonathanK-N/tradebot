"""Moteur live (paper) + kill switch + bot Telegram, sans réseau."""

from datetime import timedelta

import pyotp
import pytest

from tests.conftest import flat_bars, utc
from tradebot.alerts.telegram import CommandHandler
from tradebot.core.config import AppConfig
from tradebot.core.engine import TradingEngine
from tradebot.core.instrument import XAUUSD
from tradebot.core.models import OrderIntent, Side
from tradebot.execution.sim import SimBroker
from tradebot.fundamental.calendar import EventCalendar
from tradebot.journal.store import Journal, SqlJournal
from tradebot.live.runner import LiveRunner
from tradebot.live.status import StatusFile
from tradebot.risk.killswitch import ControlFile
from tradebot.risk.manager import RiskManager
from tradebot.risk.state import MemoryStateStore
from tradebot.strategy.registry import build_strategy

T0 = utc(2024, 6, 11, 9, 0)


class ListFeed:
    def __init__(self, bars):
        self.bars = list(bars)

    def poll(self):
        out, self.bars = self.bars[:5], self.bars[5:]
        return out


class Recorder:
    def __init__(self):
        self.msgs = []

    def send(self, level, text):
        self.msgs.append((level, text))


@pytest.fixture
def env(tmp_path):
    cfg = AppConfig()
    broker = SimBroker(XAUUSD, cfg.costs, 10_000)
    risk = RiskManager(cfg.risk, XAUUSD, MemoryStateStore(), 10_000)
    control = ControlFile(tmp_path / "control.json")
    notes = Recorder()
    engine = TradingEngine(build_strategy("trend_pullback", "XAUUSD"), risk, broker,
                           EventCalendar(cfg.news), Journal(), control=control, notifier=notes)
    status = StatusFile(tmp_path / "status.json")
    now = [T0 + timedelta(minutes=30)]
    runner = LiveRunner(cfg, engine, ListFeed(flat_bars(T0, 30)), EventCalendar(cfg.news, require_fresh=True),
                        status, notes, clock=lambda: now[0])
    return cfg, broker, control, engine, status, runner, notes, now


def test_cycle_publishes_status(env):
    *_, status, runner, _, _ = env
    runner.cycle()
    st = status.read()
    assert st["available"] and st["mode"] == "paper" and st["last_bar_ts"]
    assert "fail-closed" in st["calendar"]["status"]


def test_kill_switch_flattens_even_without_new_bars(env):
    _cfg, broker, control, _engine, _status, runner, notes, _now = env
    for _ in range(6):
        runner.cycle()  # toutes les bougies consommées
    broker.submit(OrderIntent(T0, "XAUUSD", Side.BUY, 0.1, 1990, 2020, "t", "c"))
    broker.on_bar(flat_bars(T0 + timedelta(minutes=30), 1)[0])
    assert broker.positions()
    control.kill("test", "pytest")
    runner.cycle()  # plus aucune bougie dans le flux : le kill doit quand même s'appliquer
    assert not broker.positions()
    assert not control.read().flatten_requested and control.read().paused
    assert any("fermées" in m for _, m in notes.msgs)


def test_catch_up_bars_do_not_trade(env):
    *_, engine, _, runner, _, now = env
    now[0] = T0 + timedelta(hours=5)  # les bougies ont 5 h de retard
    for _ in range(6):
        runner.cycle()
    assert engine.stats.approved == 0


def test_telegram_commands(tmp_path):
    secret = pyotp.random_base32()
    control = ControlFile(tmp_path / "c.json")
    status = StatusFile(tmp_path / "s.json")
    h = CommandHandler(control, status, [111], secret)
    assert h.handle(999, "/kill") is None  # inconnu : ignoré
    assert not control.read().paused
    assert "Pause" in h.handle(111, "/pause")
    assert control.read().paused
    assert "invalide" in h.handle(111, "/resume 000000")
    assert control.read().paused
    h.handle(111, f"/resume {pyotp.TOTP(secret).now()}")
    assert not control.read().paused
    h.handle(111, "/kill")
    assert control.read().flatten_requested
    assert "ne tourne pas" in h.handle(111, "/status")
    status.write({"mode": "paper", "account": {"equity": 1, "balance": 1}, "positions": [], "risk": {},
                  "control": {}})
    assert "Mode : paper" in h.handle(111, "/status")


def test_reset_halt_via_control(env):
    _cfg, _broker, control, engine, *_ = env
    engine.risk.state.halted = True
    control.request_reset_halt("pytest")
    engine.apply_control(T0)
    assert not engine.risk.state.halted and not control.read().reset_halt_requested


def test_sql_journal(tmp_path):
    from tradebot.core.models import ExitReason, Trade

    j = SqlJournal(f"sqlite:///{tmp_path / 'j.db'}")
    t = Trade("p1", "XAUUSD", Side.BUY, 0.1, T0, 2000, T0 + timedelta(hours=1), 2010, ExitReason.TAKE_PROFIT,
              100, 1, 0, 99, 50, "s", "c")
    j.trade(t)
    j.equity_point(T0, 10_000, 10_000)
    j.equity_point(T0 + timedelta(seconds=10), 10_000, 10_001)  # < 1 min : ignoré
    assert j.recent_trades()[0]["r_multiple"] == pytest.approx(99 / 50)
    assert len(j.equity_curve()) == 1
    assert j.recent_events(kind="trade")[0]["payload"]["pnl"] == 99


def test_dashboard_command(tmp_path):
    h = CommandHandler(ControlFile(tmp_path / "c.json"), StatusFile(tmp_path / "s.json"), [7], None,
                       dashboard_url="https://api-exemple.up.railway.app")
    for cmd in ("/dashboard", "/start"):
        reply = h.handle(7, cmd)
        assert "https://api-exemple.up.railway.app" in reply and "écran d'accueil" in reply
    assert "non configurée" in CommandHandler(ControlFile(tmp_path / "c.json"), StatusFile(tmp_path / "s.json"),
                                              [7], None).handle(7, "/dashboard")
    assert h.handle(8, "/dashboard") is None  # inconnu : ignoré
