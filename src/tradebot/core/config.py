"""Configuration externalisée.

- Paramètres métier : YAML versionné dans git (``config/default.yaml``).
- Secrets : UNIQUEMENT variables d'environnement (ou fichier ``.env`` non versionné).

Les limites de risque sont bornées par des PLAFONDS CODÉS EN DUR (``HARD_LIMITS``) :
une faute de frappe dans le YAML (ex. 50 au lieu de 0.5) fait échouer le démarrage
au lieu de faire exploser le compte.
"""

from __future__ import annotations

import os
from datetime import time
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

# Plafonds absolus. Les modifier exige un commit relu, jamais une variable d'env.
HARD_LIMITS = {
    "risk_per_trade_pct": 2.0,
    "max_daily_loss_pct": 5.0,
    "max_weekly_loss_pct": 10.0,
    "max_drawdown_pct": 25.0,
    "max_open_positions": 5,
    "max_trades_per_day": 20,
    "max_volume_lots": 5.0,
    "max_margin_usage_pct": 50.0,
}


class RiskConfig(BaseModel):
    risk_per_trade_pct: float = Field(0.5, gt=0)
    max_daily_loss_pct: float = Field(2.0, gt=0)
    max_weekly_loss_pct: float = Field(4.0, gt=0)
    max_drawdown_pct: float = Field(10.0, gt=0)
    max_open_positions: int = Field(1, ge=1)
    max_trades_per_day: int = Field(3, ge=1)
    max_volume_lots: float = Field(1.0, gt=0)
    max_margin_usage_pct: float = Field(30.0, gt=0)
    max_spread_abs: float = Field(0.80, gt=0, description="spread max en USD/oz")
    max_spread_median_mult: float = Field(2.5, gt=1)
    min_reward_risk: float = Field(1.0, ge=0)
    min_stop_distance: float = Field(0.50, gt=0, description="USD/oz ; évite les tailles absurdes")
    sizing_slippage_buffer: float = Field(0.10, ge=0, description="ajouté au stop pour le sizing")
    friday_cutoff_ny: time = time(14, 0)
    rollover_before_min: int = 15
    rollover_after_min: int = 30

    @model_validator(mode="after")
    def _check_hard_limits(self) -> RiskConfig:
        for key, cap in HARD_LIMITS.items():
            value = getattr(self, key)
            if value > cap:
                raise ValueError(f"{key}={value} dépasse le plafond codé en dur ({cap})")
        if self.max_daily_loss_pct > self.max_weekly_loss_pct:
            raise ValueError("la limite journalière ne peut pas dépasser la limite hebdomadaire")
        if self.max_weekly_loss_pct > self.max_drawdown_pct:
            raise ValueError("la limite hebdomadaire ne peut pas dépasser le drawdown max")
        return self


class NewsWindow(BaseModel):
    keyword: str
    before_min: int
    after_min: int


class NewsConfig(BaseModel):
    enabled: bool = True
    currencies: list[str] = ["USD"]
    min_impact: str = "high"
    default_before_min: int = 30
    default_after_min: int = 30
    windows: list[NewsWindow] = [
        NewsWindow(keyword="FOMC", before_min=60, after_min=90),
        NewsWindow(keyword="Federal Funds Rate", before_min=60, after_min=90),
        NewsWindow(keyword="Non-Farm", before_min=30, after_min=60),
        NewsWindow(keyword="CPI", before_min=30, after_min=45),
    ]
    max_calendar_age_hours: float = 36.0  # au-delà : fail-closed (aucune entrée)


class CostConfig(BaseModel):
    """Modèle de coûts. Volontairement PESSIMISTE tant qu'il n'est pas calibré
    sur les exécutions réelles de TON broker (semaine 9+)."""

    extra_spread: float = Field(0.05, ge=0, description="majoration broker vs données historiques")
    commission_per_lot_side: float = Field(3.0, ge=0)
    entry_slippage: float = Field(0.05, ge=0)
    stop_slippage: float = Field(0.10, ge=0)
    swap_long_per_lot: float = -50.0  # USD par lot et par nuit (négatif = coût)
    swap_short_per_lot: float = -5.0  # prudence : on ne suppose pas de swap positif
    triple_swap_weekday: int = Field(2, ge=0, le=6)  # 2 = mercredi (à vérifier chez le broker)


class InstrumentConfig(BaseModel):
    symbol: str = "XAUUSD"
    broker_symbol: str = "XAUUSD"  # certains brokers : "GOLD", "XAUUSD.r"...
    contract_size: float = 100.0
    tick_size: float = 0.01
    volume_min: float = 0.01
    volume_step: float = 0.01
    volume_max: float = 50.0
    leverage: float = 20.0


