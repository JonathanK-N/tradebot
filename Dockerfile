# Image du cœur (VPS Linux ou Railway). Le pont MT5 ne tourne PAS dans Docker (Windows natif).
# Une seule image pour les services api / bot / live : seule la commande de démarrage change.
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY --from=ghcr.io/astral-sh/uv:0.12.17 /uv /usr/local/bin/uv
WORKDIR /app

# Dépendances d'abord (cache Docker)
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --extra postgres --no-install-project

COPY src ./src
COPY config ./config
RUN uv sync --frozen --no-dev --extra postgres

# Utilisateur non-root (moindre privilège)
RUN useradd --create-home --uid 10001 tradebot && mkdir -p /app/var /app/data /app/reports \
    && chown -R tradebot:tradebot /app/var /app/data /app/reports
USER tradebot
ENV PATH="/app/.venv/bin:$PATH" TZ=UTC
ENTRYPOINT ["tradebot"]
CMD ["--help"]
