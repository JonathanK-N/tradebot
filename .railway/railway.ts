// Infrastructure Railway du système de trading (format « Infrastructure as Code »).
//
//   railway login && railway link      # une fois
//   railway config plan                # aperçu, ne change rien
//   railway config apply               # applique après confirmation
//
// Une seule image Docker (Dockerfile à la racine) pour trois services : seule la
// commande de démarrage change. Les SECRETS ne sont jamais écrits ici : ce sont des
// « shared variables » définies dans Railway (voir docs/RAILWAY.md) et seulement
// référencées ci-dessous, chaque service ne recevant que celles dont il a besoin.

import { defineRailway, github, group, postgres, project, redis, service, volume } from "railway/iac";

export const REPO = "JonathanK-N/tradebot";
export const BRANCH = "main";

export default defineRailway((ctx) => {
  const db = postgres("postgres");
  const cache = redis("redis");

  // État du risque (pertes du jour, pic d'equity, HALT) : doit survivre aux redéploiements.
  const state = volume("tradebot-state", { sizeMB: 5120 });

  const source = github(REPO, { branch: BRANCH });

  // Ne redéployer que si le code exécuté change (pas pour la doc ou les tests).
  const build = {
    builder: "DOCKERFILE" as const,
    dockerfilePath: "Dockerfile",
    watchPatterns: ["src/**", "config/**", "Dockerfile", "pyproject.toml", "uv.lock"],
  };

  const base = {
    TRADEBOT_CONFIG: "config/railway.yaml",
    DATABASE_URL: db.env.DATABASE_URL,
    REDIS_URL: cache.env.REDIS_URL,
  };

  // API + dashboard PWA : seul service exposé en HTTP (domaine à générer dans Railway).
  const api = service("api", {
    source,
    build,
    deploy: {
      startCommand: "tradebot api --host 0.0.0.0",
      healthcheckPath: "/livez",
      healthcheckTimeout: 120,
      restartPolicyType: "ON_FAILURE",
      restartPolicyMaxRetries: 10,
    },
    env: {
      ...base,
      API_TOKEN: ctx.shared.API_TOKEN,
      TOTP_SECRET: ctx.shared.TOTP_SECRET,
    },
  });

  // Bot Telegram : commandes /status /pause /kill /resume.
  const bot = service("bot", {
    source,
    build,
    deploy: {
      startCommand: "tradebot bot",
      restartPolicyType: "ON_FAILURE",
      restartPolicyMaxRetries: 10,
    },
    env: {
      ...base,
      TELEGRAM_BOT_TOKEN: ctx.shared.TELEGRAM_BOT_TOKEN,
      TELEGRAM_ALLOWED_CHAT_IDS: ctx.shared.TELEGRAM_ALLOWED_CHAT_IDS,
      TOTP_SECRET: ctx.shared.TOTP_SECRET,
    },
  });

  // Moteur de décision + risque. Patiente tant que le pont MT5 (VPS Windows) est absent.
  const live = service("live", {
    source,
    build,
    deploy: {
      startCommand: "tradebot live",
      restartPolicyType: "ON_FAILURE",
      restartPolicyMaxRetries: 10,
    },
    volumeMounts: { "/app/var": state },
    env: {
      ...base,
      // Les volumes Railway sont montés en root ; l'image tourne en utilisateur non-root.
      RAILWAY_RUN_UID: "0",
      TELEGRAM_BOT_TOKEN: ctx.shared.TELEGRAM_BOT_TOKEN,
      TELEGRAM_ALLOWED_CHAT_IDS: ctx.shared.TELEGRAM_ALLOWED_CHAT_IDS,
      HEALTHCHECK_URL: ctx.shared.HEALTHCHECK_URL,
    },
  });

  return project("tradebot", {
    resources: [group("Trading", [live, bot, api]), group("Données", [db, cache, state])],
  });
});
