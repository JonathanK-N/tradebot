"""H1 — Cassure du range asiatique à l'ouverture de Londres.

Hypothèse de marché (voir docs/hypotheses/H1_asian_breakout.md) : pendant la
session asiatique, l'or évolue souvent dans un range étroit ; l'arrivée de la
liquidité européenne peut déclencher une expansion directionnelle. On ne prend
que la PREMIÈRE cassure confirmée par une clôture M15, un trade maximum par jour.

Invalidation naturelle : retour au milieu du range -> stop au point médian.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from tradebot.analysis.technical import ATR
from tradebot.core.models import Bar, Side, Signal
from tradebot.core.sessions import LONDON, local_window, to_london
from tradebot.strategy.base import Strategy, StrategyContext


def _t(v: str | time) -> time:
    return v if isinstance(v, time) else time.fromisoformat(v)


class AsianBreakout(Strategy):
    name = "asian_breakout"
    timeframe_minutes = 15

    @classmethod
    def default_params(cls) -> dict[str, Any]:
        return {
            "range_start": "00:00",  # heure de Londres
            "range_end": "07:00",
            "trade_end": "11:00",  # plus d'entrée après
            "exit_time": "16:30",  # sortie forcée (avant la fin de l'overlap)
            "atr_period": 14,
            "buffer_atr": 0.10,  # la clôture doit dépasser le range de 0,1 ATR
            "min_range_atr": 2.0,  # range trop étroit = bruit
            "max_range_atr": 12.0,  # range trop large = le mouvement a déjà eu lieu
            "reward_risk": 1.5,
            "min_range_coverage": 0.8,  # part minimale de bougies présentes dans le range
        }

    def __init__(self, symbol: str, **params: Any) -> None:
        super().__init__(symbol, **params)
        p = self.params
        self.atr = ATR(int(p["atr_period"]))
        self._day: date | None = None
        self._hi = float("-inf")
        self._lo = float("inf")
        self._n_range_bars = 0
        self._traded = False
        self._windows: dict[str, tuple[datetime, datetime]] = {}
        self._exit_at: datetime | None = None

    def _reset_day(self, d: date) -> None:
        p = self.params
        self._day = d
        self._hi, self._lo = float("-inf"), float("inf")
        self._n_range_bars = 0
        self._traded = False
        self._windows = {
            "range": local_window(d, _t(p["range_start"]), _t(p["range_end"]), LONDON),
            "trade": local_window(d, _t(p["range_end"]), _t(p["trade_end"]), LONDON),
        }
        self._exit_at = datetime.combine(d, _t(p["exit_time"]), tzinfo=LONDON).astimezone(UTC)

    def on_bar(self, bar: Bar, ctx: StrategyContext) -> Signal | None:
        atr = self.atr.update(bar)
        close_ts = self.close_time(bar)
        d = to_london(bar.ts).date()
        if d != self._day:
            self._reset_day(d)

        r_start, r_end = self._windows["range"]
        if r_start <= bar.ts and close_ts <= r_end:
            self._hi = max(self._hi, bar.high)
            self._lo = min(self._lo, bar.low)
            self._n_range_bars += 1
            return None

        t_start, t_end = self._windows["trade"]
        if not (t_start <= bar.ts and close_ts <= t_end) or self._traded or atr is None:
            return None
        if ctx.open_positions > 0:
            return None

        p = self.params
        expected = (r_end - r_start).total_seconds() / 60 / self.timeframe_minutes
        if self._n_range_bars < p["min_range_coverage"] * expected:
            return None
        width = self._hi - self._lo
        if not (p["min_range_atr"] * atr <= width <= p["max_range_atr"] * atr):
            return None

        buf = p["buffer_atr"] * atr
        mid = (self._hi + self._lo) / 2
        side: Side | None = None
        if bar.close > self._hi + buf:
            side = Side.BUY
            entry = bar.ask_close
        elif bar.close < self._lo - buf:
            side = Side.SELL
            entry = bar.close
        if side is None:
            return None

        self._traded = True  # une seule tentative par jour, même si le risque refuse
        risk = abs(entry - mid)
        tp = entry + side.sign * p["reward_risk"] * risk
        return Signal(
            ts=close_ts,
            symbol=self.symbol,
            side=side,
            entry_ref=entry,
            stop_loss=mid,
            take_profit=tp,
            strategy=self.name,
            reason=(
                f"cassure {'haussière' if side is Side.BUY else 'baissière'} du range asiatique "
                f"[{self._lo:.2f}-{self._hi:.2f}] (largeur {width:.2f} = {width / atr:.1f} ATR)"
            ),
            expire_after=max(self._exit_at - close_ts, timedelta(0)) if self._exit_at else None,
            features={"range_hi": self._hi, "range_lo": self._lo, "atr": atr,
                      "range_atr": width / atr},
        )
