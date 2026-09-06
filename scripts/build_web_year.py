"""Export the last year of heatmap days and market news for the public site."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from update_quotes import (  # noqa: E402
    DATA_DIR,
    LISTS_DIR,
    WEB_QUOTE_DAYS,
    compact_day_payload,
    prune_quote_archives,
    write_quote_index,
)

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

HISTORY = ROOT / "history"
COLS = ["Date", "Close", "Volume"]


def _market_label(market: str) -> str:
    return "米株" if market == "us" else "日本株"


def _meta(market: str) -> dict[str, dict]:
    name = "tickers_us.csv" if market == "us" else "tickers_jp.csv"
    path = LISTS_DIR / name
    out: dict[str, dict] = {}
    if not path.is_file():
        return out
    df = pd.read_csv(path, encoding="utf-8-sig")
    for rec in df.to_dict("records"):
        ticker = str(rec.get("ticker") or "")
        if ticker:
            out[ticker] = rec
    return out


def _bars_dir(market: str) -> Path:
    return HISTORY / ("data_us" if market == "us" else "data")


def _cutoff(keep_days: int) -> str:
    return (datetime.now() - timedelta(days=keep_days)).strftime("%Y-%m-%d")


def export_quotes(market: str, keep_days: int = WEB_QUOTE_DAYS) -> list[str]:
    folder = _bars_dir(market)
    files = sorted(folder.glob("*.parquet")) if folder.is_dir() else []
    if not files:
        print(f"{market}: no parquet in {folder}", flush=True)
        return []
    meta = _meta(market)
    cutoff = _cutoff(keep_days)
    by_day: dict[str, list[dict]] = defaultdict(list)
    print(f"{market}: scan {len(files):,} parquet files from {cutoff}", flush=True)
    for i, path in enumerate(files, start=1):
        ticker = path.stem
        rec = meta.get(ticker, {})
        try:
            df = pd.read_parquet(path, columns=COLS)
        except Exception:
            continue
        if df.empty or "Date" not in df.columns or "Close" not in df.columns:
            continue
        dates = pd.to_datetime(df["Date"], errors="coerce")
        close = pd.to_numeric(df["Close"], errors="coerce")
        vol = (
            pd.to_numeric(df["Volume"], errors="coerce").fillna(0)
            if "Volume" in df.columns
            else pd.Series(0.0, index=df.index)
        )
        keep = dates.notna() & close.notna()
        if int(keep.sum()) < 2:
            continue
        day_s = dates[keep].dt.strftime("%Y-%m-%d").to_numpy()
        close_v = close[keep].to_numpy()
        vol_v = vol[keep].to_numpy()
        order = day_s.argsort(kind="mergesort")
        day_s = day_s[order]
        close_v = close_v[order]
        vol_v = vol_v[order]
        name = str(rec.get("銘柄名") or ticker)
        sector = str(rec.get("33業種区分") or rec.get("17業種区分") or rec.get("市場・商品区分") or "その他")
        for idx in range(1, len(day_s)):
            asof = str(day_s[idx])
            if asof < cutoff:
                continue
            last = float(close_v[idx])
            prev = float(close_v[idx - 1])
            if prev == 0:
                continue
            vol_last = int(float(vol_v[idx]))
            start = max(0, idx - 5)
            prev_vol = vol_v[start:idx]
            vol_avg = float(prev_vol.mean()) if len(prev_vol) else 0.0
            by_day[asof].append(
                {
                    "ticker": ticker,
                    "name": name,
                    "sector": sector,
                    "price": last,
                    "change_pct": (last - prev) / prev * 100.0,
                    "volume": vol_last,
                    "vol_ratio": (vol_last / vol_avg) if vol_avg else 1.0,
                    "turnover": last * vol_last,
                }
            )
        if i % 800 == 0 or i == len(files):
            print(f"  {market} {i}/{len(files)}  days={len(by_day)}", flush=True)

    label = _market_label(market)
    dest = DATA_DIR / "quotes" / market
    dest.mkdir(parents=True, exist_ok=True)
    min_count = 80 if market == "jp" else 120
    written: list[str] = []
    for asof in sorted(by_day):
        quotes = by_day[asof]
        if len(quotes) < min_count:
            continue
        payload = compact_day_payload(label, asof, quotes)
        path = dest / f"{asof}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        written.append(asof)
    prune_quote_archives(market, keep_days)
    days = write_quote_index(market)
    print(f"{market}: wrote {len(written):,} compact days  index={len(days)}", flush=True)
    return days


def _has_event_news(payload: dict) -> bool:
    for line in payload.get("news_bullets") or []:
        text = str(line)
        if "前日比" in text or "セクター" in text:
            continue
        if text.strip():
            return True
    return False


def export_news(market: str, days: list[str], *, sleep_sec: float, use_llm: bool) -> int:
    if not days:
        return 0
    from news.calendar import parse_day
    from news.collect import collect_one
    from news.indices import all_tickers, download_history
    from news.llm import llm_configured
    from news.storage import load_day

    code = "US" if market == "us" else "JP"
    if use_llm and not llm_configured():
        print("news: no LLM key, using headlines + index facts", flush=True)
        use_llm = False
    start_d = parse_day(days[0])
    end_d = parse_day(days[-1])
    print(f"{code}: news {len(days)} days {days[0]}..{days[-1]}", flush=True)
    raw = download_history(all_tickers(code), start_d, end_d)
    saved = 0
    for i, asof in enumerate(days, start=1):
        existing = load_day(code, asof)
        if existing and _has_event_news(existing):
            continue
        try:
            payload = collect_one(
                code,
                parse_day(asof),
                raw=raw,
                mode="backfill",
                force=True,
                include_gdelt=False,
                use_llm=use_llm,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  news error {code} {asof}: {exc}", flush=True)
            payload = None
        if payload:
            saved += 1
        if i % 20 == 0 or i == len(days):
            print(f"  {code} news {i}/{len(days)} new={saved}", flush=True)
        if i < len(days) and sleep_sec > 0:
            import time

            time.sleep(sleep_sec)
    from news.storage import prune_news, refresh_index

    prune_news(code)
    refresh_index(code)
    print(f"{code}: news wrote {saved}", flush=True)
    return saved


def main() -> int:
    parser = argparse.ArgumentParser(description="Build 1-year public heatmap + news JSON")
    parser.add_argument("--market", choices=("jp", "us", "both"), default="both")
    parser.add_argument("--quotes-only", action="store_true")
    parser.add_argument("--news-only", action="store_true")
    parser.add_argument("--sleep", type=float, default=0.35)
    parser.add_argument("--no-llm", action="store_true")
    args = parser.parse_args()
    markets = ["jp", "us"] if args.market == "both" else [args.market]
    for market in markets:
        days = []
        if not args.news_only:
            days = export_quotes(market)
        if args.quotes_only:
            continue
        if not days:
            folder = DATA_DIR / "quotes" / market
            days = sorted(p.stem for p in folder.glob("????-??-??.json")) if folder.is_dir() else []
            cutoff = _cutoff(WEB_QUOTE_DAYS)
            days = [d for d in days if d >= cutoff]
        export_news(market, days, sleep_sec=args.sleep, use_llm=not args.no_llm)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
