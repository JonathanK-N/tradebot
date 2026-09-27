# Runbook — que faire quand…

Principe : **d'abord protéger le capital, ensuite comprendre.** Toutes les actions ci-dessous se font au téléphone.

| Situation | Action immédiate (< 1 min) | Ensuite |
|---|---|---|
| Doute sur ce que fait le bot | Telegram `/pause` (plus d'entrées, positions conservées avec SL/TP) | `/status`, `/positions`, lire le journal |
| Comportement anormal, position inattendue | Telegram `/kill` **ou** bouton « Tout fermer » du dashboard | Vérifier dans l'**app MT5** que tout est fermé |
| Telegram et dashboard muets | **App MT5 du broker** → fermer les positions | Termius : `docker compose ps`, `docker compose logs --tail 200 live` |
| Alerte « position SANS stop-loss » | App MT5 : poser le SL ou fermer | Chercher la cause dans les logs du pont (`var\bridge.log`) |
| Alerte « pont injoignable » | Rien d'urgent : les SL/TP sont chez le broker ; le pont refuse les nouvelles entrées | Windows App → le terminal MT5 est-il ouvert et connecté ? `Start-ScheduledTask tradebot-bridge` |
| healthchecks.io signale le silence | `/status` ; si pas de réponse : app MT5 | Termius : état du VPS, `docker compose up -d` |
| Calendrier « fail-closed » | Rien : c'est voulu, aucune entrée tant qu'il est périmé | Tester `tb calendar-week` ; flux ForexFactory changé ? |
| Limite journalière / hebdo atteinte | Rien : positions fermées, reprise automatique à la période suivante | Revue écrite des trades du jour |
| **HALT drawdown** | Rien : le système est à l'arrêt complet | Revue écrite **obligatoire** (causes, conformité aux attentes Monte Carlo) avant `/reset_halt <TOTP>` |
| Spécification du symbole changée (alerte au démarrage) | `/pause` | Vérifier contrat, pas de volume, horaires chez le broker ; mettre à jour la config |

## Checklist hebdomadaire (dimanche, 15 min)
- [ ] Rapport réel vs backtest : nombre de trades, espérance en R, slippage moyen réel vs modélisé
- [ ] Refus du risque : motifs dominants (`/api/events?kind=decision`)
- [ ] Aucune intervention manuelle non documentée
- [ ] Sauvegarde de la nuit OK (`tail backup.log`)
- [ ] Mises à jour du VPS Windows faites ce week-end
