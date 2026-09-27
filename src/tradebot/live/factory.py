"""Assemblage des composants live selon le mode, avec les verrous de sécurité.

VERROU COMPTE RÉEL (règle absolue n°6 : backtest -> walk-forward -> démo -> réel) :
le système REFUSE de trader sur un compte réel sauf si les DEUX conditions sont
réunies :
  - ``environment: live`` dans le YAML (versionné : la décision est tracée dans git) ;
  - variable d'environnement ``TRADEBOT_ALLOW_REAL_MONEY=oui-j-ai-valide-la-demo``.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path

from tradebot.alerts.healthcheck import Healthcheck
from tradebot.alerts.telegram import TelegramClient, TelegramNotifier
from tradebot.backtest.runner import instrument_from
from tradebot.core.config import AppConfig, Secrets
from tradebot.core.engine import NullNotifier, TradingEngine
from tradebot.core.kv import FileKV, RedisKV
from tradebot.core.logging import get_logger
from tradebot.execution.sim import SimBroker
from tradebot.fundamental.calendar import EventCalendar
from tradebot.fundamental.forexfactory import fetch_ff_week
from tradebot.journal.store import SqlJournal
from tradebot.live.runner import AdapterFeed, LiveRunner
from tradebot.live.status import StatusStore
from tradebot.risk.killswitch import ControlStore
from tradebot.risk.manager import RiskManager
from tradebot.risk.state import StateStore
from tradebot.strategy.registry import build_strategy

REAL_MONEY_TOKEN = "oui-j-ai-valide-la-demo"
log = get_logger(__name__)


class RealMoneyLocked(RuntimeError):
    pass


def assert_real_money_allowed(cfg: AppConfig, is_demo: bool) -> None:
    if is_demo:
        return
    if cfg.environment != "live" or os.environ.get("TRADEBOT_ALLOW_REAL_MONEY") != REAL_MONEY_TOKEN:
        raise RealMoneyLocked(
            "Compte RÉEL détecté mais non autorisé. Il faut environment: live dans le YAML ET "
            f"TRADEBOT_ALLOW_REAL_MONEY={REAL_MONEY_TOKEN}. As-tu validé 4 à 8 semaines de démo ?"
        )


def state_stores(cfg: AppConfig, secrets: Secrets) -> tuple[ControlStore, StatusStore]:
    """Stockage partagé du contrôle (pause/kill) et du statut, selon ``live.state_backend``."""
    if cfg.live.state_backend == "redis":
        if not secrets.redis_url:
            raise RuntimeError("state_backend=redis mais REDIS_URL est absent")
        kv = RedisKV(secrets.redis_url)
    else:
        kv = FileKV(cfg.live.state_dir)
    return ControlStore(kv, "control"), StatusStore(kv, "status")


def build_notifier(secrets: Secrets):
    if secrets.telegram_bot_token and secrets.telegram_allowed_chat_ids:
        return TelegramNotifier(TelegramClient(secrets.telegram_bot_token), secrets.telegram_allowed_chat_ids)
    return NullNotifier()


def build_runner(cfg: AppConfig, secrets: Secrets, warmup_bars: int = 20_000) -> LiveRunner:
    inst = instrument_from(cfg)
    state = Path(cfg.live.state_dir)
    notifier = build_notifier(secrets)
    mode = cfg.live.mode
    heartbeat = bridge_alive = None

    if mode == "mt5" or (mode == "paper" and not secrets.redis_url):
        from tradebot.execution.mt5_adapter import MT5Adapter

        adapter = MT5Adapter(cfg.instrument.broker_symbol, cfg.live.magic_number, inst)
        adapter.connect(secrets.mt5_login, secrets.mt5_password, secrets.mt5_server, secrets.mt5_path)
        live_spec = adapter.instrument_spec()
        if live_spec.contract_size != inst.contract_size or live_spec.volume_step != inst.volume_step:
            notifier.send("critical", f"⚠️ Spécification broker différente de la config : {live_spec}")
            inst = live_spec  # la vérité est chez le broker
        feed = AdapterFeed(adapter)
        history = feed.history(min(warmup_bars, 99_000))
        if mode == "mt5":
            assert_real_money_allowed(cfg, adapter.is_demo())
            broker = adapter
        else:
            broker = SimBroker(inst, cfg.costs, cfg.account.initial_balance)
    elif mode in ("remote", "paper"):
        from tradebot.bridge.client import RemoteBroker, RemoteFeed
        from tradebot.bridge.transport import RedisTransport

        if not secrets.redis_url:
            raise RuntimeError("REDIS_URL requis en mode remote")
        transport = RedisTransport(secrets.redis_url)
        remote = RemoteBroker(transport)
        feed = RemoteFeed(transport)
        history = feed.history()
        heartbeat, bridge_alive = remote.heartbeat, remote.bridge_alive
        broker = remote if mode == "remote" else SimBroker(inst, cfg.costs, cfg.account.initial_balance)
    else:
        raise ValueError(mode)

    journal = SqlJournal(secrets.database_url or cfg.journal_url)
    equity0 = broker.account().equity if mode != "paper" else cfg.account.initial_balance
    risk = RiskManager(cfg.risk, inst, StateStore(state / "risk_state.json"), equity0)
    calendar = EventCalendar(cfg.news, require_fresh=True)
    control, status = state_stores(cfg, secrets)
    strategy = build_strategy(cfg.strategy.name, inst.symbol, cfg.strategy.params)
    engine = TradingEngine(strategy, risk, broker, calendar, journal, control=control, notifier=notifier,
                           extra_spread=0.0 if mode != "paper" else cfg.costs.extra_spread)
    engine.warmup(history)
    return LiveRunner(cfg, engine, feed, calendar, status, notifier,
                      health=Healthcheck(secrets.healthcheck_url), calendar_fetcher=fetch_ff_week,
                      heartbeat=heartbeat, bridge_alive=bridge_alive)


def build_runner_with_retry(cfg: AppConfig, secrets: Secrets, *, sleep: Callable[[float], None] = time.sleep,
                            max_attempts: int | None = None, builder=build_runner,
                            status: StatusStore | None = None) -> LiveRunner:
    """Attend le pont MT5 / le terminal au lieu de planter.

    Sur Railway, un service qui s'arrête en erreur n'est redémarré que 10 fois : si le
    moteur démarre avant le pont Windows (cas normal), il doit PATIENTER, pas mourir.
    Les erreurs de configuration (verrou compte réel, secrets manquants) restent fatales.
    """
    notifier = build_notifier(secrets)
    if status is None:
        try:
            status = state_stores(cfg, secrets)[1]
        except RuntimeError:
            status = None
    attempt = 0
    while True:
        attempt += 1
        try:
            return builder(cfg, secrets)
        except (RealMoneyLocked, ValueError):
            raise
        except (ConnectionError, TimeoutError, OSError, RuntimeError) as e:
            if isinstance(e, RuntimeError) and "requis" in str(e):
                raise  # secret manquant : inutile de réessayer
            log.warning("live_waiting_for_broker", attempt=attempt, error=str(e))
            _publish_waiting(status, cfg, attempt, str(e))
            if attempt == 1:
                notifier.send("critical", (
                    "⏳ Moteur démarré, en attente du pont MT5.\n"
                    "Normal tant que le VPS Windows avec MetaTrader 5 n'est pas installé : "
                    "le moteur n'a encore ni prix ni broker, donc AUCUNE position ne peut être ouverte.\n"
                    f"Détail technique : {e}"))
            if max_attempts is not None and attempt >= max_attempts:
                raise
            sleep(cfg.live.bridge_wait_seconds)


def _publish_waiting(status: StatusStore | None, cfg: AppConfig, attempt: int, error: str) -> None:
    """Le dashboard et /status affichent « en attente du pont » au lieu de « moteur arrêté »."""
    if status is None:
        return
    try:
        status.write({"mode": cfg.live.mode, "strategy": cfg.strategy.name, "state": "waiting_for_bridge",
                      "waiting": {"attempt": attempt, "reason": error}, "positions": [], "account": {},
                      "risk": {}, "control": {}, "calendar": {"status": "inactif (moteur en attente)"}})
    except Exception as ex:  # l'état est informatif : ne jamais bloquer l'attente pour lui
        log.warning("status_publish_failed", error=str(ex))
