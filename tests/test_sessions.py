"""Sessions et heure d'été : la source de bugs silencieux n°1 des bots de trading."""

from datetime import date, time

from tests.conftest import utc
from tradebot.core.sessions import (
    LONDON,
    Session,
    is_friday_cutoff,
    is_market_open,
    is_rollover_window,
    local_window,
    session_of,
    trading_day,
)


def test_weekend_closed():
    assert not is_market_open(utc(2024, 6, 8, 12))  # samedi
    assert not is_market_open(utc(2024, 6, 9, 21))  # dimanche 17:00 NY (EDT)
    assert is_market_open(utc(2024, 6, 9, 22, 1))  # dimanche 18:01 NY


def test_friday_close_follows_dst():
    # été (EDT, UTC-4) : fermeture 21:00 UTC ; hiver (EST, UTC-5) : 22:00 UTC
    assert is_market_open(utc(2024, 6, 7, 20, 59))
    assert not is_market_open(utc(2024, 6, 7, 21, 0))
    assert is_market_open(utc(2024, 1, 5, 21, 30))
    assert not is_market_open(utc(2024, 1, 5, 22, 0))


def test_daily_break():
    assert not is_market_open(utc(2024, 6, 11, 21, 30))  # 17:30 NY
    assert is_market_open(utc(2024, 6, 11, 22, 0))  # 18:00 NY


def test_trading_day_rolls_at_ny_close():
    assert trading_day(utc(2024, 6, 11, 20, 59)) == date(2024, 6, 11)
    assert trading_day(utc(2024, 6, 11, 22, 30)) == date(2024, 6, 12)
    # dimanche soir -> journée du lundi
    assert trading_day(utc(2024, 6, 9, 23)) == date(2024, 6, 10)


def test_rollover_window():
    assert is_rollover_window(utc(2024, 6, 11, 20, 50))  # 16:50 NY
    assert not is_rollover_window(utc(2024, 6, 11, 20, 40))


def test_london_window_dst_gap_weeks():
    # 2024-03-18 : les USA sont en heure d'été, pas encore Londres (écart de 4 h au lieu de 5)
    s, e = local_window(date(2024, 3, 18), time(0), time(7), LONDON)
    assert s == utc(2024, 3, 18, 0) and e == utc(2024, 3, 18, 7)
    s, _ = local_window(date(2024, 6, 18), time(0), time(7), LONDON)
    assert s == utc(2024, 6, 17, 23)  # BST


def test_sessions():
    assert session_of(utc(2024, 6, 11, 2)) is Session.ASIA
    assert session_of(utc(2024, 6, 11, 9)) is Session.LONDON
    assert session_of(utc(2024, 6, 11, 13)) is Session.OVERLAP
    assert session_of(utc(2024, 6, 8, 13)) is Session.CLOSED


def test_friday_cutoff():
    assert is_friday_cutoff(utc(2024, 6, 7, 18, 30))  # 14:30 NY
    assert not is_friday_cutoff(utc(2024, 6, 6, 18, 30))  # jeudi
