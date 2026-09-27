"""Boucle live (paper, démo MT5 ou réel) — le même TradingEngine que le backtest.

Chaque cycle (``poll_seconds``) :
  1. heartbeat vers le pont (mode remote)
  2. nouvelles bougies M1 -> moteur (en mode rattrapage si elles sont anciennes)
  3. commandes du téléphone (pause / kill / reset_halt), même marché fermé
  4. calendrier économique (rafraîchi toutes les heures, fail-closed)
  5. réconciliation (positions sans SL, trop de positions) -> alerte critique
  6. publication de l'état (bot + dashboard) et ping du dead man's switch

Toute exception d'un cycle est journalisée et alertée, puis la boucle continue :
un bug ponctuel ne doit pas laisser des positions sans surveillance.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Protocol

from tradebot.alerts.healthcheck import Healthcheck
from tradebot.bridge import protocol as P
from tradebot.core.config import AppConfig
from tradebot.core.engine import Notifier, TradingEngine
from tradebot.core.logging import get_logger
from tradebot.core.models import Bar
from tradebot.fundamental.calendar import EconomicEvent, EventCalendar
from tradebot.live.status import StatusFile

log = get_logger(__name__)

CATCH_UP_AFTER = timedelta(minutes=3)


class BarFeed(Protocol):
    def poll(self) -> list[Bar]: ...


class AdapterFeed:
    """Flux direct depuis un adaptateur exposant ``completed_m1_bars`` (MT5 local)."""

    def __init__(self, adapter, count: int = 60) -> None:
        self.adapter = adapter
        self.count = count
        self.last: datetime | None = None

    def history(self, count: int) -> list[Bar]:
        bars = self.adapter.completed_m1_bars(count)
        if bars:
            self.last = bars[-1].ts
        return bars

    def poll(self) -> list[Bar]:
        bars = [b for b in self.adapter.completed_m1_bars(self.count) if self.last is None or b.ts > self.last]
        if bars:
            self.last = bars[-1].ts
        return bars


class LiveRunner:
    def __init__(
        self,
        cfg: AppConfig,
        engine: TradingEngine,
        feed: BarFeed,
        calendar: EventCalendar,
        status: StatusFile,
        notifier: Notifier,
        *,
        health: Healthcheck | None = None,
        calendar_fetcher: Callable[[], list[EconomicEvent]] | None = None,
        heartbeat: Callable[[], None] | None = None,
        bridge_alive: Callable[[], bool] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.cfg = cfg
        self.engine = engine
        self.feed = feed
        self.calendar = calendar
        self.status = status
        self.notifier = notifier
        self.health = health or Healthcheck(None)
        self.calendar_fetcher = calendar_fetcher
        self.heartbeat = heartbeat
        self.bridge_alive = bridge_alive
        self.clock = clock
        self._next_calendar = datetime.min.replace(tzinfo=UTC)
        self._next_reconcile = datetime.min.replace(tzinfo=UTC)
        self._alerted: set[str] = set()

    # ------------------------------------------------------------------
    def _alert_once(self, key: str, text: str) -> None:
        if key not in self._alerted:
            self._alerted.add(key)
            self.notifier.send("critical", text)

    def _clear(self, key: str) -> None:
        self._alerted.discard(key)

    def refresh_calendar(self, now: datetime) -> None:
        if self.calendar_fetcher is None or now < self._next_calendar:
            return
        try:
            events = self.calendar_fetcher()
            self.calendar.merge_events(events, refreshed_at=now)
            self._next_calendar = now + timedelta(hours=1)
            self._clear("calendar")
        except Exception as e:
            self._next_calendar = now + timedelta(minutes=10)
            log.error("calendar_refresh_failed", error=str(e))
            if self.calendar.check(now).blocked and "fail-closed" in self.calendar.check(now).reason:
                self._alert_once("calendar", f"⚠️ Calendrier indisponible : entrées bloquées (fail-closed). {e}")

    def reconcile(self, now: datetime) -> None:
        if now < self._next_reconcile:
            return
        self._next_reconcile = now + timedelta(seconds=self.cfg.live.reconcile_seconds)
        positions = self.engine.broker.positions()
        naked = [p for p in positions if not p.stop_loss]
        if naked:
            self._alert_once("naked", f"🚨 {len(naked)} position(s) SANS stop-loss : "
                             f"{[p.position_id for p in naked]}. Vérifie dans l'app MT5 !")
        else:
            self._clear("naked")
        if len(positions) > self.cfg.risk.max_open_positions:
            self._alert_once("too_many", f"🚨 {len(positions)} positions ouvertes "
                             f"(max {self.cfg.risk.max_open_positions}) : incohérence à vérifier.")
        else:
            self._clear("too_many")

    def publish(self, now: datetime) -> None:
        e = self.engine
        try:
            acct = asdict(e.broker.account())
            positions = [P.encode(p) for p in e.broker.positions()]
        except Exception as ex:
            acct, positions = {"error": str(ex)}, []
        chk = self.calendar.check(now)
        self.status.write({
            "mode": self.cfg.live.mode,
            "strategy": e.strategy.name,
            "account": acct,
            "positions": positions,
            "risk": asdict(e.risk.state),
            "control": asdict(e._flags()),
            "last_bar_ts": e.last_bar.ts.isoformat() if e.last_bar else None,
            "stats": asdict(e.stats),
            "bridge_alive": self.bridge_alive() if self.bridge_alive else None,
            "calendar": {
                "status": f"BLOQUÉ : {chk.reason}" if chk.blocked else "ok",
                "last_refresh": self.calendar.last_refresh.isoformat() if self.calendar.last_refresh else None,
                "upcoming": [{"ts": ev.ts.isoformat(), "title": ev.title}
                             for ev in self.calendar.upcoming(now, 24)],
            },
        })

    def cycle(self) -> None:
        now = self.clock()
        if self.heartbeat:
            self.heartbeat()
        for bar in self.feed.poll():
            if self.engine.last_bar is not None and bar.ts <= self.engine.last_bar.ts:
                continue
            fresh = now - (bar.ts + timedelta(minutes=1)) <= CATCH_UP_AFTER
            self.engine.on_m1_bar(bar, allow_entries=fresh)
        self.engine.apply_control(now)
        self.refresh_calendar(now)
        self.reconcile(now)
        self.publish(now)
        self.health.ping()

    def run_forever(self) -> None:  # pragma: no cover - boucle infinie
        log.info("live_started", mode=self.cfg.live.mode, strategy=self.engine.strategy.name)
        self.notifier.send("critical", f"▶️ Moteur démarré ({self.cfg.live.mode}, {self.engine.strategy.name})")
        errors = 0
        while True:
            try:
                self.cycle()
                errors = 0
            except Exception as e:
                errors += 1
                log.exception("live_cycle_error")
                if errors in (1, 10, 100):
                    self.notifier.send("critical", f"❗ Erreur du moteur ({errors}x) : {e}")
                self.health.ping(fail=errors >= 10)
            time.sleep(self.cfg.live.poll_seconds)
