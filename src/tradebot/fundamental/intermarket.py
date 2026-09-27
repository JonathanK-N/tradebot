"""Contexte intermarché QUOTIDIEN (taux réels, dollar, VIX).

Anti look-ahead : une valeur FRED datée du jour J n'est publiée qu'en fin de
journée J (voire J+1). On applique donc un décalage de publication : pour
décider à l'instant t, on n'utilise que les valeurs dont la date est
<= date(t) - ``publication_lag_days``.

Ce contexte n'est PAS utilisé par les hypothèses H1/H2 pré-enregistrées : l'ajouter
serait une nouvelle hypothèse, testée séparément (sinon : data snooping).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from io import StringIO

import httpx
import pandas as pd

FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"

SERIES = {
    "real_yield_10y": "DFII10",  # rendement réel 10 ans (TIPS)
    "usd_broad": "DTWEXBGS",  # indice dollar large (proxy DXY)
    "vix": "VIXCLS",
}


def fetch_fred(series_id: str, timeout: float = 30.0) -> pd.Series:
    r = httpx.get(FRED_CSV.format(series=series_id), timeout=timeout, follow_redirects=True)
    r.raise_for_status()
    df = pd.read_csv(StringIO(r.text))
    date_col = df.columns[0]
    s = pd.to_numeric(df[series_id], errors="coerce")  # "." = valeur manquante chez FRED
    s.index = pd.to_datetime(df[date_col])
    return s.dropna().rename(series_id)


class IntermarketContext:
    def __init__(self, frame: pd.DataFrame, publication_lag_days: int = 1) -> None:
        self.frame = frame.sort_index()
        self.lag = timedelta(days=publication_lag_days)

    def asof(self, ts: datetime) -> dict[str, float]:
        cutoff = pd.Timestamp((ts - self.lag).date())
        sub = self.frame.loc[:cutoff]
        if sub.empty:
            return {}
        row = sub.ffill().iloc[-1]
        return {k: float(v) for k, v in row.items() if pd.notna(v)}

    def change(self, ts: datetime, column: str, days: int) -> float | None:
        cutoff = pd.Timestamp((ts - self.lag).date())
        s = self.frame[column].loc[:cutoff].dropna()
        past = s.loc[: cutoff - pd.Timedelta(days=days)]
        if s.empty or past.empty:
            return None
        return float(s.iloc[-1] - past.iloc[-1])
