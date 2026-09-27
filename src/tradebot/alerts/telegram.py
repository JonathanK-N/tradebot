"""Alertes et commandes Telegram (le canal principal depuis le téléphone).

Sécurité :
- seules les conversations dont le ``chat_id`` est dans la liste blanche sont servies
  (les autres sont ignorées silencieusement et journalisées) ;
- les commandes qui RÉDUISENT le risque (/pause, /kill) sont immédiates : en
  urgence, chaque seconde compte, et un attaquant qui les déclencherait ne ferait
  que fermer des positions ;
- les commandes qui AUGMENTENT le risque (/resume, /reset_halt) exigent un code
  TOTP à 6 chiffres (appli d'authentification), valable 30 s.

Une alerte qui échoue ne doit JAMAIS faire tomber le moteur : les erreurs réseau
sont journalisées puis ignorées.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import httpx
import pyotp

from tradebot.core.logging import get_logger
from tradebot.live.status import StatusFile
from tradebot.risk.killswitch import ControlFile

log = get_logger(__name__)
LEVELS = {"info": 1, "signal": 2, "critical": 3}


class TelegramClient:
    def __init__(self, token: str, http: httpx.Client | None = None) -> None:
        self.base = f"https://api.telegram.org/bot{token}"
        self.http = http or httpx.Client(timeout=40)

    def send_message(self, chat_id: int, text: str) -> None:
        self.http.post(f"{self.base}/sendMessage",
                       json={"chat_id": chat_id, "text": text[:4000], "disable_web_page_preview": True})

    def get_updates(self, offset: int | None, timeout: int = 30) -> list[dict[str, Any]]:
        r = self.http.get(f"{self.base}/getUpdates", params={"offset": offset, "timeout": timeout},
                          timeout=timeout + 10)
        r.raise_for_status()
        return r.json().get("result", [])


class TelegramNotifier:
    def __init__(self, client: TelegramClient, chat_ids: list[int], min_level: str = "info",
                 min_interval_s: float = 1.0) -> None:
        self.client = client
        self.chat_ids = chat_ids
        self.min_level = LEVELS[min_level]
        self.min_interval_s = min_interval_s
        self._last = 0.0

    def send(self, level: str, text: str) -> None:
        if LEVELS.get(level, 3) < self.min_level:
            return
        wait = self.min_interval_s - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)  # respect des limites de débit Telegram
        for cid in self.chat_ids:
            try:
                self.client.send_message(cid, text)
            except Exception as e:
                log.error("telegram_send_failed", error=str(e))
        self._last = time.monotonic()


def _fmt_positions(positions: list[dict]) -> str:
    if not positions:
        return "Aucune position ouverte."
    return "\n".join(
        f"• {p['side'].upper()} {p['volume']} @ {p['entry_price']:.2f} | SL {p['stop_loss']:.2f} "
        f"| TP {p.get('take_profit') or '-'}" for p in positions
    )


class CommandHandler:
    def __init__(self, control: ControlFile, status: StatusFile, allowed_ids: list[int],
                 totp_secret: str | None, *,
                 trades_provider: Callable[[int], list[dict]] | None = None) -> None:
        self.control = control
        self.status = status
        self.allowed = set(allowed_ids)
        self.totp = pyotp.TOTP(totp_secret) if totp_secret else None
        self.trades_provider = trades_provider

    def _check_totp(self, args: list[str]) -> bool:
        return bool(self.totp and args and self.totp.verify(args[0], valid_window=1))

    def handle(self, chat_id: int, text: str) -> str | None:
        if chat_id not in self.allowed:
            log.warning("telegram_unauthorized", chat_id=chat_id)
            return None
        parts = text.strip().split()
        if not parts:
            return None
        cmd, args = parts[0].split("@")[0].lower(), parts[1:]
        who = f"telegram:{chat_id}"

        if cmd in ("/start", "/help"):
            return ("Commandes :\n/status — état du système\n/positions — positions ouvertes\n"
                    "/trades — derniers trades\n/news — annonces à venir\n"
                    "/pause — stoppe les nouvelles entrées\n/kill — ferme TOUT et stoppe\n"
                    "/resume <code TOTP> — reprise\n/reset_halt <code TOTP> — lever le HALT drawdown")
        if cmd == "/pause":
            self.control.pause("pause via Telegram", who)
            return "⏸ Pause activée : plus aucune nouvelle entrée. Positions conservées avec leurs SL/TP."
        if cmd == "/kill":
            self.control.kill("kill via Telegram", who)
            return ("🛑 KILL demandé : fermeture de toutes les positions au prochain cycle (≤ quelques s).\n"
                    "Vérifie avec /positions. En cas de doute : ferme depuis l'app MT5.")
        if cmd == "/resume":
            if not self._check_totp(args):
                return "Code TOTP invalide ou manquant. Usage : /resume 123456"
            self.control.resume(who)
            return "▶️ Reprise autorisée."
        if cmd == "/reset_halt":
            if not self._check_totp(args):
                return "Code TOTP invalide ou manquant. Usage : /reset_halt 123456"
            self.control.request_reset_halt(who)
            return ("Levée du HALT demandée (appliquée au prochain cycle). "
                    "As-tu bien fait la revue écrite de ce drawdown ?")

        st = self.status.read()
        if not st.get("available"):
            return "⚠️ Aucun état publié : le moteur live ne tourne pas ?"
        stale = st["age_s"] > 120
        head = f"⚠️ État vieux de {st['age_s']:.0f} s — moteur bloqué ?\n" if stale else ""
        if cmd == "/status":
            a, r, c = st.get("account", {}), st.get("risk", {}), st.get("control", {})
            return head + (
                f"Mode : {st.get('mode')} | stratégie : {st.get('strategy')}\n"
                f"Equity : {a.get('equity', 0):.2f} | solde : {a.get('balance', 0):.2f}\n"
                f"Positions : {len(st.get('positions', []))} | trades du jour : {r.get('trades_today')}\n"
                f"Pause : {c.get('paused')} | HALT : {r.get('halted')} {r.get('halt_reason', '')}\n"
                f"Limite jour : {r.get('day_locked')} | semaine : {r.get('week_locked')}\n"
                f"Dernière bougie : {st.get('last_bar_ts')}\n"
                f"Calendrier : {st.get('calendar', {}).get('status')}\n"
                f"Pont MT5 : {st.get('bridge_alive', 'n/a')}"
            )
        if cmd == "/positions":
            return head + _fmt_positions(st.get("positions", []))
        if cmd == "/news":
            evs = st.get("calendar", {}).get("upcoming", [])
            return head + ("\n".join(f"• {e['ts']} {e['title']}" for e in evs) or "Aucune annonce (24 h).")
        if cmd == "/trades":
            if not self.trades_provider:
                return "Journal indisponible."
            rows = self.trades_provider(10)
            return "\n".join(
                f"{'✅' if r['pnl'] >= 0 else '❌'} {str(r['exit_ts'])[:16]} {r['side']} {r['pnl']:+.2f} "
                f"({r['r_multiple']:+.2f} R) {r['exit_reason']}" for r in rows) or "Aucun trade."
        return "Commande inconnue. /help"


def run_bot(client: TelegramClient, handler: CommandHandler) -> None:  # pragma: no cover - boucle réseau
    offset = None
    log.info("telegram_bot_started")
    while True:
        try:
            for u in client.get_updates(offset):
                offset = u["update_id"] + 1
                msg = u.get("message") or {}
                chat, text = msg.get("chat", {}).get("id"), msg.get("text")
                if chat is None or not text:
                    continue
                reply = handler.handle(int(chat), text)
                if reply:
                    client.send_message(int(chat), reply)
        except Exception as e:
            log.error("telegram_poll_error", error=str(e))
            time.sleep(5)
