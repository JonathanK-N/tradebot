import pyotp
import pytest
from fastapi.testclient import TestClient

from tradebot.api.app import create_app
from tradebot.core.config import AppConfig, LiveConfig, Secrets
from tradebot.journal.store import SqlJournal
from tradebot.risk.killswitch import ControlFile

TOKEN = "t" * 32


@pytest.fixture
def client(tmp_path):
    secret = pyotp.random_base32()
    cfg = AppConfig(live=LiveConfig(state_dir=str(tmp_path)))
    app = create_app(cfg, Secrets(api_token=TOKEN, totp_secret=secret),
                     journal=SqlJournal(f"sqlite:///{tmp_path / 'j.db'}"))  # backend fichier par défaut
    return TestClient(app), ControlFile(tmp_path / "control.json"), secret


def h():
    return {"Authorization": f"Bearer {TOKEN}"}


def test_refuses_weak_token():
    with pytest.raises(RuntimeError):
        create_app(AppConfig(), Secrets(api_token="short"))


def test_auth_required(client):
    c, *_ = client
    assert c.get("/api/status").status_code == 401
    assert c.get("/api/status", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert c.get("/api/status", headers=h()).json() == {"available": False}
    assert c.get("/").status_code == 200  # la page est publique, pas les données


def test_commands(client):
    c, control, secret = client
    assert c.post("/api/kill", headers=h()).json()["ok"]
    assert control.read().flatten_requested
    assert c.post("/api/resume", headers=h(), json={"code": "000000"}).status_code == 403
    assert control.read().paused
    assert c.post("/api/resume", headers=h(), json={"code": pyotp.TOTP(secret).now()}).json()["ok"]
    assert not control.read().paused
    c.post("/api/pause", headers=h())
    assert control.read().paused
    assert c.get("/healthz").status_code == 503  # aucun état publié
