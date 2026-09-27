"""Calendrier des sessions de l'or, robuste aux changements d'heure (DST).

Règle d'or : on stocke tout en UTC, mais les sessions sont définies en heure
LOCALE de leur place (Londres, New York) puis converties. Sinon, deux fois par an
(et pendant les ~3 semaines où l'Europe et les États-Unis ne changent pas d'heure
le même jour), toutes les fenêtres sont décalées d'une heure.

Conventions (standard du marché spot/CFD de l'or, à confronter à ton broker) :
- Ouverture hebdo : dimanche 18:00 New York. Fermeture : vendredi 17:00 New York.
- Pause quotidienne : 17:00–18:00 New York (rollover : spreads très larges, swaps).
- « Journée de trading » : commence à 17:00 NY. Un trade à 18:30 NY le lundi
  appartient donc à la journée du mardi (convention NY close, utilisée pour
  remettre à zéro les limites de perte journalières).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo("America/New_York")
LONDON = ZoneInfo("Europe/London")
TOKYO = ZoneInfo("Asia/Tokyo")

DAILY_CLOSE_NY = time(17, 0)
DAILY_REOPEN_NY = time(18, 0)


class Session(StrEnum):
    ASIA = "asia"
    LONDON = "london"
    OVERLAP = "london_ny_overlap"
    NEW_YORK = "new_york"
    LATE = "late_ny"
    CLOSED = "closed"


def ensure_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        raise ValueError(f"datetime naïf interdit (ambiguïté de fuseau) : {ts!r}")
    return ts.astimezone(UTC)


def to_ny(ts: datetime) -> datetime:
    return ensure_utc(ts).astimezone(NEW_YORK)


def to_london(ts: datetime) -> datetime:
    return ensure_utc(ts).astimezone(LONDON)


def is_market_open(ts: datetime) -> bool:
    """Marché ouvert selon le calendrier standard (hors jours fériés)."""
    ny = to_ny(ts)
    wd = ny.weekday()  # lundi=0 ... dimanche=6
    t = ny.time()
    if wd == 5:  # samedi
        return False
    if wd == 6:  # dimanche : ouvre à 18:00
        return t >= DAILY_REOPEN_NY
    if wd == 4 and t >= DAILY_CLOSE_NY:  # vendredi après 17:00
        return False
    # pause quotidienne 17:00-18:00
    return not (DAILY_CLOSE_NY <= t < DAILY_REOPEN_NY)


def trading_day(ts: datetime) -> date:
    """Journée de trading (convention NY 17:00)."""
    return (to_ny(ts) + timedelta(hours=7)).date()


def trading_week(ts: datetime) -> tuple[int, int]:
    """Semaine ISO de la journée de trading (dimanche soir -> semaine du lundi)."""
    iso = trading_day(ts).isocalendar()
    return iso.year, iso.week


def minutes_to_daily_close(ts: datetime) -> float:
    ny = to_ny(ts)
    close = datetime.combine(ny.date(), DAILY_CLOSE_NY, tzinfo=NEW_YORK)
    if ny >= close:
        close += timedelta(days=1)
    return (close - ny).total_seconds() / 60.0


def is_rollover_window(ts: datetime, before_min: int = 15, after_min: int = 15) -> bool:
    """Autour de 17:00 NY : spreads élargis, liquidité faible -> pas d'entrée."""
    ny = to_ny(ts)
    close = datetime.combine(ny.date(), DAILY_CLOSE_NY, tzinfo=NEW_YORK)
    reopen = datetime.combine(ny.date(), DAILY_REOPEN_NY, tzinfo=NEW_YORK)
    return close - timedelta(minutes=before_min) <= ny < reopen + timedelta(minutes=after_min)


def is_friday_cutoff(ts: datetime, cutoff_ny: time = time(14, 0)) -> bool:
    """Vendredi après l'heure limite : pas de nouvelle position (risque de gap du week-end)."""
    ny = to_ny(ts)
    return ny.weekday() == 4 and ny.time() >= cutoff_ny


def session_of(ts: datetime) -> Session:
    if not is_market_open(ts):
        return Session.CLOSED
    ldn = to_london(ts).time()
    ny = to_ny(ts).time()
    london_open = time(8, 0) <= ldn < time(16, 30)
    ny_open = time(8, 0) <= ny < time(17, 0)
    if london_open and ny_open:
        return Session.OVERLAP
    if london_open:
        return Session.LONDON
    if ny_open:
        return Session.NEW_YORK if ny < time(13, 30) else Session.LATE
    return Session.ASIA


def local_window(day: date, start: time, end: time, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """Fenêtre [start, end) en heure locale ``tz`` pour ``day``, renvoyée en UTC."""
    s = datetime.combine(day, start, tzinfo=tz).astimezone(UTC)
    e = datetime.combine(day, end, tzinfo=tz).astimezone(UTC)
    if e <= s:
        e += timedelta(days=1)
    return s, e


def is_new_york_rollover_crossed(prev: datetime, now: datetime) -> bool:
    """Vrai si l'instant 17:00 NY se situe dans (prev, now] — sert aux swaps."""
    return trading_day(prev) != trading_day(now)
