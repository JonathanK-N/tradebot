"""Stockage des bougies en Parquet, partitionné par année.

    data/bars/XAUUSD/M1/2024.parquet

Parquet : compact (~15 Mo/an de M1), rapide, lisible par pandas, DuckDB, Polars.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from tradebot.core.models import Bar

COLUMNS = ["ts", "open", "high", "low", "close", "spread", "volume"]


class BarStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root) / "bars"

    def _dir(self, symbol: str, tf: str = "M1") -> Path:
        return self.root / symbol / tf

    def write(self, symbol: str, df: pd.DataFrame, tf: str = "M1") -> list[Path]:
        """Fusionne avec l'existant (idempotent : relancer un téléchargement ne duplique rien)."""
        df = df[COLUMNS].copy()
        df["ts"] = pd.to_datetime(df["ts"], utc=True)
        out = []
        for year, part in df.groupby(df["ts"].dt.year):
            path = self._dir(symbol, tf) / f"{year}.parquet"
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                part = pd.concat([pd.read_parquet(path), part])
            part = part.sort_values("ts").drop_duplicates("ts", keep="last")
            part.to_parquet(path, index=False)
            out.append(path)
        return out

    def load(self, symbol: str, start: datetime | None = None, end: datetime | None = None,
             tf: str = "M1") -> pd.DataFrame:
        d = self._dir(symbol, tf)
        files = sorted(d.glob("*.parquet"))
        if start is not None:
            files = [f for f in files if int(f.stem) >= start.year]
        if end is not None:
            files = [f for f in files if int(f.stem) <= end.year]
        if not files:
            raise FileNotFoundError(f"aucune donnée {symbol} {tf} dans {d}")
        df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
        df["ts"] = pd.to_datetime(df["ts"], utc=True)
        if start is not None:
            df = df[df["ts"] >= pd.Timestamp(start)]
        if end is not None:
            df = df[df["ts"] < pd.Timestamp(end)]
        return df.sort_values("ts").reset_index(drop=True)


def iter_bars(df: pd.DataFrame) -> Iterator[Bar]:
    """Conversion rapide DataFrame -> Bar (itertuples est ~50× plus rapide qu'iterrows)."""
    ts = pd.to_datetime(df["ts"], utc=True)
    for t, o, h, lo, c, s, v in zip(
        ts.dt.to_pydatetime(), df["open"].to_numpy(), df["high"].to_numpy(), df["low"].to_numpy(),
        df["close"].to_numpy(), df["spread"].to_numpy(), df["volume"].to_numpy(), strict=True,
    ):
        yield Bar(t.astimezone(UTC), float(o), float(h), float(lo), float(c), float(s), float(v))
