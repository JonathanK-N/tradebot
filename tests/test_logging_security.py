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


def test_telegram_errors_are_redacted():
    """Les erreurs HTTP contiennent l'URL : le jeton doit y être masqué."""
    import pytest

    from tradebot.alerts.telegram import TelegramError

    for status in (409, 500):
        transport = httpx.MockTransport(lambda req, s=status: httpx.Response(s, json={"ok": False}))
        client = TelegramClient(TOKEN, http=httpx.Client(transport=transport))
        with pytest.raises(TelegramError) as exc:
            client.get_updates(None, timeout=0)
        assert TOKEN not in str(exc.value) and "***" in str(exc.value)
        assert exc.value.status_code == status

    def boom(req):
        raise httpx.ConnectError(f"connexion impossible vers {req.url}")

    client = TelegramClient(TOKEN, http=httpx.Client(transport=httpx.MockTransport(boom)))
    with pytest.raises(TelegramError) as exc:
        client.send_message(1, "x")
    assert TOKEN not in str(exc.value)
