# H1 — Cassure du range asiatique (pré-enregistrement)

> Document rédigé **avant** tout backtest sur données réelles. La date du commit git en fait foi.
> Tout changement de règle ou de paramètre après avoir vu des résultats = **nouvelle hypothèse (H1b)**,
> évaluée sur une période qui n'a pas servi à la concevoir.

## Hypothèse de marché
Pendant la session asiatique, la liquidité sur l'or est plus faible et le prix évolue souvent dans un
range. L'ouverture de Londres (flux européens, fixing LBMA du matin à 10:30) peut déclencher une
expansion directionnelle. Si l'hypothèse est vraie, la **première** clôture M15 hors du range, dans
les heures suivant l'ouverture, a une espérance positive **après coûts**.

## Règles (code : `src/tradebot/strategy/asian_breakout.py`)
| Élément | Règle |
|---|---|
| Range | plus haut / plus bas des bougies M15 entre 00:00 et 07:00 (heure de Londres, DST géré) |
| Validité | ≥ 80 % des bougies présentes ; largeur entre 2 et 12 ATR(14, M15) |
| Entrée | 1re clôture M15 > haut + 0,1 ATR (achat) ou < bas − 0,1 ATR (vente), entre 07:00 et 11:00 Londres |
| Stop | milieu du range (invalidation : le prix revient dans la zone d'équilibre) |
| Objectif | 1,5 R |
| Sortie forcée | 16:30 Londres |
| Fréquence | 1 tentative maximum par jour (même si le risque la refuse) |

## Ce qui invaliderait l'hypothèse
- Espérance après coûts ≤ 0 sur l'historique complet, ou IC 95 % qui inclut 0.
- Walk-forward : WFE < 0,5 ou moins de 60 % des fenêtres hors échantillon positives.
- Performance concentrée sur une seule année ou un seul régime (ex. 2020).

## Risques spécifiques identifiés
- Les jours de NFP/CPI (13:30 Londres l'été) sont hors fenêtre d'entrée, mais une position ouverte
  à 10:00 peut encore être ouverte à l'annonce. Le filtre news ne bloque que les **entrées**.
  → À mesurer : performance des trades encore ouverts pendant une annonce.
- Fausses cassures fréquentes les jours sans catalyseur.

## Grille autorisée pour le walk-forward (fixée maintenant)
`reward_risk ∈ {1.0, 1.5, 2.0}`, `buffer_atr ∈ {0.05, 0.10, 0.20}`, `max_range_atr ∈ {8, 12}` — 18 combinaisons.
