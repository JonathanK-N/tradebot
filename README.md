# tradebot — trading automatisé XAU/USD

Système de trading pensé comme un logiciel : le risque passe avant la performance, chaque décision
est tracée, et tout se pilote depuis un téléphone.

> ⚠️ Aucun résultat de ce dépôt n'est une promesse de gain. Les deux stratégies fournies (H1, H2) sont
> des **hypothèses à tester**, pas des stratégies validées. Ordre imposé : backtest → walk-forward
> → démo (4 à 8 semaines minimum) → micro-lot → montée progressive.

## Architecture
```
VPS LINUX (Docker)                          VPS WINDOWS
 collecte → stratégie → RISQUE (veto) ──Redis/Tailscale──► pont MT5 (2e veto) → broker
     journal · alertes Telegram · API/PWA                    SL/TP toujours posés chez le broker
TÉLÉPHONE : PWA · bot Telegram (/pause /kill /status) · app MT5 (kill switch ultime)
```
Le **même** `TradingEngine` tourne en backtest, en paper et en réel : seuls le broker et le flux de
données changent.

| Module | Code |
|---|---|
| 1 Collecte | `data/` (Dukascopy, Parquet, qualité, synthétique) · `fundamental/intermarket.py` (FRED) |
| 2 Technique | `analysis/technical.py` (indicateurs en streaming, agrégation sans look-ahead) |
| 3 Fondamental | `fundamental/` (calendrier fail-closed, ForexFactory, FOMC/NFP historiques) |
| 4 Risque | `risk/` (sizing, limites jour/semaine/drawdown, HALT, kill switch, plafonds codés en dur) |
| 5 Décision | `strategy/` + `core/engine.py` |
| 6 Exécution | `execution/` (MT5, simulateur) · `bridge/` (pont Redis + garde-fous locaux) |
| 7 Journal | `journal/store.py` (SQLite/PostgreSQL, append-only) |
| 8 Dashboard | `api/` (FastAPI + PWA) |
| 9 Alertes | `alerts/` (Telegram, healthchecks.io) |
| 10 Backtest | `backtest/` (coûts, swaps, gaps, rapports) |
| 11 Optimisation | `optimization/` (walk-forward, Monte Carlo, sensibilité) |
| 12 IA | à venir (après au moins 3 mois de journal réel) |

## Démarrage rapide (local)
```bash
uv sync                          # installe Python 3.12 + dépendances
uv run pytest                    # suite de tests complète
npm ci && npm run test:railway   # test de l'infrastructure Railway (Node 22)
uv run tradebot data-synthetic   # données ALÉATOIRES pour essayer
uv run tradebot backtest --symbol SYNTH --strategy asian_breakout
```

## Documentation
- [docs/DEPLOIEMENT.md](docs/DEPLOIEMENT.md) : mise en production sur VPS, pas à pas, depuis le téléphone
- [docs/RAILWAY.md](docs/RAILWAY.md) : alternative hébergée (Railway) : fichiers, risques, tests
- [docs/RUNBOOK.md](docs/RUNBOOK.md) : que faire en cas d'incident
- [docs/ROADMAP.md](docs/ROADMAP.md) : avancement et critères go/no-go
- [docs/hypotheses/](docs/hypotheses/) : stratégies pré-enregistrées

## Sécurité en bref
Aucun port public (Tailscale) · secrets uniquement dans `.env` (chmod 600, ignoré par git, recherche
de secrets en CI) · SSH par clé uniquement · commandes qui augmentent le risque protégées par TOTP ·
verrou logiciel contre le compte réel (`RealMoneyLocked`).
