"""Déploiement Railway : configuration, stockage Redis partagé, démarrage des services.

Complément du test Node ``tests/railway/check.ts`` (qui exécute .railway/railway.ts).
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import fakeredis
import httpx
import pytest
import redis
from fastapi.testclient import TestClient

from tradebot.alerts.telegram import CommandHandler
from tradebot.api.app import AuthLimiter, create_app
from tradebot.cli import build_parser
from tradebot.core.config import AppConfig, LiveConfig, Secrets, load_config, normalize_database_url
from tradebot.core.kv import RedisKV, StateUnavailable
from tradebot.journal.store import SqlJournal
from tradebot.live.factory import RealMoneyLocked, build_runner_with_retry, state_stores
from tradebot.live.status import StatusStore
from tradebot.risk.killswitch import ControlStore

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "x" * 32


# --------------------------------------------------------------- configuration
@pytest.mark.parametrize("url,expected", [
    ("postgresql://u:p@h:5432/railway", "postgresql+psycopg://u:p@h:5432/railway"),
    ("postgres://u:p@h:5432/db", "postgresql+psycopg://u:p@h:5432/db"),
    ("postgresql+psycopg://u:p@h/db", "postgresql+psycopg://u:p@h/db"),
    ("sqlite:///var/j.db", "sqlite:///var/j.db"),
])
def test_database_url_normalized(url, expected):
    assert normalize_database_url(url) == expected
    assert Secrets.from_env({"DATABASE_URL": url}).database_url == expected


def test_railway_url_selects_psycopg_driver():
    from sqlalchemy import create_engine

    engine = create_engine(normalize_database_url("postgresql://u:p@localhost:5432/railway"))
    assert engine.dialect.driver == "psycopg"  # le pilote est bien installé et chargé


def test_railway_yaml_inherits_default():
    cfg = load_config(ROOT / "config" / "railway.yaml")
    default = load_config(ROOT / "config" / "default.yaml")
    assert cfg.live.mode == "remote" and cfg.live.state_backend == "redis"
    assert cfg.live.state_dir.startswith("/app/var")  # sur le volume du service live
    assert cfg.environment != "live"  # le verrou compte réel reste actif
    assert cfg.risk == default.risk  # le risque n'est PAS redéfini en douce
    assert cfg.costs == default.costs and cfg.strategy == default.strategy


def test_explicit_missing_config_is_an_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "absent.yaml")


def test_extends_loop_is_detected(tmp_path):
    (tmp_path / "a.yaml").write_text("extends: b.yaml\n", encoding="utf-8")
    (tmp_path / "b.yaml").write_text("extends: a.yaml\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(tmp_path / "a.yaml")


def test_start_commands_are_valid_cli_commands():
    """Chaque commande de démarrage déclarée dans .railway/railway.ts doit être acceptée par la CLI."""
    ts = (ROOT / ".railway" / "railway.ts").read_text(encoding="utf-8")
    commands = re.findall(r'startCommand:\s*"([^"]+)"', ts)
    assert sorted(c.split()[1] for c in commands) == ["api", "bot", "live"]
    parser = build_parser()
    for cmd in commands:
        argv = cmd.split()
        assert argv[0] == "tradebot"
        parser.parse_args(argv[1:])  # lève SystemExit si invalide
    assert re.search(r'TRADEBOT_CONFIG:\s*"config/railway.yaml"', ts)


# --------------------------------------------------------------- Dockerfile
def test_dockerfile_is_production_ready():
    d = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"ghcr.io/astral-sh/uv:\d+\.\d+\.\d+ ", d), "version de uv non épinglée"
    assert "--frozen" in d and "--extra postgres" in d and "--no-dev" in d
    assert re.search(r"^USER tradebot$", d, re.M), "l'image doit tourner en non-root"
    assert 'ENTRYPOINT ["tradebot"]' in d
    assert "/app/.venv/bin" in d  # `tradebot` trouvable par la commande de démarrage Railway
    ignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").split()
    for must in (".env", ".git", ".venv", "data/", "var/"):
        assert must in ignore, f"{must} doit être exclu de l'image"
    assert "psycopg" in (ROOT / "uv.lock").read_text(encoding="utf-8")


# --------------------------------------------------------------- Redis partagé
@pytest.fixture
def fake_kv():
    return RedisKV("redis://unused", client=fakeredis.FakeRedis(decode_responses=True))


class DownRedis:
    def get(self, *_):
        raise redis.ConnectionError("Redis injoignable")

    set = get


def test_control_and_status_shared_through_redis(fake_kv):
    # deux « services » distincts, même Redis : le bot écrit, le moteur lit
    bot_side = ControlStore(fake_kv)
    engine_side = ControlStore(RedisKV("redis://unused", client=fake_kv.r))
    bot_side.kill("test", "telegram")
    flags = engine_side.read()
    assert flags.paused and flags.flatten_requested
    engine_side.ack_flatten()
    assert not bot_side.read().flatten_requested and bot_side.read().paused

    StatusStore(fake_kv).write({"mode": "remote"})
    st = StatusStore(fake_kv).read()
    assert st["available"] and st["mode"] == "remote" and st["age_s"] < 5


def test_redis_down_is_fail_closed():
    kv = RedisKV("redis://unused", client=DownRedis())
    flags = ControlStore(kv).read()
    assert flags.paused and "injoignable" in flags.reason  # le moteur n'ouvre plus rien
    assert StatusStore(kv).read()["available"] is False
    with pytest.raises(StateUnavailable):
        ControlStore(kv).kill("x", "y")


def test_telegram_reports_storage_failure():
    kv = RedisKV("redis://unused", client=DownRedis())
    h = CommandHandler(ControlStore(kv), StatusStore(kv), [1], None)
    reply = h.handle(1, "/kill")
    assert "NON appliquée" in reply and "MT5" in reply


def test_api_returns_503_when_storage_down(tmp_path):
    kv = RedisKV("redis://unused", client=DownRedis())
    app = create_app(AppConfig(), Secrets(api_token=TOKEN),
                     journal=SqlJournal(f"sqlite:///{tmp_path / 'j.db'}"),
                     stores=(ControlStore(kv), StatusStore(kv)))
    c = TestClient(app)
    r = c.post("/api/kill", headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 503 and "MT5" in r.json()["detail"]
    assert c.get("/livez").status_code == 200  # le processus reste sain


def test_state_stores_selection(tmp_path):
    cfg = AppConfig(live=LiveConfig(state_backend="redis"))
    with pytest.raises(RuntimeError):
        state_stores(cfg, Secrets())
    control, _ = state_stores(cfg, Secrets(redis_url="redis://localhost:6399/0"))
    assert isinstance(control.kv, RedisKV)
    file_cfg = AppConfig(live=LiveConfig(state_dir=str(tmp_path)))
    control, _status = state_stores(file_cfg, Secrets())
    control.pause("t", "t")
    assert (tmp_path / "control.json").exists()


# --------------------------------------------------------------- démarrage du moteur
def test_live_waits_for_bridge_instead_of_crashing():
    calls, sleeps = [], []

    def builder(cfg, secrets):
        calls.append(1)
        if len(calls) < 4:
            raise ConnectionError("historique non publié : le pont MT5 tourne-t-il ?")
        return "runner"

    out = build_runner_with_retry(AppConfig(), Secrets(), sleep=sleeps.append, builder=builder)
    assert out == "runner" and len(calls) == 4 and sleeps == [30.0, 30.0, 30.0]


def test_live_config_errors_are_fatal():
    def locked(cfg, secrets):
        raise RealMoneyLocked("compte réel")

    def missing(cfg, secrets):
        raise RuntimeError("REDIS_URL requis en mode remote")

    for builder in (locked, missing):
        with pytest.raises((RealMoneyLocked, RuntimeError)):
            build_runner_with_retry(AppConfig(), Secrets(), sleep=lambda s: None, builder=builder)


def test_live_gives_up_after_max_attempts():
    def down(cfg, secrets):
        raise ConnectionError("down")

    with pytest.raises(ConnectionError):
        build_runner_with_retry(AppConfig(), Secrets(), sleep=lambda s: None, builder=down, max_attempts=3)


# --------------------------------------------------------------- sécurité de l'API publique
def test_auth_limiter_blocks_brute_force(tmp_path):
    now = [0.0]
    limiter = AuthLimiter(max_failures=3, window_s=60, clock=lambda: now[0])
    app = create_app(AppConfig(live=LiveConfig(state_dir=str(tmp_path))), Secrets(api_token=TOKEN),
                     journal=SqlJournal(f"sqlite:///{tmp_path / 'j.db'}"), limiter=limiter)
    c = TestClient(app)
    bad = {"Authorization": "Bearer mauvais"}
    assert [c.get("/api/status", headers=bad).status_code for _ in range(3)] == [401] * 3
    good = {"Authorization": f"Bearer {TOKEN}"}
    assert c.get("/api/status", headers=good).status_code == 429  # même le bon jeton : IP bloquée
    now[0] = 61.0
    assert c.get("/api/status", headers=good).status_code == 200  # débloqué après la fenêtre


def test_security_headers(tmp_path):
    app = create_app(AppConfig(live=LiveConfig(state_dir=str(tmp_path))), Secrets(api_token=TOKEN),
                     journal=SqlJournal(f"sqlite:///{tmp_path / 'j.db'}"))
    r = TestClient(app).get("/")
    assert r.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
    assert r.headers["Strict-Transport-Security"].startswith("max-age=")


# --------------------------------------------------------------- vrai processus, comme sur Railway
def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_api_process_listens_on_railway_port(tmp_path):
    """Lance `tradebot api` exactement comme Railway (port imposé par $PORT, sans --port)."""
    port = _free_port()
    env = {**os.environ, "PORT": str(port), "API_TOKEN": TOKEN,
           "DATABASE_URL": f"sqlite:///{(tmp_path / 'j.db').as_posix()}", "TRADEBOT_LOG_FILE": ""}
    env.pop("TRADEBOT_CONFIG", None)
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text(f"extends: {(ROOT / 'config' / 'default.yaml').as_posix()}\n"
                   f"live:\n  state_dir: {(tmp_path / 'state').as_posix()}\n", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "tradebot.cli", "--config", str(cfg), "api", "--host", "127.0.0.1"],
        env=env, cwd=tmp_path, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    try:
        base = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 30
        while True:
            try:
                r = httpx.get(f"{base}/livez", timeout=2)
                break
            except httpx.HTTPError:
                if proc.poll() is not None or time.monotonic() > deadline:
                    out = proc.stdout.read().decode(errors="replace") if proc.stdout else ""
                    pytest.fail(f"l'API n'a pas démarré sur $PORT={port}\n{out}")
                time.sleep(0.3)
        assert r.status_code == 200 and r.json() == {"ok": True}
        assert httpx.get(f"{base}/api/status").status_code == 401
        ok = httpx.get(f"{base}/api/status", headers={"Authorization": f"Bearer {TOKEN}"})
        assert ok.status_code == 200 and ok.json()["available"] is False  # aucun moteur publié
        assert httpx.get(f"{base}/healthz").status_code == 503
        assert "Tradebot" in httpx.get(base).text
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_api_refuses_to_start_without_token(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "API_TOKEN"}
    env.update(PORT=str(_free_port()), DATABASE_URL=f"sqlite:///{(tmp_path / 'j.db').as_posix()}")
    r = subprocess.run([sys.executable, "-m", "tradebot.cli", "api"], env=env, cwd=ROOT,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode != 0 and "API_TOKEN" in (r.stdout + r.stderr)


def test_waiting_state_is_published(fake_kv):
    status = StatusStore(fake_kv)
    attempts = []

    def builder(cfg, secrets):
        attempts.append(1)
        if len(attempts) < 3:
            raise ConnectionError("historique non publié : le pont MT5 tourne-t-il ?")
        return "runner"

    seen = []
    build_runner_with_retry(AppConfig(), Secrets(), sleep=lambda s: seen.append(status.read()),
                            builder=builder, status=status)
    assert seen[0]["state"] == "waiting_for_bridge" and seen[-1]["waiting"]["attempt"] == 2
    h = CommandHandler(ControlStore(fake_kv), status, [1], None)
    assert "EN ATTENTE du pont MT5" in h.handle(1, "/status")
