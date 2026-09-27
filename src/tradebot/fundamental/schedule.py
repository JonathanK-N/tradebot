"""Calendrier HISTORIQUE reconstruit pour les backtests.

Honnêteté sur la qualité :
- FOMC : dates des décisions programmées, saisies à la main depuis mémoire.
  ⚠️ À VÉRIFIER contre federalreserve.gov/monetarypolicy/fomccalendars.htm avant
  de s'y fier (``tradebot calendar verify`` affiche la liste pour contrôle).
  Les réunions d'urgence (ex. mars 2020) ne sont pas incluses.
- NFP : APPROXIMATION « premier vendredi du mois, 8:30 NY ». Le BLS s'en écarte
  parfois (ex. quand le 1er vendredi tombe le 1er/2 du mois, jours fériés,
  shutdown). Le filtre news en backtest est donc imparfait.
- CPI, PCE, discours : dates irrégulières -> non générées ici. Les ajouter via un
  CSV (``data/calendar/history.csv``) construit depuis les calendriers officiels
  (bls.gov/schedule, bea.gov/news/schedule).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from tradebot.core.sessions import NEW_YORK
from tradebot.fundamental.calendar import EconomicEvent

# Jour de l'ANNONCE (2e jour de la réunion), communiqué à 14:00 NY.
FOMC_DECISION_DATES: tuple[str, ...] = (
    "2019-01-30", "2019-03-20", "2019-05-01", "2019-06-19",
    "2019-07-31", "2019-09-18", "2019-10-30", "2019-12-11",
    "2020-01-29", "2020-04-29", "2020-06-10", "2020-07-29",
    "2020-09-16", "2020-11-05", "2020-12-16",
    "2021-01-27", "2021-03-17", "2021-04-28", "2021-06-16",
    "2021-07-28", "2021-09-22", "2021-11-03", "2021-12-15",
    "2022-01-26", "2022-03-16", "2022-05-04", "2022-06-15",
    "2022-07-27", "2022-09-21", "2022-11-02", "2022-12-14",
    "2023-02-01", "2023-03-22", "2023-05-03", "2023-06-14",
    "2023-07-26", "2023-09-20", "2023-11-01", "2023-12-13",
    "2024-01-31", "2024-03-20", "2024-05-01", "2024-06-12",
    "2024-07-31", "2024-09-18", "2024-11-07", "2024-12-18",
    "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18",
    "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-10",
    "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17",
    "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09",
)


def _ny(d: date, t: time) -> datetime:
    return datetime.combine(d, t, tzinfo=NEW_YORK)


def fomc_events() -> list[EconomicEvent]:
    return [
        EconomicEvent(
            _ny(date.fromisoformat(d), time(14, 0)).astimezone(UTC),
            "USD",
            "FOMC Federal Funds Rate decision",
            "high",
        )
        for d in FOMC_DECISION_DATES
    ]


def first_friday(year: int, month: int) -> date:
    d = date(year, month, 1)
    return d + timedelta(days=(4 - d.weekday()) % 7)


def nfp_events_approx(start_year: int, end_year: int) -> list[EconomicEvent]:
    out = []
    for y in range(start_year, end_year + 1):
        for m in range(1, 13):
            ts = _ny(first_friday(y, m), time(8, 30)).astimezone(UTC)
            out.append(EconomicEvent(ts, "USD", "Non-Farm Payrolls (approx.)", "high"))
    return out


def historical_events(start_year: int, end_year: int) -> list[EconomicEvent]:
    evs = [e for e in fomc_events() if start_year <= e.ts.year <= end_year]
    return sorted(evs + nfp_events_approx(start_year, end_year), key=lambda e: e.ts)
