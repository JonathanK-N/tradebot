"""H2 — Pullback dans la tendance (H1).

Hypothèse (voir docs/hypotheses/H2_trend_pullback.md) : l'or présente des
tendances persistantes (flux macro, banques centrales). Entrer sur un retour vers
la moyenne courte, dans le sens d'une tendance confirmée, offre un meilleur
rapport gain/risque qu'une entrée en poursuite.

Règles (achat ; vente symétrique) :
- Tendance : EMA50 > EMA200 et clôture > EMA200.
- Pullback : le plus bas touche l'EMA20, la clôture repasse au-dessus, bougie haussière.
- Session : uniquement Londres / overlap / New York (liquidité).
- Stop : 1,5 ATR ; objectif : 2 R ; sortie forcée après 48 h.
- Pause de ``cooldown_bars`` bougies après chaque signal.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from tradebot.analysis.technical import ATR, EMA
from tradebot.core.models import Bar, Side, Signal
from tradebot.core.sessions import Session, session_of
from tradebot.strategy.base import Strategy, StrategyContext

ALLOWED = {Session.LONDON, Session.OVERLAP, Session.NEW_YORK}


class TrendPullback(Strategy):
    name = "trend_pullback"
    timeframe_minutes = 60

    @classmethod
    def default_params(cls) -> dict[str, Any]:
        return {
            "ema_fast": 20,
            "ema_mid": 50,
            "ema_slow": 200,
            "atr_period": 14,
            "stop_atr": 1.5,
            "reward_risk": 2.0,
            "max_hold_hours": 48,
            "cooldown_bars": 6,
        }

    def __init__(self, symbol: str, **params: Any) -> None:
        super().__init__(symbol, **params)
        p = self.params
        self.fast = EMA(int(p["ema_fast"]))
        self.mid = EMA(int(p["ema_mid"]))
        self.slow = EMA(int(p["ema_slow"]))
        self.atr = ATR(int(p["atr_period"]))
        self._cooldown = 0

    def on_bar(self, bar: Bar, ctx: StrategyContext) -> Signal | None:
        f = self.fast.update(bar.close)
        m = self.mid.update(bar.close)
        s = self.slow.update(bar.close)
        a = self.atr.update(bar)
        if self._cooldown > 0:
            self._cooldown -= 1
            return None
        if f is None or m is None or s is None or a is None or ctx.open_positions > 0:
            return None
        close_ts = self.close_time(bar)
        if session_of(close_ts) not in ALLOWED:
            return None

        p = self.params
        side: Side | None = None
        if m > s and bar.close > s and bar.low <= f < bar.close and bar.close > bar.open:
            side, entry = Side.BUY, bar.ask_close
        elif m < s and bar.close < s and bar.high >= f > bar.close and bar.close < bar.open:
            side, entry = Side.SELL, bar.close
        if side is None:
            return None

        self._cooldown = int(p["cooldown_bars"])
        stop = entry - side.sign * p["stop_atr"] * a
        tp = entry + side.sign * p["stop_atr"] * a * p["reward_risk"]
        return Signal(
            ts=close_ts,
            symbol=self.symbol,
            side=side,
            entry_ref=entry,
            stop_loss=stop,
            take_profit=tp,
            strategy=self.name,
            reason=(
                f"pullback {'haussier' if side is Side.BUY else 'baissier'} sur EMA{p['ema_fast']} "
                f"(EMA{p['ema_mid']}={m:.2f} vs EMA{p['ema_slow']}={s:.2f}, ATR={a:.2f})"
            ),
            expire_after=timedelta(hours=float(p["max_hold_hours"])),
            features={"ema_fast": f, "ema_mid": m, "ema_slow": s, "atr": a},
        )
