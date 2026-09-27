"""Point d'entrée unique : ``tradebot <commande>``. ``tradebot --help`` pour la liste.

Conçu pour être lancé depuis un téléphone (Termius / SSH) : commandes courtes,
rapports écrits en Markdown dans ``reports/`` (lisibles sur GitHub ou via ``cat``).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import yaml

from tradebot.core.config import AppConfig, Secrets, load_config, load_dotenv
from tradebot.core.logging import configure_logging, get_logger

log = get_logger("cli")


def _date(s: str) -> date:
    return date.fromisoformat(s)


def _dt(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s).replace(tzinfo=UTC) if s else None


def _params(kv: list[str] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in kv or []:
        k, v = item.split("=", 1)
        out[k] = yaml.safe_load(v)  # "1.5" -> 1.5, "07:00" -> str
    return out


def _events(cfg: AppConfig, start: datetime | None, end: datetime | None):
    from tradebot.fundamental.calendar import load_events_csv
    from tradebot.fundamental.schedule import historical_events

    csv = Path(cfg.data_dir) / "calendar" / "history.csv"
    if csv.exists():
        return load_events_csv(csv)
    y0 = start.year if start else 2015
    y1 = end.year if end else datetime.now(UTC).year
    log.warning("calendar_history_csv_missing", fallback="FOMC + NFP approximatif")
    return historical_events(y0, y1)


def _load_bars(cfg: AppConfig, symbol: str | None, start, end):
    from tradebot.data.store import BarStore

    return BarStore(cfg.data_dir).load(symbol or cfg.instrument.symbol, start, end)


def _out_dir(name: str) -> Path:
    return Path("reports") / f"{datetime.now(UTC):%Y%m%d-%H%M%S}-{name}"


# ---------------------------------------------------------------- commandes
def cmd_data_download(a, cfg: AppConfig) -> None:
    from tradebot.data.dukascopy import DukascopyDownloader
    from tradebot.data.store import BarStore

    dl = DukascopyDownloader(Path(cfg.data_dir) / "raw" / "dukascopy")
    df = dl.range(a.symbol, a.start, a.end)
    paths = BarStore(cfg.data_dir).write(a.symbol, df)
    print(f"{len(df):,} bougies M1 écrites : {[str(p) for p in paths]}")


def cmd_data_synthetic(a, cfg: AppConfig) -> None:
    from tradebot.data.store import BarStore
    from tradebot.data.synthetic import generate_m1

    df = generate_m1(a.start, a.end, seed=a.seed)
    BarStore(cfg.data_dir).write("SYNTH", df)
    print(f"{len(df):,} bougies SYNTHÉTIQUES écrites sous le symbole SYNTH (aucune valeur de marché)")


def cmd_data_quality(a, cfg: AppConfig) -> None:
    from tradebot.data.quality import check_quality

    df = _load_bars(cfg, a.symbol, _dt(a.start), _dt(a.end))
    rep = check_quality(df, a.symbol or cfg.instrument.symbol)
    out = _out_dir("quality")
    out.mkdir(parents=True, exist_ok=True)
    (out / "quality.md").write_text(rep.to_markdown(), encoding="utf-8")
    print(rep.to_markdown())


def cmd_data_fred(a, cfg: AppConfig) -> None:
    import pandas as pd

    from tradebot.fundamental.intermarket import SERIES, fetch_fred

    frame = pd.concat({k: fetch_fred(v) for k, v in SERIES.items()}, axis=1)
    path = Path(cfg.data_dir) / "intermarket" / "fred_daily.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path)
    print(f"{len(frame)} jours écrits dans {path}\n{frame.tail()}")


def cmd_calendar_build(a, cfg: AppConfig) -> None:
    from tradebot.fundamental.calendar import save_events_csv
    from tradebot.fundamental.schedule import historical_events

    path = Path(cfg.data_dir) / "calendar" / "history.csv"
    save_events_csv(historical_events(a.start_year, a.end_year), path)
    print(f"Écrit : {path}\n⚠️ Contient FOMC + NFP APPROXIMATIF. Ajoute CPI/PCE depuis bls.gov / bea.gov "
          "et vérifie les dates FOMC sur federalreserve.gov.")


def cmd_calendar_week(a, cfg: AppConfig) -> None:
    from tradebot.fundamental.forexfactory import fetch_ff_week

    for e in fetch_ff_week():
        if e.currency in cfg.news.currencies and e.impact == "high":
            print(f"{e.ts:%a %d %H:%M} UTC  {e.title}")


def cmd_backtest(a, cfg: AppConfig) -> None:
    from tradebot.backtest.report import write_report
    from tradebot.backtest.runner import run_backtest

    start, end = _dt(a.start), _dt(a.end)
    bars = _load_bars(cfg, a.symbol, start, end)
    res = run_backtest(cfg, bars, strategy=a.strategy, params=_params(a.param), events=_events(cfg, start, end))
    md = write_report(res, _out_dir(f"backtest-{res.strategy}"))
    print(md.read_text(encoding="utf-8"))
    print(f"\nRapport : {md}")


def cmd_walkforward(a, cfg: AppConfig) -> None:
    from tradebot.optimization.montecarlo import monte_carlo
    from tradebot.optimization.walkforward import walk_forward

    start, end = _dt(a.start), _dt(a.end)
    bars = _load_bars(cfg, a.symbol, start, end)
    grid = yaml.safe_load(Path(a.grid).read_text(encoding="utf-8"))[a.strategy]
    res = walk_forward(cfg, bars, a.strategy, grid, train_months=a.train, test_months=a.test,
                       events=_events(cfg, start, end), workers=a.workers)
    out = _out_dir(f"walkforward-{a.strategy}")
    out.mkdir(parents=True, exist_ok=True)
    text = res.to_markdown()
    rs = [t.r_multiple for t in res.oos_trades]
    if len(rs) >= 2:
        text += "\n\n" + monte_carlo(rs, risk_pct=cfg.risk.risk_per_trade_pct,
                                     dd_limit_pct=cfg.risk.max_drawdown_pct).to_markdown()
    (out / "walkforward.md").write_text(text, encoding="utf-8")
    print(text + f"\n\nRapport : {out / 'walkforward.md'}")


def cmd_sensitivity(a, cfg: AppConfig) -> None:
    from tradebot.optimization.sensitivity import sensitivity

    start, end = _dt(a.start), _dt(a.end)
    bars = _load_bars(cfg, a.symbol, start, end)
    grid = yaml.safe_load(Path(a.grid).read_text(encoding="utf-8"))[a.strategy]
    res = sensitivity(cfg, bars, a.strategy, grid, events=_events(cfg, start, end))
    out = _out_dir(f"sensitivity-{a.strategy}")
    out.mkdir(parents=True, exist_ok=True)
    (out / "sensitivity.md").write_text(res.to_markdown(), encoding="utf-8")
    print(res.to_markdown())


def cmd_montecarlo(a, cfg: AppConfig) -> None:
    import pandas as pd

    from tradebot.optimization.montecarlo import monte_carlo

    rs = pd.read_csv(a.trades)["r"].to_numpy()
    print(monte_carlo(rs, risk_pct=a.risk or cfg.risk.risk_per_trade_pct,
                      dd_limit_pct=cfg.risk.max_drawdown_pct).to_markdown())


def cmd_live(a, cfg: AppConfig, secrets: Secrets) -> None:
    from tradebot.live.factory import build_runner_with_retry

    build_runner_with_retry(cfg, secrets).run_forever()


def cmd_bridge(a, cfg: AppConfig, secrets: Secrets) -> None:
    from tradebot.backtest.runner import instrument_from
    from tradebot.bridge.server import BridgeGuard, BridgeServer
    from tradebot.bridge.transport import RedisTransport
    from tradebot.execution.mt5_adapter import MT5Adapter
    from tradebot.live.factory import assert_real_money_allowed

    if not secrets.redis_url:
        raise SystemExit("REDIS_URL manquant")
    adapter = MT5Adapter(cfg.instrument.broker_symbol, cfg.live.magic_number, instrument_from(cfg))
    adapter.connect(secrets.mt5_login, secrets.mt5_password, secrets.mt5_server, secrets.mt5_path)
    assert_real_money_allowed(cfg, adapter.is_demo())
    guard = BridgeGuard(max_volume=cfg.risk.max_volume_lots, max_positions=cfg.risk.max_open_positions,
                        core_timeout_s=cfg.live.heartbeat_timeout_seconds * 1.5)
    BridgeServer(adapter, RedisTransport(secrets.redis_url), guard).run_forever()


def cmd_bot(a, cfg: AppConfig, secrets: Secrets) -> None:
    from tradebot.alerts.telegram import CommandHandler, TelegramClient, run_bot
    from tradebot.journal.store import SqlJournal
    from tradebot.live.factory import state_stores

    if not secrets.telegram_bot_token or not secrets.telegram_allowed_chat_ids:
        raise SystemExit("TELEGRAM_BOT_TOKEN et TELEGRAM_ALLOWED_CHAT_IDS requis")
    journal = SqlJournal(secrets.database_url or cfg.journal_url)
    control, status = state_stores(cfg, secrets)
    dashboard = os.environ.get("DASHBOARD_URL", "").strip() or None
    if dashboard and not dashboard.startswith("http"):
        dashboard = f"https://{dashboard}"  # Railway fournit le domaine sans schéma
    handler = CommandHandler(control, status, secrets.telegram_allowed_chat_ids, secrets.totp_secret,
                             trades_provider=journal.recent_trades, dashboard_url=dashboard)
    run_bot(TelegramClient(secrets.telegram_bot_token), handler)


def cmd_telegram_whoami(a, cfg: AppConfig, secrets: Secrets) -> None:
    """Aide à la configuration : affiche le chat_id des derniers messages reçus par le bot."""
    from tradebot.alerts.telegram import TelegramClient

    if not secrets.telegram_bot_token:
        raise SystemExit("TELEGRAM_BOT_TOKEN manquant")
    for u in TelegramClient(secrets.telegram_bot_token).get_updates(None, timeout=1):
        m = u.get("message") or {}
        print(m.get("chat", {}).get("id"), m.get("from", {}).get("username"), m.get("text"))


def cmd_api(a, cfg: AppConfig, secrets: Secrets) -> None:
    import uvicorn

    from tradebot.api.app import create_app

    # Railway (et la plupart des PaaS) imposent le port via la variable PORT
    port = a.port if a.port is not None else int(os.environ.get("PORT", "8000"))
    uvicorn.run(create_app(cfg, secrets), host=a.host, port=port, proxy_headers=True,
                forwarded_allow_ips="*")


def cmd_totp_setup(a, cfg: AppConfig) -> None:
    import pyotp

    secret = pyotp.random_base32()
    uri = pyotp.TOTP(secret).provisioning_uri(name="tradebot", issuer_name="tradebot")
    print(f"TOTP_SECRET={secret}\n\nÀ ajouter dans .env (JAMAIS dans git).\n"
          f"Dans ton appli d'authentification : « saisir une clé » -> {secret}\n(ou URI : {uri})")


def cmd_config_check(a, cfg: AppConfig) -> None:
    print(json.dumps(cfg.model_dump(mode="json"), indent=2, ensure_ascii=False))
    s = Secrets.from_env()
    present = {k: bool(v) for k, v in s.model_dump().items()}
    print("\nSecrets présents :", json.dumps(present, indent=2))


# ---------------------------------------------------------------- parseur
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tradebot", description="Système de trading XAU/USD")
    p.add_argument("--config", default=None, help="fichier YAML (défaut : config/default.yaml)")
    p.add_argument("--log-level", default="INFO")
    p.add_argument("--pretty", action="store_true", help="logs lisibles au lieu de JSON")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name: str, fn, help_: str, needs_secrets: bool = False):
        sp = sub.add_parser(name, help=help_)
        sp.set_defaults(fn=fn, needs_secrets=needs_secrets)
        return sp

    def period(sp, symbol=True):
        if symbol:
            sp.add_argument("--symbol", default=None)
        sp.add_argument("--start", default=None, help="AAAA-MM-JJ")
        sp.add_argument("--end", default=None, help="AAAA-MM-JJ (exclu)")

    sp = add("data-download", cmd_data_download, "télécharger l'historique M1 Dukascopy")
    sp.add_argument("--symbol", default="XAUUSD")
    sp.add_argument("--start", type=_date, required=True)
    sp.add_argument("--end", type=_date, required=True)

    sp = add("data-synthetic", cmd_data_synthetic, "générer des données aléatoires (tests)")
    sp.add_argument("--start", default="2023-01-01")
    sp.add_argument("--end", default="2024-01-01")
    sp.add_argument("--seed", type=int, default=42)

    period(add("data-quality", cmd_data_quality, "rapport qualité des données"))
    add("data-fred", cmd_data_fred, "télécharger taux réels / dollar / VIX (FRED)")

    sp = add("calendar-build", cmd_calendar_build, "construire le calendrier historique (backtest)")
    sp.add_argument("--start-year", type=int, default=2015)
    sp.add_argument("--end-year", type=int, default=datetime.now(UTC).year)
    add("calendar-week", cmd_calendar_week, "annonces USD à fort impact de la semaine")

    sp = add("backtest", cmd_backtest, "backtest d'une stratégie")
    sp.add_argument("--strategy", default=None)
    sp.add_argument("--param", action="append", help="clé=valeur (répétable)")
    period(sp)

    for name, fn in (("walkforward", cmd_walkforward), ("sensitivity", cmd_sensitivity)):
        sp = add(name, fn, f"{name} (grille dans config/grids.yaml)")
        sp.add_argument("--strategy", required=True)
        sp.add_argument("--grid", default="config/grids.yaml")
        period(sp)
        if name == "walkforward":
            sp.add_argument("--train", type=int, default=12, help="mois d'entraînement")
            sp.add_argument("--test", type=int, default=3, help="mois de test")
            sp.add_argument("--workers", type=int, default=1)

    sp = add("montecarlo", cmd_montecarlo, "Monte Carlo sur un trades.csv")
    sp.add_argument("trades")
    sp.add_argument("--risk", type=float, default=None)

    add("live", cmd_live, "moteur live (mode défini dans la config)", True)
    add("bridge", cmd_bridge, "pont MT5 (VPS Windows)", True)
    add("bot", cmd_bot, "bot Telegram de commande", True)
    add("telegram-whoami", cmd_telegram_whoami, "afficher ton chat_id Telegram", True)
    sp = add("api", cmd_api, "API + dashboard PWA", True)
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=None, help="défaut : $PORT sinon 8000")
    add("totp-setup", cmd_totp_setup, "générer un secret TOTP")
    add("config-check", cmd_config_check, "afficher la config effective et les secrets présents")
    return p


def main(argv: list[str] | None = None) -> None:
    for stream in (sys.stdout, sys.stderr):  # accents lisibles même dans une console Windows
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    load_dotenv()
    args = build_parser().parse_args(argv)
    configure_logging(args.log_level, json=not args.pretty)
    cfg = load_config(args.config)
    if args.needs_secrets:
        args.fn(args, cfg, Secrets.from_env())
    else:
        args.fn(args, cfg)


if __name__ == "__main__":
    sys.exit(main())
