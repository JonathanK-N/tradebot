#!/usr/bin/env bash
# Sauvegarde quotidienne chiffrée (restic -> Backblaze B2 ou tout stockage S3).
# Cron : 15 22 * * *  /home/tradebot/tradebot/scripts/linux/backup.sh >> /home/tradebot/backup.log 2>&1
# Prérequis dans ~/.restic.env (chmod 600) :
#   RESTIC_REPOSITORY=b2:mon-bucket:tradebot   RESTIC_PASSWORD=...
#   B2_ACCOUNT_ID=...   B2_ACCOUNT_KEY=...
# ⚠️ Garde RESTIC_PASSWORD dans ton gestionnaire de mots de passe : sans lui, la sauvegarde est illisible.
set -euo pipefail
cd "$(dirname "$0")/../.."
set -a; source ~/.restic.env; set +a

mkdir -p var/backup
docker compose exec -T postgres pg_dump -U tradebot -Fc tradebot > var/backup/journal.dump
restic snapshots &>/dev/null || restic init
restic backup var/ config/ reports/ --exclude var/backup/*.tmp --tag tradebot
restic forget --keep-daily 14 --keep-weekly 8 --keep-monthly 12 --prune
restic check --read-data-subset=1%
echo "✅ sauvegarde $(date -u +%FT%TZ)"
