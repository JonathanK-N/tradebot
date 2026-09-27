"""Faux module MetaTrader5 : reproduit les comportements qui cassent les bots en réel
(requotes, timeouts où l'ordre est pourtant exécuté, SL non posé, heure serveur)."""

from __future__ import annotations

import time
from types import SimpleNamespace as NS

import numpy as np


class FakeMT5:
    TRADE_ACTION_DEAL, TRADE_ACTION_SLTP = 1, 6
    ORDER_TYPE_BUY, ORDER_TYPE_SELL = 0, 1
    POSITION_TYPE_BUY, POSITION_TYPE_SELL = 0, 1
    ORDER_FILLING_FOK, ORDER_FILLING_IOC, ORDER_FILLING_RETURN = 0, 1, 2
    ORDER_TIME_GTC = 0
    TIMEFRAME_M1 = 1
    DEAL_ENTRY_IN, DEAL_ENTRY_OUT, DEAL_ENTRY_OUT_BY = 0, 1, 3
    DEAL_REASON_SL, DEAL_REASON_TP = 4, 5
    ACCOUNT_TRADE_MODE_DEMO = 0

    def __init__(self, server_offset_h: int = 3) -> None:
        self.offset = server_offset_h * 3600
        self.bid, self.ask = 2000.0, 2000.3
        self.positions: dict[int, NS] = {}
        self.deals: list[NS] = []
        self.script: list[tuple[int, bool]] = []  # (retcode, exécuter quand même ?)
        self.drop_sl = False
        self.sltp_ok = True
        self.sent: list[dict] = []
        self._ticket = 1000

    # --- API MT5
    def initialize(self, **kw): return True
    def last_error(self): return (0, "ok")
    def symbol_select(self, s, v): return True
    def account_info(self):
        return NS(balance=10_000.0, equity=10_000.0, margin=0.0, currency="USD", server="Demo",
                  trade_mode=0, leverage=20)
    def symbol_info(self, s):
        return NS(point=0.01, trade_contract_size=100.0, trade_tick_size=0.01, volume_min=0.01,
                  volume_step=0.01, volume_max=100.0, filling_mode=2)
    def symbol_info_tick(self, s):
        return NS(bid=self.bid, ask=self.ask, time=int(time.time()) + self.offset)
    def positions_get(self, symbol=None): return tuple(self.positions.values())
    def history_deals_get(self, position=None):
        return tuple(d for d in self.deals if d.position_id == position)

    def copy_rates_from_pos(self, symbol, tf, start, count):
        t0 = 1_718_096_400 + self.offset  # 2024-06-11 09:00 UTC en heure serveur
        dt = np.dtype([("time", "i8"), ("open", "f8"), ("high", "f8"), ("low", "f8"), ("close", "f8"),
                       ("tick_volume", "i8"), ("spread", "i4"), ("real_volume", "i8")])
        return np.array([(t0 + 60 * i, 2000, 2001, 1999, 2000.5, 10, 25, 0) for i in range(count)], dtype=dt)

    def order_send(self, req):
        self.sent.append(req)
        code, execute = self.script.pop(0) if self.script else (10009, True)
        if req["action"] == self.TRADE_ACTION_SLTP:
            if self.sltp_ok:
                p = self.positions[req["position"]]
                p.sl, p.tp = req["sl"], req["tp"]
                return NS(retcode=10009, price=0, volume=0, comment="ok")
            return NS(retcode=10016, price=0, volume=0, comment="invalid stops")
        if execute:
            if "position" in req:
                p = self.positions.pop(req["position"])
                self.deals.append(NS(position_id=p.ticket, entry=self.DEAL_ENTRY_IN, profit=0.0,
                                     commission=-3.0, swap=0.0, fee=0.0, price=p.price_open, time=p.time,
                                     reason=0))
                pnl = (req["price"] - p.price_open) * p.volume * 100 * (1 if p.type == 0 else -1)
                self.deals.append(NS(position_id=p.ticket, entry=self.DEAL_ENTRY_OUT, profit=pnl,
                                     commission=-3.0, swap=-1.0, fee=0.0, price=req["price"],
                                     time=int(time.time()) + self.offset, reason=0))
            else:
                self._ticket += 1
                self.positions[self._ticket] = NS(
                    ticket=self._ticket, symbol=req["symbol"], magic=req["magic"], type=req["type"],
                    volume=req["volume"], price_open=req["price"], time=int(time.time()) + self.offset,
                    sl=0.0 if self.drop_sl else req["sl"], tp=req["tp"], comment=req["comment"], swap=0.0)
        return NS(retcode=code, price=req["price"], volume=req["volume"], comment=f"rc {code}")
