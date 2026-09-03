"""日足 parquet / Web 用 index の保存先と読み書き。"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
BAR_COLUMNS = ["Date", "Open", "High", "Low", "Close", "Adj Close", "Volume"]


def default_data_root() -> Path:
    env = os.environ.get("STOCK_DATA_ROOT", "").strip()
    if env:
        return Path(env)
    # Windows 既定。Linux / CI ではリポジトリ配下にフォールバック。
    win = Path(r"C:\data\日本株")
    if win.exists() or os.name == "nt":
        return win
    return REPO_ROOT / "data" / "bars_store"


def market_code(market: str) -> str:
    return "us" if str(market).lower() in {"us", "米株"} else "jp"


def market_dirs(root: Path | None = None, market: str = "jp") -> dict[str, Path]:
    base = root or default_data_root()
    code = market_code(market)
    if code == "jp":
        return {
            "bars": base / "data",
            "tickers": base / "cache" / "tickers.csv",
            "latest": base / "cache" / "latest_quotes.parquet",
            "shares": base / "cache" / "shares.parquet",
            "web_index": REPO_ROOT / "data" / "history" / "jp" / "index.json",
            "web_meta": REPO_ROOT / "data" / "history" / "jp" / "meta.json",
        }
    return {
        "bars": base / "data_us",
        "tickers": base / "cache" / "us_tickers.csv",
        "latest": base / "cache" / "latest_quotes_us.parquet",
        "shares": base / "cache" / "shares_us.parquet",
        "web_index": REPO_ROOT / "data" / "history" / "us" / "index.json",
        "web_meta": REPO_ROOT / "data" / "history" / "us" / "meta.json",
    }


def _safe_stem(ticker: str) -> str:
    text = str(ticker).strip()
    # Windows でもファイル名に使えるよう、Yahoo ティッカーをそのまま stem にする
    # （7203.T / BRK-B など）。不正文字だけ落とす。
    return re.sub(r'[<>:"/\\|?*]', "_", text)


def bars_path(root: Path | None, market: str, ticker: str) -> Path:
    dirs = market_dirs(root, market)
    return dirs["bars"] / f"{_safe_stem(ticker)}.parquet"


def _normalize_bars(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=BAR_COLUMNS)
    work = df.copy()
    if "Date" not in work.columns:
        work = work.reset_index()
        if "index" in work.columns and "Date" not in work.columns:
            work = work.rename(columns={"index": "Date"})
        if "Datetime" in work.columns and "Date" not in work.columns:
            work = work.rename(columns={"Datetime": "Date"})
    colmap = {}
    for c in work.columns:
        low = str(c).strip().lower().replace("_", " ")
        if low == "date":
            colmap[c] = "Date"
        elif low == "open":
            colmap[c] = "Open"
        elif low == "high":
            colmap[c] = "High"
        elif low == "low":
            colmap[c] = "Low"
        elif low == "close":
            colmap[c] = "Close"
        elif low in {"adj close", "adjclose"}:
            colmap[c] = "Adj Close"
        elif low == "volume":
            colmap[c] = "Volume"
    work = work.rename(columns=colmap)
    if "Date" not in work.columns:
        raise ValueError("Date column missing")
    work["Date"] = pd.to_datetime(work["Date"], errors="coerce", utc=False)
    if getattr(work["Date"].dt, "tz", None) is not None:
        work["Date"] = work["Date"].dt.tz_localize(None)
    for col in ("Open", "High", "Low", "Close", "Adj Close", "Volume"):
        if col not in work.columns:
            work[col] = pd.NA if col != "Volume" else 0.0
        work[col] = pd.to_numeric(work[col], errors="coerce")
    work = work.dropna(subset=["Date", "Close"]).sort_values("Date")
    work = work.drop_duplicates(subset=["Date"], keep="last")
    if "Adj Close" in work.columns:
        work["Adj Close"] = work["Adj Close"].fillna(work["Close"])
    else:
        work["Adj Close"] = work["Close"]
    work["Volume"] = work["Volume"].fillna(0)
    return work[BAR_COLUMNS].reset_index(drop=True)


def load_bars(path: Path) -> pd.DataFrame:
    if not path.is_file():
        return pd.DataFrame(columns=BAR_COLUMNS)
    try:
        df = pd.read_parquet(path)
    except Exception:
        return pd.DataFrame(columns=BAR_COLUMNS)
    return _normalize_bars(df)


def write_bars(path: Path, df: pd.DataFrame) -> Path:
    clean = _normalize_bars(df)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".parquet.tmp")
    clean.to_parquet(tmp, index=False)
    tmp.replace(path)
    return path


def merge_bars(existing: pd.DataFrame, incoming: pd.DataFrame) -> pd.DataFrame:
    parts = [x for x in (_normalize_bars(existing), _normalize_bars(incoming)) if not x.empty]
    if not parts:
        return pd.DataFrame(columns=BAR_COLUMNS)
    return _normalize_bars(pd.concat(parts, ignore_index=True))


def last_bar_date(path: Path) -> str | None:
    df = load_bars(path)
    if df.empty:
        return None
    return pd.Timestamp(df["Date"].iloc[-1]).strftime("%Y-%m-%d")


def write_index(market: str, entries: list[dict[str, Any]], root: Path | None = None) -> Path:
    dirs = market_dirs(root, market)
    code = "us" if str(market).lower() in {"us", "米株"} else "jp"
    label = "米株" if code == "us" else "日本株"
    payload = {
        "ok": True,
        "market": label,
        "code": code,
        "updated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "count": len(entries),
        "tickers": entries,
    }
    path = dirs["web_index"]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    meta = {
        "ok": True,
        "market": label,
        "code": code,
        "updated_at": payload["updated_at"],
        "count": len(entries),
        "min_day": "",
        "max_day": "",
        "data_root_env": "STOCK_DATA_ROOT",
        "bars_subdir": "data" if code == "jp" else "data_us",
        "format": {
            "bars": "parquet",
            "columns": BAR_COLUMNS,
            "web_snapshot": "data/quotes/{code}/YYYY-MM-DD.json",
            "latest": "data/{code}.json",
            "index": f"data/history/{code}/index.json",
        },
        "note": (
            "銘柄ごとの全期間日足は parquet（サーバー側。STOCK_DATA_ROOT 配下）。"
            "Web の日付切替ヒートマップは data/quotes の日次 JSON を使う。"
            "個別 OHLCV は GET /api/bars で取得。"
        ),
    }
    # min_day が空文字だけのときの補正
    days_min = [e["min_day"] for e in entries if e.get("min_day")]
    days_max = [e["max_day"] for e in entries if e.get("max_day")]
    meta["min_day"] = min(days_min) if days_min else ""
    meta["max_day"] = max(days_max) if days_max else ""
    meta_path = dirs["web_meta"]
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def read_index(market: str, root: Path | None = None) -> dict[str, Any]:
    path = market_dirs(root, market)["web_index"]
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def scan_index_entries(root: Path | None, market: str) -> list[dict[str, Any]]:
    dirs = market_dirs(root, market)
    folder = dirs["bars"]
    if not folder.is_dir():
        return []
    entries: list[dict[str, Any]] = []
    for path in sorted(folder.glob("*.parquet")):
        df = load_bars(path)
        if df.empty:
            continue
        min_day = pd.Timestamp(df["Date"].iloc[0]).strftime("%Y-%m-%d")
        max_day = pd.Timestamp(df["Date"].iloc[-1]).strftime("%Y-%m-%d")
        entries.append(
            {
                "ticker": path.stem,
                "bars": int(len(df)),
                "min_day": min_day,
                "max_day": max_day,
                "file": path.name,
                "bytes": path.stat().st_size,
            }
        )
    return entries


def _num_or_none(v: Any, ndigits: int = 6) -> float | None:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    if pd.isna(n):
        return None
    return round(n, ndigits)


def bars_to_web_json(df: pd.DataFrame, ticker: str, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    clean = _normalize_bars(df)
    rows: list[dict[str, Any]] = []
    for _, rec in clean.iterrows():
        rows.append(
            {
                "d": pd.Timestamp(rec["Date"]).strftime("%Y-%m-%d"),
                "o": _num_or_none(rec["Open"]),
                "h": _num_or_none(rec["High"]),
                "l": _num_or_none(rec["Low"]),
                "c": _num_or_none(rec["Close"]) or 0.0,
                "a": _num_or_none(rec["Adj Close"]),
                "v": int(float(rec["Volume"] or 0)),
            }
        )
    info = meta or {}
    return {
        "ok": True,
        "ticker": ticker,
        "name": str(info.get("銘柄名") or info.get("name") or ticker),
        "sector": str(
            info.get("33業種区分")
            or info.get("17業種区分")
            or info.get("市場・商品区分")
            or info.get("sector")
            or "その他"
        ),
        "min_day": rows[0]["d"] if rows else "",
        "max_day": rows[-1]["d"] if rows else "",
        "count": len(rows),
        "columns": ["d", "o", "h", "l", "c", "a", "v"],
        "bars": rows,
    }