class StrategyConfig(BaseModel):
    name: str = "asian_breakout"
    params: dict[str, Any] = {}


class AccountConfig(BaseModel):
    initial_balance: float = Field(10_000.0, gt=0)
    currency: str = "USD"


class LiveConfig(BaseModel):
    mode: str = "paper"  # paper | mt5 | remote
    poll_seconds: float = 5.0
    reconcile_seconds: float = 30.0
    heartbeat_timeout_seconds: float = 60.0
    magic_number: int = 20260926
    state_dir: str = "var/state"
    # file : un seul hôte avec disque partagé ; redis : services séparés (Railway)
    state_backend: str = "file"
    bridge_wait_seconds: float = 30.0  # attente entre deux tentatives si le pont est absent

    @field_validator("mode")
    @classmethod
    def _mode(cls, v: str) -> str:
        if v not in {"paper", "mt5", "remote"}:
            raise ValueError("mode doit être paper, mt5 ou remote")
        return v

    @field_validator("state_backend")
    @classmethod
    def _backend(cls, v: str) -> str:
        if v not in {"file", "redis"}:
            raise ValueError("state_backend doit être file ou redis")
        return v


class AppConfig(BaseModel):
    environment: str = "dev"
    account: AccountConfig = AccountConfig()
    instrument: InstrumentConfig = InstrumentConfig()
    risk: RiskConfig = RiskConfig()
    news: NewsConfig = NewsConfig()
    costs: CostConfig = CostConfig()
    strategy: StrategyConfig = StrategyConfig()
    live: LiveConfig = LiveConfig()
    data_dir: str = "data"
    journal_url: str = "sqlite:///var/journal.db"


class Secrets(BaseModel):
    """Lus depuis l'environnement. Jamais loggés, jamais versionnés."""

    mt5_login: int | None = None
    mt5_password: str | None = None
    mt5_server: str | None = None
    mt5_path: str | None = None
    telegram_bot_token: str | None = None
    telegram_allowed_chat_ids: list[int] = []
    totp_secret: str | None = None
    api_token: str | None = None
    database_url: str | None = None
    redis_url: str | None = None
    healthcheck_url: str | None = None

    def __repr__(self) -> str:  # évite toute fuite accidentelle dans un log/traceback
        return "Secrets(***)"

    __str__ = __repr__

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Secrets:
        e = dict(os.environ if env is None else env)
        ids = [int(x) for x in e.get("TELEGRAM_ALLOWED_CHAT_IDS", "").split(",") if x.strip()]
        db = e.get("DATABASE_URL")
        login = e.get("MT5_LOGIN")
        return cls(
            mt5_login=int(login) if login else None,
            mt5_password=e.get("MT5_PASSWORD"),
            mt5_server=e.get("MT5_SERVER"),
            mt5_path=e.get("MT5_PATH"),
            telegram_bot_token=e.get("TELEGRAM_BOT_TOKEN"),
            telegram_allowed_chat_ids=ids,
            totp_secret=e.get("TOTP_SECRET"),
            api_token=e.get("API_TOKEN"),
            database_url=normalize_database_url(db) if db else None,
            redis_url=e.get("REDIS_URL"),
            healthcheck_url=e.get("HEALTHCHECK_URL"),
        )


def normalize_database_url(url: str) -> str:
    """Railway / Heroku fournissent ``postgres://`` ou ``postgresql://`` ; SQLAlchemy a
    besoin du pilote explicite (psycopg 3) : ``postgresql+psycopg://``."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def load_dotenv(path: str | Path = ".env") -> None:
    """Charge un .env minimal (KEY=VALUE) sans écraser l'environnement existant."""
    p = Path(path)
    if not p.exists():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in override.items():
        out[k] = _deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def _read_yaml(path: Path, depth: int = 0) -> dict[str, Any]:
    """Lit un YAML ; ``extends: autre.yaml`` hérite d'un fichier de base (chemin relatif)."""
    if depth > 5:
        raise ValueError("chaîne « extends » trop longue (boucle ?)")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    parent = data.pop("extends", None)
    if parent:
        data = _deep_merge(_read_yaml(path.parent / parent, depth + 1), data)
    return data


def load_config(path: str | Path | None = None) -> AppConfig:
    path = Path(path or os.environ.get("TRADEBOT_CONFIG", "config/default.yaml"))
    if not path.exists():
        if path.name != "default.yaml":  # un fichier explicitement demandé doit exister
            raise FileNotFoundError(f"fichier de configuration introuvable : {path}")
        return AppConfig()
    return AppConfig.model_validate(_read_yaml(path))
