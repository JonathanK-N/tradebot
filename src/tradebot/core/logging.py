"""Logs structurés (JSON en production, lisibles en développement).

Chaque log est une ligne JSON : facile à filtrer (``jq``), à expédier vers un
outil d'observabilité, et à lire dans Termius depuis le téléphone.
Les secrets ne doivent JAMAIS être passés en argument de log.
"""

from __future__ import annotations

import logging
import os
import sys

import structlog

_SENSITIVE = ("password", "token", "secret", "api_key", "totp")


def _redact(_: object, __: str, event_dict: dict) -> dict:
    for key in list(event_dict):
        if any(s in key.lower() for s in _SENSITIVE):
            event_dict[key] = "***"
    return event_dict


def configure_logging(level: str = "INFO", json: bool = True) -> None:
    """``TRADEBOT_LOG_FILE`` : écrit aussi dans un fichier (utile pour le pont Windows lancé
    par le planificateur de tâches, qui n'a pas de console)."""
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if path := os.environ.get("TRADEBOT_LOG_FILE"):
        from logging.handlers import RotatingFileHandler

        handlers.append(RotatingFileHandler(path, maxBytes=20_000_000, backupCount=5, encoding="utf-8"))
    logging.basicConfig(format="%(message)s", handlers=handlers, level=level.upper(), force=True)
    # SÉCURITÉ : httpx journalise l'URL complète de chaque requête au niveau INFO, or l'API
    # Telegram place le jeton du bot DANS l'URL (/bot<jeton>/...). Sans cette ligne, le jeton
    # se retrouve en clair dans les logs (incident réel sur Railway).
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    renderer = (
        structlog.processors.JSONRenderer()
        if json
        else structlog.dev.ConsoleRenderer(colors=False)
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _redact,
            structlog.processors.format_exc_info,
            renderer,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[level.upper()]
        ),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
