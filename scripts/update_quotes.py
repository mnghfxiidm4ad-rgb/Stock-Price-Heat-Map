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
from datetime import datetime, timedelta, timezone
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


QUOTE_COLS = ("ticker", "name", "sector", "price", "change_pct", "volume", "vol_ratio", "turnover")
WEB_QUOTE_DAYS = 370


def compact_quotes(quotes: list[dict]) -> list[list]:
    rows: list[list] = []
    for rec in quotes:
        rows.append(
            [
                str(rec.get("ticker") or ""),
                str(rec.get("name") or rec.get("ticker") or ""),
                str(rec.get("sector") or "その他"),
                round(_clean(rec.get("price")), 4),
                round(_clean(rec.get("change_pct")), 4),
                int(_clean(rec.get("volume"))),
                round(_clean(rec.get("vol_ratio")) or 1.0, 4),
                round(_clean(rec.get("turnover")), 2),
            ]
        )
    return rows


def compact_day_payload(market_label: str, asof: str, quotes: list[dict], sectors: list[str] | None = None) -> dict[str, Any]:
    if sectors is None:
        sectors = sorted({str(r.get("sector") or "その他") for r in quotes})
    return {
        "ok": True,
        "asof": asof,
        "market": market_label,
        "cols": list(QUOTE_COLS),
        "quotes": compact_quotes(quotes),
        "sectors": sectors,
    }


def expand_day_payload(payload: dict[str, Any]) -> dict[str, Any]:
    quotes = payload.get("quotes")
    cols = payload.get("cols")
    if not isinstance(quotes, list) or not quotes:
        return payload
    if isinstance(quotes[0], dict):
        return payload
    names = [str(c) for c in cols] if isinstance(cols, list) and cols else list(QUOTE_COLS)
    asof = str(payload.get("asof") or "")
    out: list[dict] = []
    for raw in quotes:
        if not isinstance(raw, list):
            continue
        rec = {names[i]: raw[i] for i in range(min(len(names), len(raw)))}
        rec.setdefault("asof", asof)
        rec.setdefault("prev", 0.0)
        rec.setdefault("vol_avg", 0)
        rec.setdefault("shares", 0.0)
        rec.setdefault("market_cap", 0.0)
        out.append(rec)
    payload = dict(payload)
    payload["quotes"] = out
    return payload


def prune_quote_archives(market: str, keep_days: int = WEB_QUOTE_DAYS) -> None:
    folder = DATA_DIR / "quotes" / market
    if not folder.is_dir():
        return
    files = sorted(folder.glob("????-??-??.json"))
    if len(files) <= keep_days:
        return
    cutoff = (datetime.now() - timedelta(days=keep_days)).strftime("%Y-%m-%d")
    for path in files:
        if path.stem < cutoff:
            path.unlink(missing_ok=True)


