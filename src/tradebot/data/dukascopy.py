"""Téléchargement de l'historique minute BID/ASK depuis Dukascopy (gratuit).

Format : un fichier ``.bi5`` (LZMA) par jour et par côté (BID/ASK), contenant des
enregistrements de 24 octets big-endian :
    uint32 secondes depuis minuit UTC, uint32 open, uint32 close, uint32 low,
    uint32 high, float32 volume
⚠️ Le mois dans l'URL commence à 00 (janvier = 00).

Le prix est un entier à diviser par ``PRICE_DIVISOR[symbol]``. Un contrôle de
vraisemblance refuse les données si le prix médian sort d'une plage plausible
(protection contre une erreur d'échelle silencieuse).

Limites :
- Ce sont les prix de Dukascopy, PAS ceux de ton broker (spreads, cotations et
  horaires diffèrent). On calibre l'écart avec l'historique MT5 (semaine 9).
- Serveur gratuit : on limite le rythme des requêtes et on met tout en cache disque.
"""

from __future__ import annotations

import lzma
import struct
import time as _time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pandas as pd

BASE = "https://datafeed.dukascopy.com/datafeed/{sym}/{y:04d}/{m:02d}/{d:02d}/{side}_candles_min_1.bi5"
RECORD = struct.Struct(">IIIIIf")
PRICE_DIVISOR = {"XAUUSD": 1000.0, "XAGUSD": 1000.0, "EURUSD": 100000.0, "USDJPY": 1000.0}
PLAUSIBLE = {"XAUUSD": (200.0, 20000.0), "XAGUSD": (3.0, 500.0)}


def decode_candles(raw: bytes, day: date, divisor: float) -> pd.DataFrame:
    if not raw:
        return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])
    data = lzma.decompress(raw)
    n = len(data) // RECORD.size
    rows = [RECORD.unpack_from(data, i * RECORD.size) for i in range(n)]
    base = datetime(day.year, day.month, day.day, tzinfo=UTC)
    return pd.DataFrame(
        {
            "ts": [base + timedelta(seconds=r[0]) for r in rows],
            "open": [r[1] / divisor for r in rows],
            "close": [r[2] / divisor for r in rows],
            "low": [r[3] / divisor for r in rows],
            "high": [r[4] / divisor for r in rows],
            "volume": [r[5] for r in rows],
        }
    )


class DukascopyDownloader:
    def __init__(self, cache_dir: str | Path, *, pause_s: float = 0.25, timeout: float = 30.0,
                 retries: int = 4) -> None:
        self.cache = Path(cache_dir)
        self.pause_s = pause_s
        self.retries = retries
        self.client = httpx.Client(timeout=timeout, headers={"User-Agent": "tradebot/0.1"})

    def _raw(self, symbol: str, day: date, side: str) -> bytes:
        path = self.cache / symbol / f"{day:%Y}" / f"{day:%Y%m%d}_{side}.bi5"
        if path.exists():
            return path.read_bytes()
        url = BASE.format(sym=symbol, y=day.year, m=day.month - 1, d=day.day, side=side)
        delay = 1.0
        for attempt in range(self.retries):
            try:
                r = self.client.get(url)
                if r.status_code == 404:
                    content = b""
                else:
                    r.raise_for_status()
                    content = r.content
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
                _time.sleep(self.pause_s)
                return content
            except httpx.HTTPError:
                if attempt == self.retries - 1:
                    raise
                _time.sleep(delay)
                delay *= 2
        return b""

    def day(self, symbol: str, day: date) -> pd.DataFrame:
        div = PRICE_DIVISOR[symbol]
        bid = decode_candles(self._raw(symbol, day, "BID"), day, div)
        ask = decode_candles(self._raw(symbol, day, "ASK"), day, div)
        if bid.empty or ask.empty:
            return pd.DataFrame()
        df = bid.merge(ask[["ts", "open", "close"]], on="ts", suffixes=("", "_ask"))
        # spread moyen de la minute ≈ moyenne des écarts à l'ouverture et à la clôture
        df["spread"] = ((df["open_ask"] - df["open"]) + (df["close_ask"] - df["close"])) / 2
        df = df.drop(columns=["open_ask", "close_ask"])
        # Dukascopy remplit les minutes sans cotation (week-end, pause) avec volume 0
        df = df[df["volume"] > 0]
        return df[["ts", "open", "high", "low", "close", "spread", "volume"]]

    def range(self, symbol: str, start: date, end: date, progress: bool = True) -> pd.DataFrame:
        frames = []
        d = start
        while d <= end:
            if d.weekday() != 5:  # samedi : aucune cotation
                frames.append(self.day(symbol, d))
                if progress and d.day == 1:
                    print(f"  {symbol} {d:%Y-%m} ...", flush=True)
            d += timedelta(days=1)
        frames = [f for f in frames if not f.empty]
        if not frames:
            return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "spread", "volume"])
        df = pd.concat(frames, ignore_index=True).sort_values("ts").drop_duplicates("ts")
        lo, hi = PLAUSIBLE.get(symbol, (0.0, float("inf")))
        med = float(df["close"].median())
        if not lo <= med <= hi:
            raise ValueError(f"prix médian {med} hors plage plausible {lo}-{hi} : erreur d'échelle ?")
        return df.reset_index(drop=True)
