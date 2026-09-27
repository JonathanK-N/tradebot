"""Diagnostic de la connexion MT5 avant de brancher le pont.

Se connecte au terminal DÉJÀ ouvert et connecté (aucun mot de passe requis) et
répond aux questions qui bloquent un premier démarrage :
- le terminal est-il joignable et le trading algorithmique autorisé ?
- le compte est-il un compte DÉMO ?
- sous quel nom ce broker cote-t-il l'or ?
- quelles sont les vraies spécifications du contrat (taille, pas de volume, horaires) ?
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import ModuleType
from typing import Any

GOLD_PATTERNS = ("*XAU*", "*GOLD*")


@dataclass
class Mt5Report:
    connected: bool
    error: str = ""
    is_demo: bool | None = None
    server: str = ""
    company: str = ""
    currency: str = ""
    leverage: int | None = None
    algo_trading_allowed: bool | None = None
    gold_symbols: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.connected and bool(self.is_demo) and bool(self.algo_trading_allowed) and bool(self.gold_symbols)

    def to_text(self) -> str:
        if not self.connected:
            return (f"❌ Terminal MT5 injoignable : {self.error}\n"
                    "→ Ouvre MetaTrader 5, connecte-toi au compte DÉMO, puis relance la commande.")
        lines = [
            f"Broker : {self.company} — serveur {self.server}",
            f"Compte : {'DÉMO ✅' if self.is_demo else 'RÉEL ⚠️ (le verrou refusera tout ordre)'}"
            f" — devise {self.currency}, levier 1:{self.leverage}",
            "Trading algorithmique autorisé : "
            + ("✅" if self.algo_trading_allowed else "❌ bouton « Algo Trading » à activer"),
            "",
            "Symboles or trouvés :" if self.gold_symbols else "❌ Aucun symbole or trouvé chez ce broker.",
        ]
        for s in self.gold_symbols:
            lines.append(f"  • {s['name']} — contrat {s['contract_size']} oz/lot, volume min {s['volume_min']} "
                         f"pas {s['volume_step']}, spread actuel {s['spread_points']} pts, "
                         f"{'négociable' if s['tradable'] else 'NON négociable'}")
        if self.gold_symbols:
            best = self.gold_symbols[0]["name"]
            lines += ["", f"→ Dans config/default.yaml : instrument.broker_symbol: {best}"]
        lines += ["", "✅ Prêt pour le pont" if self.ok else "⚠️ Corrige les points ❌ avant de lancer le pont"]
        return "\n".join(lines)


def check_mt5(mt5: ModuleType | Any, path: str | None = None) -> Mt5Report:
    kwargs = {"path": path} if path else {}
    if not mt5.initialize(**kwargs):
        return Mt5Report(False, error=str(mt5.last_error()))
    try:
        acc = mt5.account_info()
        term = mt5.terminal_info()
        if acc is None:
            return Mt5Report(False, error="terminal ouvert mais aucun compte connecté")
        symbols: dict[str, Any] = {}
        for pattern in GOLD_PATTERNS:
            for s in mt5.symbols_get(pattern) or ():
                symbols[s.name] = s
        gold = []
        for s in symbols.values():
            if "XAG" in s.name.upper():  # argent, pas or
                continue
            gold.append({
                "name": s.name,
                "contract_size": float(s.trade_contract_size),
                "volume_min": float(s.volume_min),
                "volume_step": float(s.volume_step),
                "spread_points": int(getattr(s, "spread", 0)),
                "tradable": int(getattr(s, "trade_mode", 0)) != int(getattr(mt5, "SYMBOL_TRADE_MODE_DISABLED", 0)),
            })
        # symboles « USD » d'abord (XAUUSD) : c'est l'instrument visé
        gold.sort(key=lambda g: (("USD" not in g["name"].upper()), len(g["name"])))
        return Mt5Report(
            connected=True,
            is_demo=acc.trade_mode == getattr(mt5, "ACCOUNT_TRADE_MODE_DEMO", 0),
            server=str(acc.server),
            company=str(getattr(acc, "company", "")),
            currency=str(acc.currency),
            leverage=int(getattr(acc, "leverage", 0) or 0),
            algo_trading_allowed=bool(getattr(term, "trade_allowed", False)) and bool(
                getattr(acc, "trade_expert", True)),
            gold_symbols=gold,
        )
    finally:
        mt5.shutdown()
