"""Contrôle qualité des données : une donnée fausse produit un backtest faux.

Critère go/no-go (semaine 2) : < 0,1 % de minutes manquantes pendant les heures
d'ouverture, 0 incohérence OHLC, pics isolés expliqués ou filtrés.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from tradebot.core.sessions import NEW_YORK


def market_open_mask(idx: pd.DatetimeIndex) -> np.ndarray:
    """Version vectorisée de sessions.is_market_open (mêmes règles)."""
    ny = idx.tz_convert(NEW_YORK)
    wd = ny.weekday.to_numpy()
    minutes = (ny.hour * 60 + ny.minute).to_numpy()
    close, reopen = 17 * 60, 18 * 60
    in_break = (minutes >= close) & (minutes < reopen)
    is_open = (wd <= 4) & ~in_break
    is_open &= ~((wd == 4) & (minutes >= close))
    is_open |= (wd == 6) & (minutes >= reopen)
    return is_open


@dataclass
class QualityReport:
    symbol: str
    n_bars: int
    start: str
    end: str
    duplicates: int
    ohlc_violations: int
    non_positive_spread: int
    spikes: int
    expected_open_minutes: int
    missing_open_minutes: int
    missing_pct: float
    spread_median_by_hour_utc: dict[int, float] = field(default_factory=dict)
    largest_gaps: list[tuple[str, int]] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.missing_pct < 0.1 and self.ohlc_violations == 0 and self.duplicates == 0

    def to_dict(self) -> dict:
        return {**asdict(self), "passed": self.passed}

    def to_markdown(self) -> str:
        lines = [
            f"# Qualité des données — {self.symbol}",
            f"**Verdict : {'✅ GO' if self.passed else '❌ NO-GO'}**",
            "",
            f"- Période : {self.start} → {self.end}",
            f"- Bougies M1 : {self.n_bars:,}",
            f"- Minutes manquantes (marché ouvert) : {self.missing_open_minutes:,} "
            f"/ {self.expected_open_minutes:,} ({self.missing_pct:.3f} %)",
            f"- Doublons : {self.duplicates}",
            f"- Incohérences OHLC : {self.ohlc_violations}",
            f"- Spreads <= 0 : {self.non_positive_spread}",
            f"- Pics suspects (> 10 écarts-types) : {self.spikes}",
            "",
            "## Plus grands trous (marché ouvert)",
        ]
        lines += [f"- {ts} : {n} min" for ts, n in self.largest_gaps] or ["- aucun"]
        lines += ["", "## Spread médian par heure UTC"]
        lines += [f"- {h:02d}h : {v:.3f}" for h, v in sorted(self.spread_median_by_hour_utc.items())]
        lines += ["", "_Note : les jours fériés (Noël, 1er janvier…) apparaissent comme des trous._"]
        return "\n".join(lines)


def check_quality(df: pd.DataFrame, symbol: str) -> QualityReport:
    ts = pd.to_datetime(df["ts"], utc=True)
    dup = int(ts.duplicated().sum())
    o, h, lo, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    viol = int(((h < np.maximum(o, c) - 1e-9) | (lo > np.minimum(o, c) + 1e-9) | (h < lo)).sum())
    nps = int((df["spread"] <= 0).sum())

    r = np.log(df["close"]).diff()
    std = r.rolling(1440, min_periods=100).std()
    spikes = int((r.abs() > 10 * std).sum())

    full = pd.date_range(ts.min().floor("min"), ts.max().floor("min"), freq="min", tz="UTC")
    expected_idx = full[market_open_mask(full)]
    present = pd.DatetimeIndex(ts.dt.floor("min").unique())
    missing_idx = expected_idx.difference(present)
    expected = len(expected_idx)
    missing = len(missing_idx)

    gaps: list[tuple[str, int]] = []
    if missing:
        m = pd.Series(missing_idx)
        grp = (m.diff() != pd.Timedelta(minutes=1)).cumsum()
        sizes = m.groupby(grp).agg(["first", "count"]).sort_values("count", ascending=False).head(10)
        gaps = [(str(r["first"]), int(r["count"])) for _, r in sizes.iterrows()]

    by_hour = df.groupby(ts.dt.hour)["spread"].median().round(4).to_dict()
    return QualityReport(
        symbol=symbol,
        n_bars=len(df),
        start=str(ts.min()),
        end=str(ts.max()),
        duplicates=dup,
        ohlc_violations=viol,
        non_positive_spread=nps,
        spikes=spikes,
        expected_open_minutes=expected,
        missing_open_minutes=missing,
        missing_pct=100 * missing / expected if expected else 0.0,
        spread_median_by_hour_utc={int(k): float(v) for k, v in by_hour.items()},
        largest_gaps=gaps,
    )
