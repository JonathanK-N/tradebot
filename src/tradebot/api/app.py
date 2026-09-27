"""API du dashboard mobile (FastAPI) + service de la PWA.

Exposition : UNIQUEMENT sur le réseau Tailscale via ``tailscale serve`` (HTTPS
automatique en *.ts.net, nécessaire pour installer la PWA). Aucun port public.
Authentification : jeton Bearer (défense en profondeur, en plus de Tailscale).
Commandes qui augmentent le risque : code TOTP obligatoire.
"""

from __future__ import annotations

import hmac
from pathlib import Path
from typing import Any

import pyotp
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from tradebot.core.config import AppConfig, Secrets
from tradebot.journal.store import SqlJournal
from tradebot.live.status import StatusFile
from tradebot.risk.killswitch import ControlFile

STATIC = Path(__file__).parent / "static"


class TotpBody(BaseModel):
    code: str


def create_app(cfg: AppConfig, secrets: Secrets, journal: SqlJournal | None = None) -> FastAPI:
    if not secrets.api_token or len(secrets.api_token) < 24:
        raise RuntimeError("API_TOKEN absent ou trop court (>= 24 caractères) : refus de démarrer")
    state = Path(cfg.live.state_dir)
    status = StatusFile(state / "status.json")
    control = ControlFile(state / "control.json")
    journal = journal or SqlJournal(secrets.database_url or cfg.journal_url)
    totp = pyotp.TOTP(secrets.totp_secret) if secrets.totp_secret else None
    app = FastAPI(title="tradebot", docs_url=None, redoc_url=None, openapi_url=None)

    def auth(authorization: str = Header(default="")) -> None:
        token = authorization.removeprefix("Bearer ").strip()
        if not hmac.compare_digest(token.encode(), secrets.api_token.encode()):  # type: ignore[union-attr]
            raise HTTPException(401, "jeton invalide")

    def check_totp(code: str) -> None:
        if not totp or not totp.verify(code, valid_window=1):
            raise HTTPException(403, "code TOTP invalide")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})

    @app.get("/manifest.webmanifest")
    def manifest() -> FileResponse:
        return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")

    @app.get("/sw.js")
    def sw() -> FileResponse:
        return FileResponse(STATIC / "sw.js", media_type="application/javascript")

    @app.get("/icon.svg")
    def icon() -> FileResponse:
        return FileResponse(STATIC / "icon.svg", media_type="image/svg+xml")

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        st = status.read()
        ok = st.get("available") and st.get("age_s", 1e9) < 120
        return JSONResponse({"ok": bool(ok)}, status_code=200 if ok else 503)  # type: ignore[return-value]

    @app.get("/api/status", dependencies=[Depends(auth)])
    def get_status() -> dict[str, Any]:
        return status.read()

    @app.get("/api/trades", dependencies=[Depends(auth)])
    def get_trades(limit: int = 50) -> list[dict[str, Any]]:
        return journal.recent_trades(min(limit, 500))

    @app.get("/api/equity", dependencies=[Depends(auth)])
    def get_equity(limit: int = 1440) -> list[dict[str, Any]]:
        return journal.equity_curve(min(limit, 20_000))

    @app.get("/api/events", dependencies=[Depends(auth)])
    def get_events(limit: int = 50, kind: str | None = None) -> list[dict[str, Any]]:
        return journal.recent_events(min(limit, 500), kind)

    @app.post("/api/pause", dependencies=[Depends(auth)])
    def pause() -> dict[str, Any]:
        control.pause("pause via dashboard", "dashboard")
        return {"ok": True}

    @app.post("/api/kill", dependencies=[Depends(auth)])
    def kill() -> dict[str, Any]:
        control.kill("kill via dashboard", "dashboard")
        return {"ok": True}

    @app.post("/api/resume", dependencies=[Depends(auth)])
    def resume(body: TotpBody) -> dict[str, Any]:
        check_totp(body.code)
        control.resume("dashboard")
        return {"ok": True}

    @app.post("/api/reset_halt", dependencies=[Depends(auth)])
    def reset_halt(body: TotpBody) -> dict[str, Any]:
        check_totp(body.code)
        control.request_reset_halt("dashboard")
        return {"ok": True}

    return app
