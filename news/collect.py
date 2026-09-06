"""1日分・期間分・日次の市場ニュース生成。"""

from __future__ import annotations

import time
from datetime import date
from pathlib import Path
from typing import Any

from tqdm import tqdm

from news.calendar import iso_now, iter_trading_days, parse_day, session_date_for_daily, yyyymmdd
from news.indices import (
    all_tickers,
    build_indices,
    download_history,
    has_primary_bar,
    sector_changes,
    sectors_from_heatmap_json,
    top_bottom_sectors,
)
from news.llm import llm_configured
from news.schema import empty_payload, normalize_payload
from news.sources import collect_articles
from news.storage import ROOT, load_day, prune_news, write_day
from news.summarize import build_payload

HEATMAP_JSON = {
    "JP": ROOT / "data" / "jp.json",
    "US": ROOT / "data" / "us.json",
}


def _markets(value: str) -> list[str]:
    text = str(value or "all").strip().lower()
    if text in {"all", "both"}:
        return ["JP", "US"]
    if text in {"jp", "日本株"}:
        return ["JP"]
    if text in {"us", "米株"}:
        return ["US"]
    raise ValueError(f"unknown market: {value}")


def _is_recent(day: date, today: date | None = None) -> bool:
    if today is None:
        today = date.today()
    return (today - day).days <= 3


def collect_one(
    market: str,
    day: date | str,
    *,
    raw=None,
    mode: str = "backfill",
    force: bool = False,
    include_gdelt: bool | None = None,
    use_llm: bool = True,
) -> dict[str, Any] | None:
    code = "US" if str(market).upper() == "US" else "JP"
    session_day = parse_day(day)
    iso = yyyymmdd(session_day)
    existing = load_day(code, iso)
    if existing and existing.get("news_bullets") and not force:
        return existing

    if raw is None:
        raw = download_history(all_tickers(code), session_day, session_day)
    if not has_primary_bar(code, raw, session_day):
        print(f"  skip non-session {code} {iso} (no index bar)", flush=True)
        return None

    indices = build_indices(code, raw, session_day)
    heatmap_rows = sectors_from_heatmap_json(HEATMAP_JSON[code], session_day)
    changes = heatmap_rows if heatmap_rows else sector_changes(code, raw, session_day)
    top, bottom = top_bottom_sectors(changes, 2)

    recent = mode == "daily" or _is_recent(session_day)
    use_gdelt = include_gdelt if include_gdelt is not None else (mode == "backfill" and not recent)
    articles = collect_articles(code, session_day, recent=recent or mode == "daily", include_gdelt=use_gdelt)

    payload = build_payload(
        market=code,
        date=iso,
        indices=indices,
        top_sectors=top,
        bottom_sectors=bottom,
        articles=articles,
        generated_at=iso_now(),
        mode=mode,
        use_llm=use_llm,
    )
    path = write_day(code, payload)
    print(f"  saved {path}  bullets={len(payload['news_bullets'])}", flush=True)
    return normalize_payload(payload)


def collect_range(
    market: str,
    start: date | str,
    end: date | str,
    *,
    force: bool = False,
    sleep_sec: float = 1.2,
    include_gdelt: bool = False,
    use_llm: bool = True,
) -> list[Path]:
    codes = _markets(market)
    start_d = parse_day(start)
    end_d = parse_day(end)
    saved: list[Path] = []
    if use_llm and not llm_configured():
        print("warning: GEMINI_API_KEY / OPENAI_API_KEY が無いのでルールベースで書きます", flush=True)
        use_llm = False
    for code in codes:
        days = iter_trading_days(code, start_d, end_d)
        print(f"{code}: {len(days)} trading days {start_d}..{end_d} (holidays skipped)", flush=True)
        if not days:
            continue
        raw = download_history(all_tickers(code), start_d, end_d)
        bar = tqdm(days, desc=f"{code} backfill", unit="day")
        for i, day in enumerate(bar, start=1):
            bar.set_postfix_str(str(day))
            try:
                payload = collect_one(
                    code,
                    day,
                    raw=raw,
                    mode="backfill",
                    force=force,
                    include_gdelt=include_gdelt,
                    use_llm=use_llm,
                )
            except Exception as exc:  # noqa: BLE001
                print(f"  error {code} {day}: {exc}", flush=True)
                payload = None
            if payload:
                saved.append(ROOT / "data" / "market_news" / code.lower() / f"{payload['date']}.json")
            if i < len(days) and sleep_sec > 0:
                time.sleep(sleep_sec)
    return saved


def collect_daily(
    market: str = "all",
    *,
    force: bool = False,
    use_llm: bool = True,
    asof: str | None = None,
) -> list[dict[str, Any]]:
    codes = _markets(market)
    out: list[dict[str, Any]] = []
    if use_llm and not llm_configured():
        print("warning: GEMINI_API_KEY / OPENAI_API_KEY が無いのでルールベースで書きます", flush=True)
        use_llm = False
    for code in codes:
        day = parse_day(asof) if asof else session_date_for_daily(code)
        print(f"daily {code} session={day}", flush=True)
        try:
            payload = collect_one(code, day, mode="daily", force=force, include_gdelt=False, use_llm=use_llm)
            if payload is None:
                prev = previous_or_retry(code, day)
                print(f"  retry previous session {prev}", flush=True)
                payload = collect_one(code, prev, mode="daily", force=force, include_gdelt=False, use_llm=use_llm)
        except Exception as exc:  # noqa: BLE001
            print(f"  error daily {code}: {exc}", flush=True)
            payload = None
        if payload:
            out.append(payload)
        prune_news(code)
    return out


def previous_or_retry(market: str, day: date) -> date:
    from news.calendar import previous_trading_day

    return previous_trading_day(market, day)


def placeholder_if_needed(market: str, day: date | str) -> dict[str, Any]:
    code = "US" if str(market).upper() == "US" else "JP"
    iso = yyyymmdd(parse_day(day))
    payload = empty_payload(code, iso)
    payload["generated_at"] = iso_now()
    payload["mode"] = "empty"
    payload["trading_day"] = False
    return payload
