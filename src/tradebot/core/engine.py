"""Orchestrateur UNIQUE, identique en backtest, en paper et en réel.

Il ne connaît que des interfaces : Strategy, RiskManager, BrokerAdapter, Journal,
EventCalendar, Notifier. En backtest on lui donne un SimBroker et des données
historiques ; en réel, un adaptateur MT5 et le flux live. Le chemin de code de la
décision est donc strictement le même -> ce qui est testé est ce qui tourne.

Cycle à chaque bougie M1 COMPLÈTE :
  1. bougie de la stratégie terminée par un trou de données ? -> la traiter
  2. broker.on_bar : exécutions, stops, TP, swaps (simulateur uniquement)
  3. trades clôturés -> journal + alertes
  4. equity -> module de risque (limites, drawdown) -> fermeture forcée si besoin
  5. drapeaux de contrôle (pause / kill depuis le téléphone)
  6. agrégation -> si la bougie de la stratégie est complète : signal -> risque -> ordre
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol

from tradebot.analysis.technical import BarAggregator, RollingMedian
from tradebot.core.logging import get_logger
from tradebot.core.models import Bar, ExitReason, MarketSnapshot, RiskDecision
from tradebot.execution.base import BrokerAdapter
from tradebot.fundamental.calendar import EventCalendar
from tradebot.journal.store import Journal
from tradebot.risk.killswitch import ControlFile, ControlFlags
from tradebot.risk.manager import RiskManager
from tradebot.strategy.base import Strategy, StrategyContext

log = get_logger(__name__)


class Notifier(Protocol):
    def send(self, level: str, text: str) -> None: ...


class NullNotifier:
    def send(self, level: str, text: str) -> None:
        pass


@dataclass(slots=True)
class EngineStats:
    bars: int = 0
    signals: int = 0
    approved: int = 0
    rejected: int = 0
    stale_signals: int = 0
    reject_reasons: dict[str, int] = field(default_factory=dict)


class TradingEngine:
    MAX_SIGNAL_AGE = timedelta(minutes=5)

    def __init__(
        self,
        strategy: Strategy,
        risk: RiskManager,
        broker: BrokerAdapter,
        calendar: EventCalendar,
        journal: Journal,
        *,
        control: ControlFile | None = None,
        notifier: Notifier | None = None,
        extra_spread: float = 0.0,
        spread_window: int = 1440,
    ) -> None:
        self.strategy = strategy
        self.risk = risk
        self.broker = broker
        self.calendar = calendar
        self.journal = journal
        self.control = control
        self.notifier = notifier or NullNotifier()
        self.extra_spread = extra_spread
        self.agg = BarAggregator(strategy.timeframe_minutes)
        self.spreads = RollingMedian(spread_window)
        self.stats = EngineStats()
        self.last_bar: Bar | None = None
        self._allow_entries = True

    # ------------------------------------------------------------------
    def warmup(self, bars: list[Bar]) -> None:
        """Préchauffe les indicateurs sur l'historique, SANS aucune décision de trading."""
        for bar in bars:
            self.spreads.update(bar.spread + self.extra_spread)
            for done in (self.agg.flush_if_new_period(bar), self.agg.add(bar)):
                if done is not None:
                    self.strategy.on_bar(done, StrategyContext())
        if bars:
            self.last_bar = bars[-1]

    def on_m1_bar(self, bar: Bar, *, allow_entries: bool = True) -> None:
        """``allow_entries=False`` : rattrapage de bougies arrivées en retard (reconnexion) :
        la stratégie suit le marché mais aucune entrée n'est tentée sur des données anciennes."""
        self._allow_entries = allow_entries
        self.stats.bars += 1
        self.spreads.update(bar.spread + self.extra_spread)
        now = bar.ts  # instant de traitement = ouverture de cette minute

        done = self.agg.flush_if_new_period(bar)
        if done is not None:
            self._on_strategy_bar(done, now)

        self.broker.on_bar(bar)
        self.last_bar = bar
        close_ts = bar.ts + timedelta(minutes=1)
        self._collect_trades()

        acct = self.broker.account()
        self.journal.equity_point(close_ts, acct.balance, acct.equity)
        for ev in self.risk.on_equity(close_ts, acct.equity):
            self.journal.event(close_ts, "risk_event", {"event": ev})
            self.notifier.send("critical", f"⚠️ Risque : {ev}")
            if self.broker.positions():
                self._flatten(close_ts, ExitReason.KILL_SWITCH, f"limite de risque : {ev}")

        self.apply_control(close_ts)

        done = self.agg.add(bar)
        if done is not None:
            self._on_strategy_bar(done, close_ts)

    def apply_control(self, ts: datetime) -> None:
        """Applique les commandes du téléphone. Appelé à chaque bougie ET à chaque
        cycle live, même marché fermé (le kill switch ne doit pas attendre une bougie)."""
        flags = self._flags()
        if flags.flatten_requested:
            self._flatten(ts, ExitReason.KILL_SWITCH, f"kill switch : {flags.reason}")
            if self.control:
                self.control.ack_flatten()
        if flags.reset_halt_requested and self.control:
            self.risk.reset_halt(flags.updated_by)
            self.control.ack_reset_halt()
            self.journal.event(ts, "reset_halt", {"by": flags.updated_by})
            self.notifier.send("critical", f"HALT levé par {flags.updated_by}")

    def finish(self) -> None:
        """Fin de backtest : clôture des positions restantes."""
        if self.last_bar is not None:
            end = self.last_bar.ts + timedelta(minutes=1)
            self.broker.close_all(ExitReason.END_OF_DATA, end)
            self._collect_trades()

    # ------------------------------------------------------------------
    def _flags(self) -> ControlFlags:
        return self.control.read() if self.control else ControlFlags()

    def _flatten(self, ts: datetime, reason: ExitReason, why: str) -> None:
        results = self.broker.close_all(reason, ts)
        self._collect_trades()
        self.journal.event(ts, "flatten", {"why": why, "closed": len(results),
                                           "ok": all(r.ok for r in results)})
        if results:
            self.notifier.send("critical", f"🛑 Positions fermées ({len(results)}) — {why}")

    def _collect_trades(self) -> None:
        for t in self.broker.drain_closed_trades():
            self.journal.trade(t)
            self.notifier.send(
                "info",
                f"{'✅' if t.pnl >= 0 else '❌'} {t.strategy} {t.side.value.upper()} {t.volume} lot "
                f"clôturé ({t.exit_reason.value}) : {t.pnl:+.2f} {'USD'} ({t.r_multiple:+.2f} R)",
            )

    def _on_strategy_bar(self, sbar: Bar, now: datetime) -> None:
        positions = self.broker.positions()
        signal = self.strategy.on_bar(sbar, StrategyContext(open_positions=len(positions)))
        if signal is None:
            return
        self.stats.signals += 1
        if now - signal.ts > self.MAX_SIGNAL_AGE or not self._allow_entries:
            self.stats.stale_signals += 1
            self.journal.event(now, "stale_signal", signal, signal.correlation_id)
            return

        last = self.last_bar or sbar
        market = MarketSnapshot(
            ts=signal.ts,
            bid=last.close,
            spread=last.spread + self.extra_spread,
            median_spread=self.spreads.value,
        )
        flags = self._flags()
        decision: RiskDecision = self.risk.evaluate(
            signal, self.broker.account(), market, positions, self.calendar.check(signal.ts),
            paused=flags.paused,
        )
        self.journal.decision(decision)
        if not decision.approved or decision.intent is None:
            self.stats.rejected += 1
            for r in decision.reasons:
                key = r.split(":", 1)[0]
                self.stats.reject_reasons[key] = self.stats.reject_reasons.get(key, 0) + 1
            log.info("signal_rejected", strategy=signal.strategy, reasons=list(decision.reasons))
            return

        self.stats.approved += 1
        result = self.broker.submit(decision.intent)
        self.journal.event(signal.ts, "order", {"intent": decision.intent, "ok": result.ok,
                                                "message": result.message}, signal.correlation_id)
        if result.ok:
            self.risk.register_entry()
            self.notifier.send(
                "signal",
                f"📈 {signal.strategy} {signal.side.value.upper()} {decision.intent.volume} lot "
                f"SL {decision.intent.stop_loss} TP {decision.intent.take_profit} — {signal.reason}",
            )
        else:
            self.notifier.send("critical", f"Ordre refusé par le broker : {result.message}")
