from types import SimpleNamespace as NS

from tests.fake_mt5 import FakeMT5
from tradebot.execution.mt5_check import check_mt5


class CheckMT5(FakeMT5):
    SYMBOL_TRADE_MODE_DISABLED = 0

    def __init__(self, demo=True, algo=True, symbols=("XAUUSD", "GOLD", "XAGUSD"), up=True):
        super().__init__()
        self.demo, self.algo, self.names, self.up = demo, algo, symbols, up
        self.shut = False

    def initialize(self, **kw):
        return self.up

    def last_error(self):
        return (-10003, "IPC initialize failed, MetaTrader 5 x64 not found")

    def shutdown(self):
        self.shut = True

    def account_info(self):
        return NS(balance=10_000.0, equity=10_000.0, margin=0.0, currency="CAD", server="CMC-Demo",
                  company="CMC Markets Canada", trade_mode=0 if self.demo else 2, leverage=20,
                  trade_expert=True)

    def terminal_info(self):
        return NS(trade_allowed=self.algo)

    def symbols_get(self, pattern):
        key = pattern.strip("*").upper()
        return tuple(NS(name=n, trade_contract_size=100.0, volume_min=0.01, volume_step=0.01, spread=25,
                        trade_mode=4) for n in self.names if key in n.upper())


def test_ready_demo_account():
    m = CheckMT5()
    r = check_mt5(m)
    assert r.ok and r.is_demo and m.shut
    assert [g["name"] for g in r.gold_symbols] == ["XAUUSD", "GOLD"]  # XAUUSD d'abord, pas l'argent
    assert "broker_symbol: XAUUSD" in r.to_text()


def test_real_account_is_flagged():
    r = check_mt5(CheckMT5(demo=False))
    assert not r.ok and "RÉEL" in r.to_text()


def test_algo_trading_disabled_is_flagged():
    r = check_mt5(CheckMT5(algo=False))
    assert not r.ok and "Algo Trading" in r.to_text()


def test_terminal_not_running():
    r = check_mt5(CheckMT5(up=False))
    assert not r.connected and "Ouvre MetaTrader 5" in r.to_text()


def test_broker_without_gold():
    r = check_mt5(CheckMT5(symbols=("EURUSD",)))
    assert not r.ok and "Aucun symbole or" in r.to_text()
