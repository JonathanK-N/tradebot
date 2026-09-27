# Déploiement pas à pas — depuis le téléphone

Légende : ✅ faisable au téléphone · ⚠️ faisable mais pénible · ❌ nécessite un ordinateur

## 0. Comptes et applications (≈ 1 h) — ✅
| Quoi | Pourquoi | App |
|---|---|---|
| GitHub + **2FA** | code, CI | GitHub |
| Tailscale (même compte sur tous les appareils) | réseau privé, aucun port public | Tailscale |
| Termius | SSH vers le VPS Linux | Termius |
| Windows App (Microsoft) | bureau à distance vers le VPS Windows (semaine 9) | Windows App |
| Appli TOTP | codes /resume, 2FA | Ente Auth / 2FAS (iOS), Aegis (Android) |
| Telegram | alertes + commandes | Telegram |
| App MT5 du broker | **kill switch ultime**, indépendant de ce code | MetaTrader 5 |
| healthchecks.io | dead man's switch externe | navigateur |

## 1. Pousser le code sur GitHub — ✅
Depuis le dépôt local :
```bash
git remote add origin git@github.com:<toi>/tradebot.git
git push -u origin main
```
Le dépôt doit être **privé**. Vérifie que l'onglet *Actions* est vert (tests + recherche de secrets).

## 2. VPS Linux (≈ 45 min) — ✅
1. Commande un VPS Ubuntu 24.04 (2 vCPU / 4 Go, ~7–15 $CA/mois) avec **ta clé SSH publique**.
   Termius → *Keychain* → *Generate key* (ed25519) → copie la clé publique chez l'hébergeur.
2. Termius → connexion `root@<ip publique>` puis :
   ```bash
   curl -fsSL https://raw.githubusercontent.com/<toi>/tradebot/main/scripts/linux/bootstrap_vps.sh -o b.sh
   less b.sh                                   # relire avant d'exécuter en root
   bash b.sh "ssh-ed25519 AAAA...ta-clé"
   ```
   Ouvre sur le téléphone le lien Tailscale affiché pour autoriser le serveur.
3. Reconnecte-toi en `tradebot@<ip tailscale>` puis ferme le SSH public : `sudo ufw delete allow 22/tcp`.

## 3. Déployer le cœur (≈ 30 min) — ✅
```bash
git clone git@github.com:<toi>/tradebot.git && cd tradebot   # (clé de déploiement GitHub en lecture seule)
cp .env.example .env && chmod 600 .env
python3 -c "import base64,os; print('TOTP_SECRET=' + base64.b32encode(os.urandom(20)).decode())"
python3 -c "import secrets; print('API_TOKEN=' + secrets.token_urlsafe(32))"
python3 -c "import secrets; print('POSTGRES_PASSWORD=' + secrets.token_urlsafe(24))"
python3 -c "import secrets; print('REDIS_PASSWORD=' + secrets.token_urlsafe(24))"
nano .env    # colle les valeurs ci-dessus + TELEGRAM_*, HEALTHCHECK_URL
# TOTP_SECRET : ajoute-le aussi dans ton appli d'authentification (« saisir une clé »)
echo "TAILSCALE_IP=$(tailscale ip -4 | head -1)" >> .env
docker compose up -d --build postgres redis api bot
sudo tailscale serve --bg --https=443 http://127.0.0.1:8000
```
- **Dashboard** : ouvre `https://<nom-du-vps>.<tailnet>.ts.net` sur le téléphone (Tailscale actif),
  colle l'`API_TOKEN`, puis *Partager → Sur l'écran d'accueil* (iOS) ou *Installer l'application* (Android).
- **Telegram** : crée le bot avec @BotFather, envoie-lui un message, puis
  `docker compose run --rm bot telegram-whoami` pour obtenir ton `chat_id` → `.env` → `docker compose up -d bot`.

## 4. Recherche (semaines 2 à 8) — ✅ lancé et lu au téléphone, calcul sur le VPS
```bash
alias tb='docker compose run --rm live'
tb data-download --start 2015-01-01 --end 2026-09-25      # plusieurs heures la 1re fois (cache disque)
tb data-quality
tb calendar-build --start-year 2015                        # puis compléter CPI/PCE (voir hypothèses)
tb backtest --strategy asian_breakout --start 2015-01-01 --end 2023-01-01
tb walkforward --strategy asian_breakout --workers 2
```
Les rapports Markdown sont dans `reports/` : `cat reports/<dossier>/report.md` dans Termius.
Garde **2023 → aujourd'hui** hors de toute recherche tant que le walk-forward n'est pas validé
(période de contrôle finale, utilisable **une seule fois**).

## 5. VPS Windows + MT5 démo (semaine 9) — ⚠️
1. VPS Windows proche du serveur du broker (~25–50 $CA/mois). Installe Tailscale, git, puis le
   terminal MT5 du broker (Windows App depuis le téléphone : ça marche, sur un petit écran).
2. Connecte le compte **DÉMO** et active *Trading algorithmique*.
3. PowerShell administrateur :
   ```powershell
   git clone https://github.com/<toi>/tradebot.git; cd tradebot
   .\scripts\windows\setup_bridge.ps1
   Start-ScheduledTask -TaskName tradebot-bridge
   ```
4. Sur le VPS Linux : `live.mode: remote` dans `config/default.yaml`, commit, puis `./scripts/linux/deploy.sh`.
   Avant de laisser tourner : `/status` sur Telegram → « Pont MT5 : True ».

Le verrou `RealMoneyLocked` empêche tout ordre sur un compte **réel** tant que la démo n'est pas validée.

## 6. Sauvegardes — ✅
`~/.restic.env` (Backblaze B2), puis `crontab -e` :
```
15 22 * * * /home/tradebot/tradebot/scripts/linux/backup.sh >> /home/tradebot/backup.log 2>&1
```
Teste la **restauration** une fois (`restic restore latest --target /tmp/r`) : une sauvegarde jamais
restaurée n'est pas une sauvegarde.
