#!/usr/bin/env bash
# Mise à jour du cœur depuis git + tests + redémarrage. Depuis le téléphone :
#   ssh tradebot@<ip-tailscale> 'cd ~/tradebot && ./scripts/linux/deploy.sh'
set -euo pipefail
cd "$(dirname "$0")/../.."

git fetch --quiet
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "❌ Modifications locales non commitées : déploiement annulé."; exit 1
fi
git pull --ff-only

# Les tests tournent dans GitHub Actions AVANT la fusion sur main : on ne déploie que main.
echo "==> Construction de l'image"
docker compose build --quiet live
docker run --rm tradebot:latest config-check > /dev/null  # la config est valide (plafonds de risque)

echo "==> Redémarrage"
docker compose up -d --remove-orphans
sleep 10
docker compose ps
curl -fsS http://127.0.0.1:8000/healthz && echo " ✅ API ok" || echo " ⚠️ healthz KO (normal si marché fermé au 1er démarrage)"
