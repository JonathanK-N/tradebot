# H2 — Pullback dans la tendance, H1 (pré-enregistrement)

> Rédigé **avant** tout backtest sur données réelles. Toute modification a posteriori = H2b.

## Hypothèse de marché
L'or connaît des tendances persistantes (taux réels, achats des banques centrales, flux des ETF). Dans
une tendance établie, un retour vers la moyenne courte suivi d'une reprise offre un point d'entrée avec
un meilleur rapport gain/risque qu'une entrée en poursuite. Si c'est vrai, ces entrées ont une espérance
positive **après coûts et swaps** (les positions peuvent durer plusieurs jours).

## Règles (code : `src/tradebot/strategy/trend_pullback.py`)
| Élément | Règle (achat ; vente symétrique) |
|---|---|
| Tendance | EMA50 > EMA200 et clôture > EMA200 (bougies H1) |
| Pullback | plus bas ≤ EMA20 < clôture, bougie haussière |
| Session | entrée uniquement pendant Londres, l'overlap ou New York (hors Asie et fin de NY) |
| Stop | 1,5 ATR(14, H1) |
| Objectif | 2 R |
| Sortie forcée | 48 h |
| Pause | 6 bougies après chaque signal |

## Ce qui invaliderait l'hypothèse
Mêmes critères que H1. **Point d'attention : les swaps.** Un achat d'or porte un swap négatif
significatif ; si l'espérance devient négative avec les swaps réels du broker, l'hypothèse est rejetée
pour les achats.

## Grille autorisée (fixée maintenant)
`stop_atr ∈ {1.0, 1.5, 2.0}`, `reward_risk ∈ {1.5, 2.0, 3.0}` — 9 combinaisons.

## Hors périmètre (hypothèses futures, testées séparément)
Filtre de régime par les taux réels (FRED DFII10), par le VIX, par la tendance du dollar. Les ajouter à H2
maintenant serait du data snooping.
