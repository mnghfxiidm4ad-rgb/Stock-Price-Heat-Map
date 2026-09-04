"""GitHub Actions / ローカル用。引け後の終値スナップショットを JSON にする。"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
LISTS_DIR = ROOT / "lists"
CACHE_DIR = ROOT / ".cache"
LOCAL_ROOT = Path(os.environ.get("STOCK_DATA_ROOT", r"C:\data\日本株"))

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

JPX_LIST_PAGE = "https://www.jpx.co.jp/markets/statistics-equities/misc/01.html"
JPX_FILE_CANDIDATES = [
    "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls",
    "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xlsx",
]
NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"

EQUITY_MARKETS = (
    "プライム（内国株式）",
    "プライム（外国株式）",
    "スタンダード（内国株式）",
    "スタンダード（外国株式）",
    "グロース（内国株式）",
    "グロース（外国株式）",
)
SKIP_MARKETS_ALWAYS = ("PRO Market", "出資証券")
US_SKIP_NAME = re.compile(
    r"(Warrant|Warrants|Right|Rights|\bUnit\b|\bUnits\b|Preferred Share|ETF|ETN|ETV|NextShares)",
    re.I,
)
US_EXCHANGE_NAMES = {
    "A": "NYSE American",
    "N": "NYSE",
    "P": "NYSE Arca",
    "Z": "Cboe BZX",
    "V": "IEX",
    "Q": "NASDAQ",
    "G": "NASDAQ",
    "S": "NASDAQ",
}


def _clean(v: Any) -> float:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(n) or math.isinf(n):
        return 0.0
    return n


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def json_path(market: str) -> Path:
    return DATA_DIR / ("jp.json" if market == "jp" else "us.json")


def load_existing(market: str) -> dict[str, Any]:
    path = json_path(market)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def write_quotes_index(market: str) -> Path:
    folder = DATA_DIR / "quotes" / market
    folder.mkdir(parents=True, exist_ok=True)
    days = sorted(p.stem for p in folder.glob("????-??-??.json"))
    label = "日本株" if market == "jp" else "米株"
    payload = {
        "ok": True,
        "market": label,
        "code": market,
        "days": days,
        "count": len(days),
        "min_day": days[0] if days else "",
        "max_day": days[-1] if days else "",
        "updated_at": _now_iso(),
    }
    path = folder / "index.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def write_payload(market: str, quotes: list[dict], extra: dict[str, Any] | None = None) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    label = "日本株" if market == "jp" else "米株"
    asof = ""
    if quotes:
        asof = str(quotes[0].get("asof") or "")
        dates = [str(r.get("asof") or "") for r in quotes if r.get("asof")]
        if dates:
            asof = max(dates)
    sectors = sorted({str(r.get("sector") or "その他") for r in quotes})
    archive_dir = DATA_DIR / "quotes" / market
    archive_days = sorted(p.stem for p in archive_dir.glob("????-??-??.json")) if archive_dir.is_dir() else []
    if asof and asof not in archive_days:
        archive_days = sorted(set(archive_days) | {asof})
    history_ready = len(archive_days) > 0
    payload = {
        "ok": True,
        "market": label,
        "asof": asof,
        "updated_at": _now_iso(),
        "history_ready": history_ready,
        "quotes": quotes,
        "sectors": sectors,
        "message": "",
        "status": {
            "max_day": archive_days[-1] if archive_days else asof,
            "min_day": archive_days[0] if archive_days else asof,
            "day_count": len(archive_days),
            "latest_count": len(quotes),
            "history_ready": history_ready,
        },
    }
    if extra:
        payload.update(extra)
    path = json_path(market)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)
    if asof:
        arch = DATA_DIR / "quotes" / market / f"{asof}.json"
        arch.parent.mkdir(parents=True, exist_ok=True)
        arch.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    index_path = write_quotes_index(market)
    print(f"saved {path}  {len(quotes):,} quotes  asof={asof}  days={payload['status']['day_count']}  index={index_path}", flush=True)
    return path


def _normalize_code(value: Any) -> str:
    if pd.isna(value):
        return ""
    text = str(value).strip()
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    if re.fullmatch(r"\d+", text):
        return text.zfill(4)
    return text.upper()


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update(HTTP_HEADERS)
    return session


def load_jp_universe(session: requests.Session) -> pd.DataFrame:
    fallback = LISTS_DIR / "tickers_jp.csv"
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        html = session.get(JPX_LIST_PAGE, timeout=60)
        html.raise_for_status()
        hrefs = re.findall(r'href=["\']([^"\']+\.(?:xls|xlsx))["\']', html.text, flags=re.I)
        urls = list(JPX_FILE_CANDIDATES)
        for href in hrefs:
            if "data_j" in href.lower():
                urls.insert(0, urljoin(JPX_LIST_PAGE, href))
        excel_path = None
        for url in urls:
            try:
                resp = session.get(url, timeout=60)
                resp.raise_for_status()
            except requests.RequestException:
                continue
            if resp.content[:8] in (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",) or resp.content[:2] == b"PK":
                suffix = ".xlsx" if resp.content[:2] == b"PK" else ".xls"
                excel_path = CACHE_DIR / f"data_j{suffix}"
                excel_path.write_bytes(resp.content)
                break
        if excel_path is None:
            raise RuntimeError("JPX list download failed")
        engine = "openpyxl" if excel_path.suffix == ".xlsx" else "xlrd"
        df = pd.read_excel(excel_path, engine=engine)
        df.columns = [str(c).strip() for c in df.columns]
        df["コード"] = df["コード"].map(_normalize_code)
        df = df[df["コード"].str.len() >= 4].copy()
        df["ticker"] = df["コード"] + ".T"
        if "市場・商品区分" in df.columns:
            df = df[~df["市場・商品区分"].isin(SKIP_MARKETS_ALWAYS)].copy()
            df = df[df["市場・商品区分"].isin(EQUITY_MARKETS)].copy()
        keep = ["コード", "ticker"]
        for col in ("銘柄名", "市場・商品区分", "33業種区分", "17業種区分"):
            if col in df.columns:
                keep.append(col)
        out = df[keep].drop_duplicates(subset=["ticker"]).reset_index(drop=True)
        LISTS_DIR.mkdir(parents=True, exist_ok=True)
        out.to_csv(fallback, index=False, encoding="utf-8-sig")
        return out
    except Exception as exc:
        print(f"JPX list failed ({exc}); using {fallback}", flush=True)
        if not fallback.exists():
            raise
        return pd.read_csv(fallback, encoding="utf-8-sig")


def _yahoo_us_symbol(symbol: str) -> str:
    text = str(symbol).strip().upper()
    if not text or not re.fullmatch(r"[A-Z0-9.\-]+", text):
        return ""
    return text.replace(".", "-")


def _short_us_name(security_name: str) -> str:
    name = str(security_name or "").strip().split(" - ")[0]
    name = re.sub(r"\s+(Common Stock|Ordinary Shares|Class [A-Z]\b.*)$", "", name, flags=re.I)
    return name.strip()[:48] or "Unknown"


def load_us_universe(session: requests.Session) -> pd.DataFrame:
    fallback = LISTS_DIR / "tickers_us.csv"
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        rows: list[dict[str, str]] = []
        for url, name in ((NASDAQ_LISTED_URL, "nasdaqlisted.txt"), (OTHER_LISTED_URL, "otherlisted.txt")):
            resp = session.get(url, timeout=60)
            resp.raise_for_status()
            path = CACHE_DIR / name
            path.write_bytes(resp.content)
        nasdaq = pd.read_csv(CACHE_DIR / "nasdaqlisted.txt", sep="|")
        other = pd.read_csv(CACHE_DIR / "otherlisted.txt", sep="|")
        if "Symbol" in nasdaq.columns:
            for rec in nasdaq.to_dict("records"):
                symbol = str(rec.get("Symbol") or "")
                if str(rec.get("Test Issue") or "N").upper() == "Y":
                    continue
                name = str(rec.get("Security Name") or symbol)
                if str(rec.get("ETF") or "N").upper() == "Y" or US_SKIP_NAME.search(name):
                    continue
                ticker = _yahoo_us_symbol(symbol)
                if not ticker:
                    continue
                cat = str(rec.get("Market Category") or "Q")
                exch = US_EXCHANGE_NAMES.get(cat, "NASDAQ")
                rows.append(
                    {
                        "コード": symbol,
                        "ticker": ticker,
                        "銘柄名": _short_us_name(name),
                        "市場・商品区分": exch,
                        "33業種区分": exch,
                    }
                )
        act_col = "ACT Symbol" if "ACT Symbol" in other.columns else None
        if act_col:
            for rec in other.to_dict("records"):
                symbol = str(rec.get(act_col) or rec.get("NASDAQ Symbol") or "")
                if str(rec.get("Test Issue") or "N").upper() == "Y":
                    continue
                name = str(rec.get("Security Name") or symbol)
                if str(rec.get("ETF") or "N").upper() == "Y" or US_SKIP_NAME.search(name):
                    continue
                ticker = _yahoo_us_symbol(symbol)
                if not ticker:
                    continue
                exch = US_EXCHANGE_NAMES.get(str(rec.get("Exchange") or "N"), "US")
                rows.append(
                    {
                        "コード": symbol,
                        "ticker": ticker,
                        "銘柄名": _short_us_name(name),
                        "市場・商品区分": exch,
                        "33業種区分": exch,
                    }
                )
        out = pd.DataFrame(rows).drop_duplicates(subset=["ticker"]).reset_index(drop=True)
        if out.empty:
            raise RuntimeError("US list empty")
        LISTS_DIR.mkdir(parents=True, exist_ok=True)
        out.to_csv(fallback, index=False, encoding="utf-8-sig")
        return out
    except Exception as exc:
        print(f"US list failed ({exc}); using {fallback}", flush=True)
        if not fallback.exists():
            raise
        return pd.read_csv(fallback, encoding="utf-8-sig")


def quote_from_ohlcv(df: pd.DataFrame, ticker: str, rec: dict) -> dict[str, Any] | None:
    if df is None or df.empty or "Close" not in df.columns:
        return None
    work = df.copy()
    if "Date" in work.columns:
        work["Date"] = pd.to_datetime(work["Date"], errors="coerce")
        work = work.dropna(subset=["Date"]).sort_values("Date")
        date_src = work["Date"]
    else:
        work.index = pd.to_datetime(work.index, errors="coerce")
        work = work[work.index.notna()].sort_index()
        date_src = pd.Series(work.index, index=work.index)
    close = pd.to_numeric(work["Close"], errors="coerce")
    vol = (
        pd.to_numeric(work["Volume"], errors="coerce").fillna(0)
        if "Volume" in work.columns
        else pd.Series(0.0, index=work.index)
    )
    mask = close.notna().to_numpy()
    if int(mask.sum()) < 2:
        return None
    close_v = close.to_numpy()[mask]
    vol_v = vol.to_numpy()[mask]
    date_v = pd.to_datetime(date_src).to_numpy()[mask]
    last = float(close_v[-1])
    prev = float(close_v[-2])
    if prev == 0:
        return None
    vol_last = int(float(vol_v[-1]))
    prev_vol = vol_v[-6:-1] if len(vol_v) >= 6 else vol_v[:-1]
    vol_avg = float(prev_vol.mean()) if len(prev_vol) else 0.0
    asof = pd.Timestamp(date_v[-1]).strftime("%Y-%m-%d")
    name = str(rec.get("銘柄名") or rec.get("name") or ticker)
    sector = str(
        rec.get("33業種区分")
        or rec.get("17業種区分")
        or rec.get("市場・商品区分")
        or rec.get("sector")
        or "その他"
    )
    return {
        "ticker": ticker,
        "name": name,
        "sector": sector,
        "price": round(last, 4),
        "prev": round(prev, 4),
        "change_pct": round((last - prev) / prev * 100.0, 4),
        "volume": vol_last,
        "vol_avg": int(vol_avg),
        "vol_ratio": round((vol_last / vol_avg) if vol_avg else 1.0, 4),
        "turnover": round(last * vol_last, 2),
        "shares": 0.0,
        "market_cap": 0.0,
        "asof": asof,
    }


def download_batch(symbols: list[str], retries: int = 5) -> pd.DataFrame:
    import yfinance as yf

    kwargs = {
        "tickers": symbols,
        "period": "10d",
        "interval": "1d",
        "group_by": "ticker",
        "auto_adjust": False,
        "actions": False,
        "threads": False,
        "progress": False,
        "timeout": 30,
    }
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            raw = yf.download(**kwargs)
            if raw is None:
                return pd.DataFrame()
            return raw
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            wait = min(120.0, 6.0 * (2 ** (attempt - 1))) + random.uniform(0, 2)
            print(f"  retry {attempt}/{retries} after {wait:.0f}s: {exc}", flush=True)
            time.sleep(wait)
    print(f"  batch failed: {last_exc}", flush=True)
    return pd.DataFrame()


def quotes_from_raw(raw: pd.DataFrame, symbols: list[str], meta: dict[str, dict]) -> list[dict]:
    rows: list[dict] = []
    if raw is None or raw.empty:
        return rows
    if isinstance(raw.columns, pd.MultiIndex):
        for sym in symbols:
            try:
                df = raw[sym].dropna(how="all")
            except Exception:
                continue
            try:
                item = quote_from_ohlcv(df, sym, meta.get(sym, {}))
            except Exception:
                continue
            if item:
                rows.append(item)
        return rows
    if len(symbols) == 1:
        item = quote_from_ohlcv(raw, symbols[0], meta.get(symbols[0], {}))
        if item:
            rows.append(item)
    return rows


def merge_quotes(old_rows: list[dict], new_rows: list[dict]) -> list[dict]:
    by_ticker = {str(r.get("ticker")): r for r in old_rows if r.get("ticker")}
    for row in new_rows:
        prev = by_ticker.get(row["ticker"], {})
        if not row.get("market_cap") and prev.get("market_cap"):
            row["market_cap"] = prev["market_cap"]
            row["shares"] = prev.get("shares") or 0.0
        by_ticker[row["ticker"]] = row
    return list(by_ticker.values())


def fetch_market(market: str, batch_size: int, sleep_sec: float) -> list[dict]:
    session = _session()
    universe = load_jp_universe(session) if market == "jp" else load_us_universe(session)
    tickers = [str(t) for t in universe["ticker"].tolist()]
    meta = {str(rec.get("ticker")): rec for rec in universe.to_dict("records")}
    print(f"{market}: {len(tickers):,} tickers", flush=True)
    existing = load_existing(market)
    old_rows = list(existing.get("quotes") or [])
    new_rows: list[dict] = []
    total = (len(tickers) + batch_size - 1) // batch_size
    for i in range(0, len(tickers), batch_size):
        chunk = tickers[i : i + batch_size]
        batch_i = i // batch_size + 1
        print(f"  batch {batch_i}/{total}  {chunk[0]} .. {chunk[-1]}", flush=True)
        try:
            raw = download_batch(chunk)
            got = quotes_from_raw(raw, chunk, meta)
        except Exception as exc:  # noqa: BLE001
            print(f"    batch error: {exc}", flush=True)
            got = []
        new_rows.extend(got)
        print(f"    got {len(got)}/{len(chunk)}", flush=True)
        if i + batch_size < len(tickers):
            time.sleep(sleep_sec)
    merged = merge_quotes(old_rows, new_rows)
    print(f"{market}: new {len(new_rows):,}  merged {len(merged):,}", flush=True)
    if len(new_rows) < max(50, int(len(tickers) * 0.2)):
        raise RuntimeError(f"{market}: too few quotes ({len(new_rows)}). Yahoo may have blocked the run.")
    return merged


def export_from_local(market: str) -> list[dict]:
    sys.path.insert(0, str(ROOT))
    from data import load_latest_quotes

    label = "日本株" if market == "jp" else "米株"
    rows = load_latest_quotes(label)
    if not rows:
        raise RuntimeError(f"local quotes missing for {label}")
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Update heatmap JSON after the cash session close")
    parser.add_argument("--market", choices=("jp", "us", "both"), default="both")
    parser.add_argument("--from-local", action="store_true", help="Export C:\\data\\日本株 parquet instead of Yahoo")
    parser.add_argument("--batch-size", type=int, default=40)
    parser.add_argument("--sleep", type=float, default=2.0)
    args = parser.parse_args()
    markets = ["jp", "us"] if args.market == "both" else [args.market]
    for market in markets:
        if args.from_local:
            quotes = export_from_local(market)
        else:
            quotes = fetch_market(market, args.batch_size, args.sleep)
        write_payload(market, quotes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
