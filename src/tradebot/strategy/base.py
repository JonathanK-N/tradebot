"""Interface commune des stratégies.

Contrat :
- ``on_bar`` reçoit UNIQUEMENT des bougies COMPLÈTES du timeframe de la stratégie,
  dans l'ordre chronologique. Aucune autre source de données de marché.
- Le signal est horodaté à la CLÔTURE de la bougie (``bar.ts + timeframe``) ;
  l'exécution a lieu au plus tôt à l'ouverture de la minute suivante.
- Une stratégie ne dimensionne JAMAIS une position et ne connaît pas le compte :
  c'est le rôle exclusif du module de risque.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from tradebot.core.models import Bar, Signal


@dataclass(frozen=True, slots=True)
class StrategyContext:
    open_positions: int = 0


class Strategy(ABC):
    name: str = "base"
    timeframe_minutes: int = 15

    def __init__(self, symbol: str, **params: Any) -> None:
        self.symbol = symbol
        unknown = set(params) - set(self.default_params())
        if unknown:
            raise ValueError(f"paramètres inconnus pour {self.name}: {sorted(unknown)}")
        self.params: dict[str, Any] = {**self.default_params(), **params}

    @classmethod
    @abstractmethod
    def default_params(cls) -> dict[str, Any]: ...

    @abstractmethod
    def on_bar(self, bar: Bar, ctx: StrategyContext) -> Signal | None: ...

    def close_time(self, bar: Bar) -> datetime:
        return bar.ts + timedelta(minutes=self.timeframe_minutes)
