# Déploiement sur Railway

Légende : ✅ faisable au téléphone · ⚠️ faisable mais pénible · ❌ nécessite un ordinateur

## Ce qui tourne où

```
RAILWAY (projet « tradebot »)                                 VPS WINDOWS (inchangé)
 ├─ live   moteur + risque  ── volume /app/var (état du risque)   MT5 + pont (tradebot bridge)
 ├─ bot    Telegram                                                    │
 ├─ api    dashboard PWA  ── domaine HTTPS public                      │
 ├─ postgres  journal                                                  │
 └─ redis     contrôle, statut, file d'ordres ◄── accès public TCP ────┘
```

**Railway ne peut pas faire tourner MetaTrader 5** (Windows uniquement). Le VPS Windows reste
nécessaire dès la démo (semaine 9). Avant cela, Railway héberge le dashboard, le bot et la base de
données. Le service `live` **attend** le pont sans planter.

Fichiers :

| Fichier | Rôle |
|---|---|
| `.railway/railway.ts` | Infrastructure : 3 services, PostgreSQL, Redis, volume, variables |
| `config/railway.yaml` | Config applicative : hérite de `default.yaml`, change seulement le déploiement |
| `Dockerfile` / `.dockerignore` | Image unique, utilisateur non-root, dépendances figées |
| `.github/workflows/railway-config.yml` | Plan / apply de l'infrastructure via pull request (depuis le téléphone) |

## Différences avec un VPS, et leurs risques (à connaître)

| Sujet | VPS + Tailscale | Railway | Mitigation dans le code |
|---|---|---|---|
| Dashboard | privé (tailnet) | **public sur Internet** | jeton ≥ 24 caractères, blocage après 10 échecs par IP, TOTP pour reprendre, en-têtes de sécurité |
| Redis pour le pont Windows | privé | **proxy TCP public, non chiffré** | mot de passe long généré par Railway ; le pont applique ses propres limites (SL obligatoire, volume max, cœur vivant) |
| Disque partagé | oui | **non** (1 volume = 1 service) | contrôle et statut dans Redis, état du risque sur le volume de `live` |
| Redémarrages | illimités | 10 en cas d'échec | `live` attend le pont au lieu de planter |

Si le proxy Redis public te gêne, garde Redis sur un VPS derrière Tailscale : c'est l'option la plus
sûre. Railway reste confortable pour tout le reste.

## Mise en place

### 1. Variables partagées (secrets) — ✅ tableau de bord Railway sur mobile
Projet → **Settings → Shared Variables** (environnement `production`) :

| Variable | Comment l'obtenir |
|---|---|
| `API_TOKEN` | `uv run python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `TOTP_SECRET` | `uv run tradebot totp-setup`, puis l'ajouter aussi dans ton appli d'authentification |
| `TELEGRAM_BOT_TOKEN` | @BotFather |
| `TELEGRAM_ALLOWED_CHAT_IDS` | ton chat_id (commande `telegram-whoami` une fois le bot déployé) |
| `HEALTHCHECK_URL` | healthchecks.io (facultatif mais recommandé) |

Aucun secret n'est écrit dans le dépôt. Un test vérifie que `.railway/railway.ts` ne contient aucune
valeur en clair et que chaque service ne reçoit que les secrets dont il a besoin.

### 2. Créer l'infrastructure — ❌ une fois depuis un ordinateur, puis ✅ via GitHub
```bash
npm install -g @railway/cli     # CLI Railway >= 5.42.1
railway login
railway link                    # choisir / créer le projet « tradebot »
npm ci
railway config plan             # aperçu, ne change rien
railway config apply
```
Ensuite, dans Railway (✅ mobile) :
- Service **api** → *Settings → Networking → Generate Domain* : c'est l'adresse du dashboard.
- Service **redis** → *Settings → Networking → Public Access* : crée `REDIS_PUBLIC_URL` pour le pont.
- Crée un **jeton de projet** (Project Settings → Tokens), puis ajoute-le dans GitHub (*Settings →
  Secrets → Actions*) sous le nom `RAILWAY_TOKEN`. Désormais, toute modification de `.railway/`
  passe par une pull request (plan commenté, puis application à la fusion).

### 3. Brancher le pont Windows (semaine 9) — ⚠️
Dans le `.env` du VPS Windows :
`REDIS_URL=<valeur de REDIS_PUBLIC_URL>`. Puis `Start-ScheduledTask tradebot-bridge`.
Sur Telegram, `/status` doit afficher « Pont MT5 : True ».

### 4. Vérifier — ✅
- `https://<domaine-api>/livez` → `{"ok": true}` (processus vivant)
- Dashboard : colle `API_TOKEN`, puis *Ajouter à l'écran d'accueil*
- Telegram : `/status`
- `https://<domaine-api>/healthz` → 200 quand le moteur publie son état (sinon 503) : à surveiller
  depuis healthchecks.io ou un moniteur HTTP.

## Tests qui valident ce déploiement

| Où | Test | Ce qu'il garantit |
|---|---|---|
| local + CI | `tests/test_railway.py` | URL Postgres Railway convertie, config héritée sans toucher au risque, commandes de démarrage valides, Redis partagé, fail-closed si Redis tombe, API sur `$PORT` (vrai processus), anti force brute |
| local + CI | `tests/railway/check.ts` (`npm run test:railway`) | exécute `.railway/railway.ts` avec le SDK officiel : services, build Dockerfile, healthcheck, volume sur `live` seulement, aucun secret en clair, moindre privilège, graphe valide |
| CI | job `docker` | l'image se construit, tourne en non-root, démarre comme sur Railway (commande qui remplace l'ENTRYPOINT) et répond sur `$PORT` |
| CI | `tests/test_integration_services.py` | journal sur un VRAI PostgreSQL, contrôle et pont sur un VRAI Redis |

## Coûts (estimation, à vérifier dans le tableau de bord Railway)
Offre Hobby (environ 5 $US/mois, crédit d'usage inclus), plus l'usage réel : 3 petits services Python,
PostgreSQL et Redis, soit **environ 10 à 25 $US/mois**. Évite les walk-forward sur Railway : ils sont
gourmands en CPU et facturés à l'usage. Lance-les sur ton PC (`uv run tradebot walkforward ...`).
