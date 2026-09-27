# Feuille de route — état d'avancement

Légende : ✅ code livré et testé · 🔶 code livré, validation à faire sur données/comptes réels · ⬜ à faire

**Le code d'une étape n'est pas sa validation.** Chaque critère go/no-go ci-dessous se juge sur
TES données et TES comptes, pas sur les tests unitaires.

| Sem. | Phase | Code | Critère go/no-go (à cocher par toi) |
|---|---|---|---|
| 1 | Fondations : dépôt, CI, config, logs, secrets, VPS | ✅ | ⬜ déploiement fait entièrement au téléphone · ⬜ `nmap <ip publique>` : aucun port ouvert |
| 2 | Données : Dukascopy, Parquet, qualité, FRED | 🔶 | ⬜ < 0,1 % de minutes manquantes, 0 incohérence OHLC (`tb data-quality`) |
| 3 | Moteur décisionnel : modèles, interface Strategy, sessions/DST, H1 et H2 pré-enregistrées | ✅ | ⬜ hypothèses committées **avant** le premier backtest réel |
| 4 | Risque v1 : sizing, limites, HALT, kill switch, état persistant | ✅ | ✅ tests par propriétés (500 cas) · ⬜ relecture des valeurs de `config/default.yaml` |
| 5 | Backtest : moteur événementiel, coûts, swaps, gaps | ✅ | ✅ trades vérifiés à la main · ✅ test anti look-ahead · ⬜ calibrage du spread vs broker |
| 6 | Fondamental : calendrier, filtre news, fail-closed, contexte intermarché | 🔶 | ⬜ dates FOMC vérifiées · ⬜ CPI/PCE ajoutés à `data/calendar/history.csv` |
| 7 | Backtests H1/H2 sur données réelles | 🔶 | ⬜ ≥ 100 trades, IC 95 % de l'espérance > 0 après coûts. **No-go = retour sem. 3, c'est acceptable.** |
| 8 | Walk-forward, Monte Carlo, sensibilité | ✅ | ⬜ WFE ≥ 0,5 · ⬜ ≥ 200 trades OOS, t ≥ 2 · ⬜ DD P95 < 10 % · ⬜ stabilité ≥ 0,5 |
| 9 | Adaptateur MT5 + pont Windows | 🔶 | ⬜ 100 ordres démo : 0 doublon, 0 orphelin, 100 % avec SL côté broker |
| 10 | Automatisation, alertes Telegram, dead man's switch | ✅ | ⬜ kill depuis le téléphone < 10 s · ⬜ chaos : tuer MT5, couper le réseau, redémarrer le VPS |
| 11 | Dashboard PWA | ✅ | ⬜ installé sur l'écran d'accueil, accessible **seulement** via Tailscale |
| 12 | Forward test démo | ⬜ | ⬜ 2 semaines sans incident technique, puis démo **≥ 4–8 semaines** au total |
| 13+ | Micro-lot réel | ⬜ | ⬜ démo dans l'intervalle de confiance du backtest · ⬜ broker vérifié AMF/OCRI · ⬜ avis CPA fiscalité · ⬜ `risk_per_trade_pct: 0.25` |
| — | IA (phase 10) | ⬜ | ⬜ ≥ 3 mois de journal réel ; l'IA **filtre** des signaux, ne les crée pas, en shadow mode d'abord |

## Limites connues (dette assumée)
- Adaptateur MT5 testé contre un **faux** terminal : les comportements réels du broker (modes de
  remplissage, codes de retour, décalage horaire) sont à valider en semaine 9.
- Calendrier historique partiel (FOMC + NFP approximatif) → le filtre news du backtest est imparfait.
- `TrendPullback` en live nécessite ~9 jours d'historique pour ses indicateurs (fourni par le pont).
- Le journal ne relie pas encore automatiquement un trade MT5 clôturé à son signal d'origine
  (lien par `position_id` via l'événement `order`).
