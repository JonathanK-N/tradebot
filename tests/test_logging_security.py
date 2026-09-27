"""Aucun secret ne doit apparaître dans les logs (incident réel : jeton Telegram dans l'URL)."""

from __future__ import annotations

import logging

import httpx

from tradebot.alerts.telegram import TelegramClient
from tradebot.core.logging import configure_logging

TOKEN = "123456:SECRET-TOKEN-DO-NOT-LOG"


def test_telegram_token_never_logged(caplog):
    configure_logging("INFO")
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"ok": True, "result": []}))
    client = TelegramClient(TOKEN, http=httpx.Client(transport=transport))
    with caplog.at_level(logging.DEBUG, logger="tradebot"):
        client.send_message(1, "test")
        client.get_updates(None, timeout=0)
    everything = "\n".join(r.getMessage() for r in caplog.records)
    assert TOKEN not in everything
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING
