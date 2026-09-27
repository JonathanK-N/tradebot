"""Spécifications de contrat.

⚠️ Les valeurs par défaut de XAUUSD (100 oz/lot, pas de 0,01 lot) sont les plus
répandues, mais chaque broker a les siennes. En réel, elles DOIVENT être
relues depuis ``mt5.symbol_info()`` (voir execution/mt5_adapter.py) et le système
alerte si elles changent.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class InstrumentSpec:
    symbol: str
    contract_size: float = 100.0  # onces par lot
    tick_size: float = 0.01
    volume_min: float = 0.01
    volume_step: float = 0.01
    volume_max: float = 50.0
    leverage: float = 20.0  # levier effectif du compte sur cet actif (marge = notionnel / levier)
    quote_currency: str = "USD"

    def pnl(self, price_move: float, volume: float) -> float:
        """P&L en devise de cotation pour un mouvement de prix donné."""
        return price_move * volume * self.contract_size

    def risk_for(self, stop_distance: float, volume: float) -> float:
        return abs(stop_distance) * volume * self.contract_size

    def round_volume_down(self, volume: float) -> float:
        """Arrondi TOUJOURS vers le bas : on ne dépasse jamais le risque demandé."""
        if volume <= 0:
            return 0.0
        steps = math.floor(volume / self.volume_step + 1e-9)
        vol = round(steps * self.volume_step, 8)
        return min(vol, self.volume_max)

    def round_price(self, price: float) -> float:
        return round(round(price / self.tick_size) * self.tick_size, 8)

    def margin_required(self, price: float, volume: float) -> float:
        return price * volume * self.contract_size / self.leverage


XAUUSD = InstrumentSpec(symbol="XAUUSD")