def write_quote_index(market: str) -> list[str]:
    folder = DATA_DIR / "quotes" / market
    folder.mkdir(parents=True, exist_ok=True)
    days = sorted(p.stem for p in folder.glob("????-??-??.json"))
    (folder / "index.json").write_text(
        json.dumps({"market": market.upper(), "days": days, "count": len(days)}, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    return days


def load_existing(market: str) -> dict[str, Any]:
    path = json_path(market)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


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
    payload = {
        "ok": True,
        "market": label,
        "asof": asof,
        "updated_at": _now_iso(),
        "history_ready": False,
        "quotes": quotes,
        "sectors": sectors,
        "message": "",
        "status": {
            "max_day": asof,
            "latest_count": len(quotes),
            "history_ready": False,
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
        compact = compact_day_payload(label, asof, quotes, sectors)
        arch.write_text(json.dumps(compact, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        prune_quote_archives(market)
        days = write_quote_index(market)
        payload["days"] = days
        payload["history_ready"] = True
        payload["status"]["history_ready"] = True
        path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"saved {path}  {len(quotes):,} quotes  asof={asof}", flush=True)
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


def download_batch(
    symbols: list[str],
    retries: int = 5,
    period: str = "10d",
    start: str | None = None,
    end: str | None = None,
) -> pd.DataFrame:
    import yfinance as yf

    kwargs: dict[str, Any] = {
        "tickers": symbols,
        "interval": "1d",
        "group_by": "ticker",
        "auto_adjust": False,
        "actions": False,
        "threads": False,
        "progress": False,
        "timeout": 30,
    }
    if start:
        kwargs["start"] = start
        if end:
            kwargs["end"] = end
    else:
        kwargs["period"] = period
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


def _frame_for_symbol(raw: pd.DataFrame, symbol: str, symbols: list[str]) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame()
    if isinstance(raw.columns, pd.MultiIndex):
        try:
            return raw[symbol].dropna(how="all")
        except Exception:
            return pd.DataFrame()
    if len(symbols) == 1 and symbols[0] == symbol:
        return raw.dropna(how="all")
    return pd.DataFrame()


def quotes_from_raw(raw: pd.DataFrame, symbols: list[str], meta: dict[str, dict]) -> list[dict]:
    rows: list[dict] = []
    for sym in symbols:
        df = _frame_for_symbol(raw, sym, symbols)
        if df.empty:
            continue
        try:
            item = quote_from_ohlcv(df, sym, meta.get(sym, {}))
        except Exception:
            continue
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


def history_root() -> Path:
    return ROOT / "history"


def _safe_ticker_name(ticker: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]', "_", ticker).strip()
    return cleaned or "ticker"


def bars_to_parquet(df: pd.DataFrame, path: Path) -> dict[str, Any] | None:
    if df is None or df.empty or "Close" not in df.columns:
        return None
    work = df.copy()
    if "Date" in work.columns:
        idx = pd.to_datetime(work["Date"], errors="coerce", utc=True)
        work = work.loc[idx.notna()].copy()
        idx = idx[idx.notna()]
    else:
        idx = pd.to_datetime(work.index, errors="coerce", utc=True)
        work = work.loc[idx.notna()].copy()
        idx = idx[idx.notna()]
    close = pd.to_numeric(work["Close"], errors="coerce")
    vol = (
        pd.to_numeric(work["Volume"], errors="coerce").fillna(0)
        if "Volume" in work.columns
        else pd.Series(0.0, index=work.index)
    )
    mask = close.notna().to_numpy()
    if int(mask.sum()) < 2:
        return None
    dates = pd.DatetimeIndex(idx).tz_convert(None).strftime("%Y-%m-%d").to_numpy()
    out = pd.DataFrame(
        {
            "Date": dates[mask],
            "Close": close.to_numpy()[mask],
            "Volume": vol.to_numpy()[mask],
        }
    ).sort_values("Date")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".parquet.tmp")
    out.to_parquet(tmp, index=False)
    tmp.replace(path)
    return {
        "min": str(out["Date"].iloc[0]),
        "max": str(out["Date"].iloc[-1]),
        "rows": int(len(out)),
    }


def _index_path(market: str) -> Path:
    return history_root() / "cache" / f"history_index_{market}.json"


def _load_history_index(market: str) -> dict[str, Any]:
    path = _index_path(market)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_history_index(market: str, index: dict[str, Any]) -> None:
    path = _index_path(market)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _coverage_rank(period: str, start: str | None, min_day: str = "", max_day: str = "") -> int:
    if start:
        try:
            years = (datetime.now() - datetime.strptime(start[:10], "%Y-%m-%d")).days / 365.25
        except ValueError:
            years = 0
        if years >= 12:
            return 3
        if years >= 4.5:
            return 2
        return 1
    key = (period or "").lower()
    if key in {"max", "max."}:
        return 3
    if key in {"5y", "10y"}:
        return 2
    if key in {"1y", "6mo", "3mo", "1mo"}:
        return 1
    if min_day and max_day:
        try:
            span = (
                datetime.strptime(max_day[:10], "%Y-%m-%d") - datetime.strptime(min_day[:10], "%Y-%m-%d")
            ).days / 365.25
        except ValueError:
            span = 0
        if span >= 12:
            return 3
        if span >= 4.5:
            return 2
        if span >= 0.7:
            return 1
    return 0


def _parquet_span(path: Path) -> tuple[str, str, int] | None:
    if not path.is_file():
        return None
    try:
        df = pd.read_parquet(path, columns=["Date"])
    except Exception:
        return None
    if df.empty:
        return None
    days = pd.to_datetime(df["Date"], errors="coerce").dropna()
    if len(days) < 2:
        return None
    return days.min().strftime("%Y-%m-%d"), days.max().strftime("%Y-%m-%d"), int(len(days))


def _should_skip_ticker(
    ticker: str,
    path: Path,
    rec: dict[str, Any] | None,
    req_rank: int,
    req_start: str | None,
) -> bool:
    if not path.is_file():
        return False
    info = rec if isinstance(rec, dict) else {}
    stored_period = str(info.get("period") or "")
    stored_start = str(info.get("start") or "") or None
    min_day = str(info.get("min") or "")
    max_day = str(info.get("max") or "")
    if not min_day or not max_day:
        span = _parquet_span(path)
        if not span:
            return False
        min_day, max_day, _rows = span
    have_rank = _coverage_rank(stored_period, stored_start, min_day, max_day)
    if have_rank < req_rank:
        return False
    if req_start:
        if stored_period.lower() == "max":
            return True
        return bool(min_day and min_day <= req_start)
    return True


def fetch_history(
    market: str,
    batch_size: int,
    sleep_sec: float,
    period: str,
    start: str | None,
    end: str | None,
    skip_existing: bool = True,
) -> int:
    session = _session()
    universe = load_jp_universe(session) if market == "jp" else load_us_universe(session)
    tickers = [str(t) for t in universe["ticker"].tolist()]
    dest = history_root() / ("data" if market == "jp" else "data_us")
    cache = history_root() / "cache"
    dest.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    csv_name = "tickers.csv" if market == "jp" else "us_tickers.csv"
    universe.to_csv(cache / csv_name, index=False, encoding="utf-8-sig")
    index = _load_history_index(market)
    req_rank = _coverage_rank(period, start)
    range_txt = f"{start} .. {end}" if start else period
    existing_files = list(dest.glob("*.parquet"))
    print(f"{market}: history {len(tickers):,} tickers  {range_txt} -> {dest}", flush=True)
    print(f"{market}: already on disk {len(existing_files):,} parquet files", flush=True)
    if skip_existing:
        print(f"{market}: skip tickers already fetched at this range or longer", flush=True)
    saved = 0
    skipped = 0
    need_count = 0
    total = (len(tickers) + batch_size - 1) // batch_size
    for i in range(0, len(tickers), batch_size):
        chunk = tickers[i : i + batch_size]
        batch_i = i // batch_size + 1
        pending: list[str] = []
        batch_skip = 0
        for sym in chunk:
            path = dest / f"{_safe_ticker_name(sym)}.parquet"
            rec = index.get(sym)
            if skip_existing and _should_skip_ticker(sym, path, rec if isinstance(rec, dict) else None, req_rank, start):
                batch_skip += 1
                continue
            pending.append(sym)
        skipped += batch_skip
        need_count += len(pending)
        if not pending:
            print(f"  batch {batch_i}/{total}  skip {batch_skip}/{len(chunk)} (already fetched)", flush=True)
            continue
        print(
            f"  batch {batch_i}/{total}  {pending[0]} .. {pending[-1]}  fetch {len(pending)} skip {batch_skip}",
            flush=True,
        )
        try:
            raw = download_batch(pending, period=period, start=start, end=end)
        except Exception as exc:  # noqa: BLE001
            print(f"    batch error: {exc}", flush=True)
            raw = pd.DataFrame()
        got = 0
        for sym in pending:
            df = _frame_for_symbol(raw, sym, pending)
            path = dest / f"{_safe_ticker_name(sym)}.parquet"
            meta = bars_to_parquet(df, path)
            if not meta:
                continue
            got += 1
            rec = {
                "period": "max" if (period or "").lower() == "max" else (period or ""),
                "start": start or "",
                "min": meta["min"],
                "max": meta["max"],
                "rows": meta["rows"],
                "updated_at": _now_iso(),
            }
            index[sym] = rec
        saved += got
        _save_history_index(market, index)
        print(f"    saved {got}/{len(pending)}  total saved {saved:,}  skipped {skipped:,}", flush=True)
        if i + batch_size < len(tickers):
            time.sleep(sleep_sec)
    _save_history_index(market, index)
    print(f"{market}: wrote {saved:,} parquet files  skipped {skipped:,}", flush=True)
    if saved == 0 and skipped:
        print(
            f"{market}: 新規取得なし。同じ期間（またはより長い期間）は既に history にあります。"
            " カレンダーに出すには start.bat を開き直してください。",
            flush=True,
        )
    if need_count and saved < max(50, int(need_count * 0.2)):
        raise RuntimeError(f"{market}: too few history files ({saved}/{need_count}). Yahoo may have blocked the run.")
    return saved


def main() -> int:
    parser = argparse.ArgumentParser(description="Update heatmap JSON after the cash session close")
    parser.add_argument("--market", choices=("jp", "us", "both"), default="both")
    parser.add_argument("--from-local", action="store_true", help="Export C:\\data\\日本株 parquet instead of Yahoo")
    parser.add_argument("--history", action="store_true", help="Download daily bars into history/ for the calendar")
    parser.add_argument("--period", default="1y", help="Yahoo period for --history (default 1y)")
    parser.add_argument("--start", default="", help="History start date YYYY-MM-DD")
    parser.add_argument("--end", default="", help="History end date YYYY-MM-DD")
    parser.add_argument("--force", action="store_true", help="Re-download even if history already exists")
    parser.add_argument("--batch-size", type=int, default=40)
    parser.add_argument("--sleep", type=float, default=2.0)
    args = parser.parse_args()
    markets = ["jp", "us"] if args.market == "both" else [args.market]
    start = args.start.strip() or None
    end = args.end.strip() or None
    for market in markets:
        if args.history:
            fetch_history(
                market,
                args.batch_size,
                args.sleep,
                args.period,
                start,
                end,
                skip_existing=not args.force,
            )
            continue
        if args.from_local:
            quotes = export_from_local(market)
        else:
            quotes = fetch_market(market, args.batch_size, args.sleep)
        write_payload(market, quotes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
