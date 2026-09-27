"""Module de risque : droit de VETO sur toute décision.

Il ne « prédit » rien. Il répond à une seule question : « Ce trade, à cette
taille, maintenant, est-il compatible avec la survie du compte ? ».
Chaque refus est motivé (journalisé, visible sur le téléphone).

Ordre des contrôles : du plus grave (HALT) au plus fin (sizing). Tous les motifs
de refus sont collectés, pas seulement le premier, pour faciliter l'analyse.
"""

from __future__ import annotations

from datetime import datetime

from tradebot.core.config import RiskConfig
from tradebot.core.instrument import InstrumentSpec
from tradebot.core.logging import get_logger
from tradebot.core.models import (
    AccountState,
    MarketSnapshot,
    OrderIntent,
    Position,
    RiskDecision,
    Side,
    Signal,
)
from tradebot.core.sessions import is_friday_cutoff, is_market_open, is_rollover_window
from tradebot.fundamental.calendar import BlackoutCheck
from tradebot.risk.state import RiskState, StateStore

log = get_logger(__name__)


class RiskManager:
    def __init__(self, cfg: RiskConfig, instrument: InstrumentSpec, store: StateStore,
                 initial_equity: float) -> None:
        self.cfg = cfg
        self.inst = instrument
        self.store = store
        self.state: RiskState = store.load(initial_equity)

    # ------------------------------------------------------------------ état
    def on_equity(self, ts: datetime, equity: float) -> list[str]:
        """À appeler à chaque mise à jour d'equity (chaque minute en live/backtest).

        Renvoie la liste des événements de risque nouvellement déclenchés
        (pour alerte), et persiste l'état.
        """
        s = self.state
        events: list[str] = []
        s.roll(ts, equity)
        s.peak_equity = max(s.peak_equity, equity)

        dd_pct = (s.peak_equity - equity) / s.peak_equity * 100 if s.peak_equity > 0 else 0.0
        if not s.halted and dd_pct >= self.cfg.max_drawdown_pct:
            s.halted = True
            s.halt_reason = f"drawdown {dd_pct:.2f}% >= {self.cfg.max_drawdown_pct}%"
            events.append(f"HALT: {s.halt_reason}")

        day_pct = self._pct(equity, s.day_start_equity)
        if not s.day_locked and -day_pct >= self.cfg.max_daily_loss_pct:
            s.day_locked = True
            s.day_lock_reason = f"perte du jour {day_pct:.2f}%"
            events.append(f"DAY_LOCK: {s.day_lock_reason}")

        week_pct = self._pct(equity, s.week_start_equity)
        if not s.week_locked and -week_pct >= self.cfg.max_weekly_loss_pct:
            s.week_locked = True
            events.append(f"WEEK_LOCK: perte de la semaine {week_pct:.2f}%")

        self.store.save(s)
        for e in events:
            log.warning("risk_event", detail=e, equity=equity)
        return events

    @staticmethod
    def _pct(equity: float, ref: float) -> float:
        return (equity - ref) / ref * 100 if ref > 0 else 0.0

    def register_entry(self) -> None:
        self.state.trades_today += 1
        self.store.save(self.state)

    def reset_halt(self, by: str) -> None:
        """Levée manuelle du HALT (après revue humaine). Le pic est réinitialisé
        à l'equity courante au prochain ``on_equity`` via ``peak_equity=0``."""
        log.warning("risk_halt_reset", by=by, previous_reason=self.state.halt_reason)
        self.state.halted = False
        self.state.halt_reason = ""
        self.state.peak_equity = 0.0
        self.store.save(self.state)

    # -------------------------------------------------------------- décision
    def evaluate(
        self,
        signal: Signal,
        account: AccountState,
        market: MarketSnapshot,
        open_positions: list[Position],
        news: BlackoutCheck,
        *,
        paused: bool = False,
    ) -> RiskDecision:
        c, s = self.cfg, self.state
        reasons: list[str] = []
        ts = signal.ts

        # 1. États bloquants globaux
        if paused:
            reasons.append("pause: kill switch / pause actif")
        if s.halted:
            reasons.append(f"halt: {s.halt_reason}")
        if s.day_locked:
            reasons.append(f"limite_jour: {s.day_lock_reason}")
        if s.week_locked:
            reasons.append("limite_semaine: perte hebdomadaire max atteinte")
        if s.trades_today >= c.max_trades_per_day:
            reasons.append(f"max_trades: {s.trades_today} trades aujourd'hui (max {c.max_trades_per_day})")
        if len(open_positions) >= c.max_open_positions:
            reasons.append(f"max_positions: {len(open_positions)} ouverte(s) (max {c.max_open_positions})")

        # 2. Calendrier / conditions de marché
        if not is_market_open(ts):
            reasons.append("marche_ferme: hors horaires")
        if is_rollover_window(ts, c.rollover_before_min, c.rollover_after_min):
            reasons.append("rollover: fenêtre 17:00 NY")
        if is_friday_cutoff(ts, c.friday_cutoff_ny):
            reasons.append("vendredi: après l'heure limite (risque de gap du week-end)")
        if news.blocked:
            reasons.append(f"news: {news.reason}")
        if market.spread > c.max_spread_abs:
            reasons.append(f"spread: {market.spread:.2f} > max {c.max_spread_abs:.2f}")
        if market.median_spread and market.spread > c.max_spread_median_mult * market.median_spread:
            reasons.append(
                f"spread_median: {market.spread:.2f} > {c.max_spread_median_mult}× médiane "
                f"({market.median_spread:.2f})"
            )

        # 3. Cohérence du signal
        stop_dist = signal.stop_distance
        if signal.side is Side.BUY and signal.stop_loss >= signal.entry_ref:
            reasons.append("stop_invalide: stop au-dessus de l'entrée pour un achat")
        if signal.side is Side.SELL and signal.stop_loss <= signal.entry_ref:
            reasons.append("stop_invalide: stop sous l'entrée pour une vente")
        if stop_dist < c.min_stop_distance:
            reasons.append(f"stop_trop_proche: {stop_dist:.2f} < {c.min_stop_distance}")
        if signal.take_profit is not None and stop_dist > 0:
            rr = abs(signal.take_profit - signal.entry_ref) / stop_dist
            tp_ok = (signal.take_profit - signal.entry_ref) * signal.side.sign > 0
            if not tp_ok:
                reasons.append("tp_invalide: take-profit du mauvais côté")
            elif rr < c.min_reward_risk:
                reasons.append(f"rr: ratio gain/risque {rr:.2f} < {c.min_reward_risk}")

        # 4. Sizing (sur l'EQUITY, pas le solde : les pertes latentes comptent)
        volume = 0.0
        risk_amount = 0.0
        if stop_dist > 0 and account.equity > 0:
            budget = account.equity * c.risk_per_trade_pct / 100
            eff_stop = stop_dist + c.sizing_slippage_buffer + market.spread
            raw = budget / (eff_stop * self.inst.contract_size)
            volume = min(self.inst.round_volume_down(raw), c.max_volume_lots)
            volume = self.inst.round_volume_down(volume)
            risk_amount = self.inst.risk_for(eff_stop, volume)
            if volume < self.inst.volume_min:
                reasons.append(
                    f"capital_insuffisant: {raw:.4f} lot calculé < minimum {self.inst.volume_min} "
                    f"(risque min = {self.inst.risk_for(eff_stop, self.inst.volume_min):.2f} "
                    f"{account.currency})"
                )
            else:
                margin = self.inst.margin_required(signal.entry_ref, volume)
                max_margin = account.equity * c.max_margin_usage_pct / 100
                if account.margin_used + margin > max_margin:
                    reasons.append(
                        f"marge: {account.margin_used + margin:.0f} > {c.max_margin_usage_pct}% "
                        "de l'equity"
                    )
        else:
            reasons.append("stop_invalide: equity ou distance de stop invalide")

        if reasons:
            return RiskDecision(False, signal, tuple(reasons))

        intent = OrderIntent(
            ts=ts,
            symbol=signal.symbol,
            side=signal.side,
            volume=volume,
            stop_loss=self.inst.round_price(signal.stop_loss),
            take_profit=(
                self.inst.round_price(signal.take_profit) if signal.take_profit else None
            ),
            strategy=signal.strategy,
            correlation_id=signal.correlation_id,
            expire_at=(ts + signal.expire_after) if signal.expire_after else None,
            risk_amount=risk_amount,
        )
        return RiskDecision(True, signal, ("approuvé",), intent)
