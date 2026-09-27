import lzma
import struct
from datetime import date, timedelta

import pandas as pd

from tests.conftest import utc
from tradebot.core.config import NewsConfig
from tradebot.data.dukascopy import RECORD, decode_candles
from tradebot.data.quality import check_quality, market_open_mask
from tradebot.data.store import BarStore
from tradebot.data.synthetic import generate_m1
from tradebot.fundamental.calendar import EconomicEvent, EventCalendar, load_events_csv, save_events_csv
from tradebot.fundamental.forexfactory import parse_ff
from tradebot.fundamental.schedule import first_friday, historical_events

NFP = EconomicEvent(utc(2024, 6, 7, 12, 30), "USD", "Non-Farm Employment Change", "high")
FOMC = EconomicEvent(utc(2024, 6, 12, 18, 0), "USD", "FOMC Statement", "high")


def test_blackout_windows():
    cal = EventCalendar(NewsConfig(), [NFP, FOMC])
    assert cal.check(NFP.ts - timedelta(minutes=29)).blocked
    assert cal.check(NFP.ts + timedelta(minutes=59)).blocked  # NFP : 60 min après
    assert not cal.check(NFP.ts + timedelta(minutes=61)).blocked
    assert cal.check(FOMC.ts + timedelta(minutes=89)).blocked  # FOMC : 90 min après


def test_filters_currency_and_impact():
    ev = [EconomicEvent(NFP.ts, "EUR", "ECB", "high"), EconomicEvent(NFP.ts, "USD", "X", "medium")]
    assert not EventCalendar(NewsConfig(), ev).check(NFP.ts).blocked


def test_fail_closed_when_stale():
    cal = EventCalendar(NewsConfig(), require_fresh=True)
    assert cal.check(NFP.ts).blocked  # jamais chargé
    cal.set_events([], refreshed_at=NFP.ts - timedelta(hours=40))
    assert "périmé" in cal.check(NFP.ts).reason
    cal.set_events([], refreshed_at=NFP.ts - timedelta(hours=1))
    assert not cal.check(NFP.ts).blocked


def test_csv_roundtrip(tmp_path):
    save_events_csv([NFP, FOMC], tmp_path / "c.csv")
    assert load_events_csv(tmp_path / "c.csv") == [NFP, FOMC]


def test_forexfactory_parse():
    evs = parse_ff([
        {"title": "CPI m/m", "country": "USD", "date": "2024-06-12T08:30:00-04:00", "impact": "High"},
        {"title": "Bank Holiday", "country": "USD", "date": "2024-06-19T00:00:00-04:00",
         "impact": "Holiday"},
        {"title": "bad", "country": "USD", "date": "All Day", "impact": "High"},
    ])
    assert len(evs) == 1 and evs[0].ts == utc(2024, 6, 12, 12, 30) and evs[0].impact == "high"


def test_schedule():
    assert first_friday(2024, 6) == date(2024, 6, 7)
    evs = historical_events(2024, 2024)
    assert sum("FOMC" in e.title for e in evs) == 8 and sum("Non-Farm" in e.title for e in evs) == 12


def test_dukascopy_decode():
    recs = [(0, 2000_000, 2000_500, 1999_900, 2000_700, 1.5), (60, 2000_500, 2001_000, 2000_400, 2001_200, 2.0)]
    raw = lzma.compress(b"".join(RECORD.pack(*r) for r in recs), format=lzma.FORMAT_ALONE)
    df = decode_candles(raw, date(2024, 1, 2), 1000.0)
    assert df["open"].tolist() == [2000.0, 2000.5] and df["high"].iloc[1] == 2001.2
    assert df["ts"].iloc[1] == utc(2024, 1, 2, 0, 1)
    assert struct.calcsize(">IIIIIf") == 24


def test_quality_and_store(tmp_path):
    df = generate_m1("2024-01-02", "2024-01-10")
    rep = check_quality(df, "XAUUSD")
    assert rep.passed and rep.missing_open_minutes == 0
    holed = df.drop(df.index[1000:1100])
    rep2 = check_quality(holed, "XAUUSD")
    assert rep2.missing_open_minutes == 100 and rep2.largest_gaps[0][1] == 100

    store = BarStore(tmp_path)
    store.write("XAUUSD", df)
    store.write("XAUUSD", df)  # idempotent
    back = store.load("XAUUSD")
    assert len(back) == len(df)


def test_open_mask_matches_scalar():
    from tradebot.core.sessions import is_market_open

    idx = pd.date_range("2024-03-01", "2024-03-20", freq="17min", tz="UTC")
    vec = market_open_mask(idx)
    assert all(vec[i] == is_market_open(ts.to_pydatetime()) for i, ts in enumerate(idx))
