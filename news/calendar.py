"""東証 / NYSE の営業日判定。pandas_market_calendars と holidays を使う。"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from typing import Iterable
from zoneinfo import ZoneInfo

JP_TZ = ZoneInfo("Asia/Tokyo")
US_TZ = ZoneInfo("America/New_York")

_JP_CAL_NAMES = ("JPX", "XTKS", "TSE", "XJPX")
_US_CAL_NAMES = ("NYSE", "XNYS")


def parse_day(value: str | date | datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def is_weekend(day: date) -> bool:
    return day.weekday() >= 5


@lru_cache(maxsize=4)
def _exchange_calendar(market: str):
    try:
        import pandas_market_calendars as mcal
    except Exception:
        return None
    names = _US_CAL_NAMES if str(market).upper() == "US" else _JP_CAL_NAMES
    for name in names:
        try:
            return mcal.get_calendar(name)
        except Exception:
            continue
    return None


def _holidays_closed(market: str, day: date) -> bool:
    try:
        import holidays
    except Exception:
        return False
    year = day.year
    if str(market).upper() == "US":
        cal = holidays.NYSE(years=year)
        return day in cal
    cal = holidays.country_holidays("JP", years=year)
    extra = {
        date(year, 1, 1),
        date(year, 1, 2),
        date(year, 1, 3),
        date(year, 12, 31),
    }
    return day in cal or day in extra


def is_trading_day(market: str, day: date | str) -> bool:
    d = parse_day(day)
    cal = _exchange_calendar(market)
    if cal is not None:
        try:
            sessions = cal.valid_days(start_date=d.isoformat(), end_date=d.isoformat())
            return len(sessions) > 0
        except Exception:
            pass
    if is_weekend(d) or _holidays_closed(market, d):
        return False
    return True


def previous_trading_day(market: str, day: date | str) -> date:
    d = parse_day(day) - timedelta(days=1)
    guard = 0
    while not is_trading_day(market, d):
        d -= timedelta(days=1)
        guard += 1
        if guard > 30 or d.year < 2018:
            raise RuntimeError("trading day lookup went too far back")
    return d


def iter_trading_days(market: str, start: date | str, end: date | str) -> list[date]:
    start_d = parse_day(start)
    end_d = parse_day(end)
    if end_d < start_d:
        start_d, end_d = end_d, start_d
    cal = _exchange_calendar(market)
    if cal is not None:
        try:
            sessions = cal.valid_days(start_date=start_d.isoformat(), end_date=end_d.isoformat())
            return [s.date() if hasattr(s, "date") else parse_day(str(s)[:10]) for s in sessions]
        except Exception:
            pass
    out: list[date] = []
    cur = start_d
    while cur <= end_d:
        if is_trading_day(market, cur):
            out.append(cur)
        cur += timedelta(days=1)
    return out


def session_date_for_daily(market: str, now: datetime | None = None) -> date:
    """クローズ後に実行した前提で、対象セッション日を返す。"""
    code = str(market).upper()
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if code == "US":
        local = now.astimezone(US_TZ)
        candidate = local.date()
        if local.hour < 16 or not is_trading_day("US", candidate):
            candidate = previous_trading_day("US", candidate)
        return candidate
    local = now.astimezone(JP_TZ)
    candidate = local.date()
    if local.hour < 16 or not is_trading_day("JP", candidate):
        candidate = previous_trading_day("JP", candidate)
    return candidate


def session_window_utc(market: str, day: date) -> tuple[datetime, datetime]:
    code = str(market).upper()
    if code == "US":
        start = datetime(day.year, day.month, day.day, 0, 0, tzinfo=US_TZ) - timedelta(hours=8)
        end = datetime(day.year, day.month, day.day, 16, 30, tzinfo=US_TZ)
        return start.astimezone(timezone.utc), end.astimezone(timezone.utc)
    start = datetime(day.year, day.month, day.day, 0, 0, tzinfo=JP_TZ) - timedelta(hours=6)
    end = datetime(day.year, day.month, day.day, 16, 0, tzinfo=JP_TZ)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def iso_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def yyyymmdd(day: date) -> str:
    return day.isoformat()


def gdelt_stamp(dt: datetime) -> str:
    utc = dt.astimezone(timezone.utc)
    return utc.strftime("%Y%m%d%H%M%S")


def filter_days(market: str, days: Iterable[date | str]) -> list[date]:
    return [parse_day(d) for d in days if is_trading_day(market, d)]
