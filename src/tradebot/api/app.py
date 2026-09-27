"""API du dashboard mobile (FastAPI) + service de la PWA.

Deux modes d'exposition :
- VPS : UNIQUEMENT sur le réseau Tailscale via ``tailscale serve`` (aucun port public).
- Railway : domaine public HTTPS. L'API est alors joignable depuis Internet ; la
  protection repose sur le jeton Bearer (>= 24 caractères, comparaison à temps
  constant), la limitation des échecs d'authentification par IP, et le TOTP pour
  toute commande qui augmente le risque.
"""

from __future__ import annotations

import hmac
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

import pyotp
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel

from tradebot.core.config import AppConfig, Secrets
from tradebot.core.kv import StateUnavailable
from tradebot.journal.store import SqlJournal
from tradebot.live.status import StatusStore
from tradebot.risk.killswitch import ControlStore

STATIC = Path(__file__).parent / "static"


class TotpBody(BaseModel):
    code: str


class AuthLimiter:
    """Bloque une IP après ``max_failures`` échecs en ``window_s`` secondes (anti force brute)."""

    def __init__(self, max_failures: int = 10, window_s: float = 900.0, clock=time.monotonic) -> None:
        self.max_failures = max_failures
        self.window_s = window_s
        self.clock = clock
        self._fails: dict[str, deque[float]] = defaultdict(deque)

    def _prune(self, ip: str) -> deque[float]:
        q = self._fails[ip]
        while q and self.clock() - q[0] > self.window_s:
            q.popleft()
        return q

    def blocked(self, ip: str) -> bool:
        return len(self._prune(ip)) >= self.max_failures

    def fail(self, ip: str) -> None:
        self._prune(ip).append(self.clock())


def create_app(cfg: AppConfig, secrets: Secrets, journal: SqlJournal | None = None,
               stores: tuple[ControlStore, StatusStore] | None = None,
               limiter: AuthLimiter | None = None) -> FastAPI:
    if not secrets.api_token or len(secrets.api_token) < 24:
        raise RuntimeError("API_TOKEN absent ou trop court (>= 24 caractères) : refus de démarrer")
    if stores is None:
        from tradebot.live.factory import state_stores

        stores = state_stores(cfg, secrets)
    control, status = stores
    journal = journal or SqlJournal(secrets.database_url or cfg.journal_url)
    totp = pyotp.TOTP(secrets.totp_secret) if secrets.totp_secret else None
    limiter = limiter or AuthLimiter()
    app = FastAPI(title="tradebot", docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def security_headers(request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(StateUnavailable)
    async def state_unavailable(_: Request, exc: StateUnavailable) -> JSONResponse:
        return JSONResponse({"detail": f"stockage de contrôle injoignable : {exc}. "
                                       "Utilise l'app MT5 si c'est urgent."}, status_code=503)

    def auth(request: Request, authorization: str = Header(default="")) -> None:
        ip = request.client.host if request.client else "?"
        if limiter.blocked(ip):
            raise HTTPException(429, "trop d'échecs d'authentification, réessaie plus tard")
        token = authorization.removeprefix("Bearer ").strip()
        if not hmac.compare_digest(token.encode(), secrets.api_token.encode()):  # type: ignore[union-attr]
            limiter.fail(ip)
            raise HTTPException(401, "jeton invalide")

    def check_totp(request: Request, code: str) -> None:
        if not totp or not totp.verify(code, valid_window=1):
            limiter.fail(request.client.host if request.client else "?")
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

    @app.get("/livez")
    def livez() -> dict[str, Any]:
        """Le PROCESSUS répond (healthcheck de déploiement Railway). Ne dit rien du moteur."""
        return {"ok": True}

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        """Le MOTEUR live publie un état récent (surveillance externe, ex. healthchecks.io)."""
        st = status.read()
        ok = bool(st.get("available") and st.get("age_s", 1e9) < 120)
        return JSONResponse({"ok": ok}, status_code=200 if ok else 503)

    @app.get("/api/status", dependencies=[Depends(auth)])
    def get_status() -> dict[str, Any]:
        return status.read()

    @app.get("/api/trades", dependencies=[Depends(auth)])
    def get_trades(limit: int = 50) -> list[dict[str, Any]]:
        return journal.recent_trades(max(1, min(limit, 500)))

    @app.get("/api/equity", dependencies=[Depends(auth)])
    def get_equity(limit: int = 1440) -> list[dict[str, Any]]:
        return journal.equity_curve(max(1, min(limit, 20_000)))

    @app.get("/api/events", dependencies=[Depends(auth)])
    def get_events(limit: int = 50, kind: str | None = None) -> list[dict[str, Any]]:
        return journal.recent_events(max(1, min(limit, 500)), kind)

    @app.post("/api/pause", dependencies=[Depends(auth)])
    def pause() -> dict[str, Any]:
        control.pause("pause via dashboard", "dashboard")
        return {"ok": True}

    @app.post("/api/kill", dependencies=[Depends(auth)])
    def kill() -> dict[str, Any]:
        control.kill("kill via dashboard", "dashboard")
        return {"ok": True}

    @app.post("/api/resume", dependencies=[Depends(auth)])
    def resume(request: Request, body: TotpBody) -> dict[str, Any]:
        check_totp(request, body.code)
        control.resume("dashboard")
        return {"ok": True}

    @app.post("/api/reset_halt", dependencies=[Depends(auth)])
    def reset_halt(request: Request, body: TotpBody) -> dict[str, Any]:
        check_totp(request, body.code)
        control.request_reset_halt("dashboard")
        return {"ok": True}

    return app
