"""Générateur de données SYNTHÉTIQUES (tests, démonstration, contrôle du pipeline).

⚠️ Ces données n'ont AUCUNE valeur pour juger une stratégie : c'est une marche
aléatoire. Une stratégie « rentable » dessus est, par construction, un artefact
(chance, bug ou biais). C'est d'ailleurs un excellent test : sur des données
aléatoires, l'espérance d'une stratégie après coûts doit être <= 0.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from tradebot.data.quality import market_open_mask


def generate_m1(
    start: str = "2024-01-01",
    end: str = "2024-03-01",
    *,
    price: float = 2000.0,
    annual_vol: float = 0.15,
    seed: int = 42,
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, end, freq="min", tz="UTC", inclusive="left")
    idx = idx[market_open_mask(idx)]
    n = len(idx)
    hours = idx.hour.to_numpy()
    # volatilité intraday : Asie calme, Londres/NY actifs
    profile = np.where((hours >= 7) & (hours < 17), 1.6, 0.7)
    minutes_per_year = 252 * 23 * 60
    sigma = annual_vol / np.sqrt(minutes_per_year) * profile
    rets = rng.standard_normal(n) * sigma
    close = price * np.exp(np.cumsum(rets))
    open_ = np.concatenate([[price], close[:-1]])
    wick = np.abs(rng.standard_normal((2, n))) * sigma * close * 0.6
    high = np.maximum(open_, close) + wick[0]
    low = np.minimum(open_, close) - wick[1]
    spread = np.where((hours >= 7) & (hours < 17), 0.20, 0.35) + np.abs(rng.standard_normal(n)) * 0.05
    return pd.DataFrame(
        {
            "ts": idx,
            "open": open_.round(3),
            "high": high.round(3),
            "low": low.round(3),
            "close": close.round(3),
            "spread": spread.round(3),
            "volume": rng.integers(1, 100, n).astype(float),
        }
    )
