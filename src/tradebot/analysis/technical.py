"""Indicateurs techniques en STREAMING (mise à jour bougie par bougie).

Pourquoi du streaming plutôt que du pandas vectorisé : le même code tourne en
backtest et en réel, et un indicateur incrémental ne peut physiquement pas lire
une bougie future. C'est la protection principale contre le biais de look-ahead.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from tradebot.core.models import Bar


class EMA:
    def __init__(self, period: int) -> None:
        if period < 1:
            raise ValueError("period >= 1")
        self.period = period
        self.alpha = 2.0 / (period + 1)
        self.value: float | None = None
        self.count = 0
        self._seed = 0.0

    def update(self, x: float) -> float | None:
        self.count += 1
        # amorçage par la moyenne simple des `period` premières valeurs
        if self.count <= self.period:
            self._seed += x
            if self.count == self.period:
                self.value = self._seed / self.period
            return self.value
        assert self.value is not None
        self.value += self.alpha * (x - self.value)
        return self.value

    @property
    def ready(self) -> bool:
        return self.value is not None


class ATR:
    """Average True Range (lissage de Wilder)."""

    def __init__(self, period: int = 14) -> None:
        self.period = period
        self.value: float | None = None
        self._prev_close: float | None = None
        self._trs: list[float] = []

    def update(self, bar: Bar) -> float | None:
        if self._prev_close is None:
            tr = bar.high - bar.low
        else:
            tr = max(
                bar.high - bar.low,
                abs(bar.high - self._prev_close),
                abs(bar.low - self._prev_close),
            )
        self._prev_close = bar.close
        if self.value is None:
            self._trs.append(tr)
            if len(self._trs) == self.period:
                self.value = sum(self._trs) / self.period
            return self.value
        self.value = (self.value * (self.period - 1) + tr) / self.period
        return self.value

    @property
    def ready(self) -> bool:
        return self.value is not None


class RollingMedian:
    """Médiane glissante calculée à la demande (``update`` est O(1))."""

    def __init__(self, size: int) -> None:
        self._buf: deque[float] = deque(maxlen=size)

    def update(self, x: float) -> None:
        self._buf.append(x)

    @property
    def value(self) -> float | None:
        if not self._buf:
            return None
        s = sorted(self._buf)
        n = len(s)
        return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])


def floor_time(ts: datetime, minutes: int) -> datetime:
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    delta = int((ts - epoch).total_seconds() // 60)
    return epoch + timedelta(minutes=delta - delta % minutes)


@dataclass(slots=True)
class _Acc:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    spread_sum: float
    volume: float
    n: int


class BarAggregator:
    """Agrège des bougies M1 en bougies de ``minutes`` minutes (alignées UTC).

    Une bougie agrégée n'est émise que lorsqu'elle est COMPLÈTE : soit à la
    réception de sa dernière minute, soit à l'arrivée d'une minute de la période
    suivante (données manquantes / marché fermé).
    """

    def __init__(self, minutes: int) -> None:
        if minutes < 1 or (minutes < 60 and 60 % minutes) or (minutes >= 60 and minutes % 60):
            raise ValueError("timeframe non supporté (diviseur de 60 ou multiple d'heures)")
        self.minutes = minutes
        self._acc: _Acc | None = None

    def _emit(self) -> Bar:
        a = self._acc
        assert a is not None
        self._acc = None
        return Bar(a.ts, a.open, a.high, a.low, a.close, a.spread_sum / a.n, a.volume)

    def flush_if_new_period(self, m1: Bar) -> Bar | None:
        """À appeler AVANT de traiter une nouvelle minute."""
        if self._acc is not None and floor_time(m1.ts, self.minutes) != self._acc.ts:
            return self._emit()
        return None

    def add(self, m1: Bar) -> Bar | None:
        """Ajoute la minute ; renvoie la bougie si cette minute la complète."""
        period = floor_time(m1.ts, self.minutes)
        a = self._acc
        if a is None:
            self._acc = _Acc(period, m1.open, m1.high, m1.low, m1.close, m1.spread, m1.volume, 1)
        else:
            if period != a.ts:
                raise RuntimeError("appeler flush_if_new_period() avant add()")
            a.high = max(a.high, m1.high)
            a.low = min(a.low, m1.low)
            a.close = m1.close
            a.spread_sum += m1.spread
            a.volume += m1.volume
            a.n += 1
        if m1.ts + timedelta(minutes=1) >= period + timedelta(minutes=self.minutes):
            return self._emit()
        return None
