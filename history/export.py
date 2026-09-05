"""日足から Web 用スナップショット / latest キャッシュを書き出す。"""

from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from history.storage import (
    REPO_ROOT,
    bars_path,
    load_bars,
    market_code,
    market_dirs,
    scan_index_entries,
    write_index,
)

ROOT = Path(__file__).resolve().parent.parent


def _clean(v: Any) -> float:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(n) or math.isinf(n):
        return 0.0
    return n


def _load_update_quotes():
    scripts = ROOT / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    import update_quotes as uq  # type: ignore

    return uq


def _load_shares(path: Path) -> dict[str, float]:
    out: dict[str, float] = {}
    if not path.is_file():
        return out
    try:
        df = pd.read_parquet(path)
    except Exception:
        return out
    for rec in df.to_dict("records"):
        ticker = str(rec.get("ticker") or "")
        shares = _clean(rec.get("shares"))
        if ticker and shares > 0:
            out[ticker] = shares
    return out


def _meta_map(tickers_path: Path) -> dict[str, dict]:
    if not tickers_path.is_file():
        return {}
    tickers = pd.read_csv(tickers_path, encoding="utf-8-sig")
    return {str(rec.get("ticker")): rec for rec in tickers.to_dict("records")}


def quote_row_from_bars(
    df: pd.DataFrame,
    ticker: str,
    rec: dict,
    shares: float,
    asof: str | None = None,
) -> dict[str, Any] | None:
    if df is None or df.empty or "Close" not in df.columns:
        return None
    work = df.copy()
    work["Date"] = pd.to_datetime(work["Date"], errors="coerce")
    work = work.dropna(subset=["Date", "Close"]).sort_values("Date")
    if work.empty:
        return None
    if asof:
        target = pd.Timestamp(asof)
        work = work[work["Date"] <= target]
        if len(work) < 2:
            return None
        # 指定日そのものに足が無い場合はスキップ（スナップショットの asof を揃える）
        last_day = pd.Timestamp(work["Date"].iloc[-1]).strftime("%Y-%m-%d")
        if last_day != pd.Timestamp(asof).strftime("%Y-%m-%d"):
            return None
    if len(work) < 2:
        return None
    close = pd.to_numeric(work["Close"], errors="coerce").to_numpy()
    vol = (
        pd.to_numeric(work["Volume"], errors="coerce").fillna(0).to_numpy()
        if "Volume" in work.columns
        else np.zeros(len(work))
    )
    dates = pd.to_datetime(work["Date"]).to_numpy()
    last = float(close[-1])
    prev = float(close[-2])
    if prev == 0 or math.isnan(last) or math.isnan(prev):
        return None
    vol_last = int(float(vol[-1]))
    prev_vol = vol[-6:-1] if len(vol) >= 6 else vol[:-1]
    vol_avg = float(prev_vol.mean()) if len(prev_vol) else 0.0
    day = pd.Timestamp(dates[-1]).strftime("%Y-%m-%d")
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
        "shares": shares,
        "market_cap": round(shares * last, 2) if shares else 0.0,
        "asof": day,
    }


def build_quotes_for_day(root: Path, market: str, asof: str | None = None) -> list[dict[str, Any]]:
    dirs = market_dirs(root, market)
    meta = _meta_map(dirs["tickers"])
    shares_map = _load_shares(dirs["shares"])
    folder = dirs["bars"]
    if not folder.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(folder.glob("*.parquet")):
        ticker = path.stem
        df = load_bars(path)
        item = quote_row_from_bars(df, ticker, meta.get(ticker, {}), float(shares_map.get(ticker) or 0.0), asof)
        if item:
            rows.append(item)
    return rows


def export_web_snapshot(root: Path, market: str, asof: str | None = None, force: bool = False) -> Path:
    uq = _load_update_quotes()
    quotes = build_quotes_for_day(root, market, asof=asof)
    if not quotes:
        raise RuntimeError(f"{market}: no quotes for asof={asof or 'latest'}")
    existing = uq.load_existing(market)
    old_n = len(existing.get("quotes") or [])
    if old_n and len(quotes) < max(50, int(old_n * 0.5)) and not force:
        raise RuntimeError(
            f"{market}: refusing to overwrite snapshot ({len(quotes)} quotes vs existing {old_n}). "
            "Fetch more tickers or pass --force-snapshot."
        )
    return uq.write_payload(market, quotes)


def export_web_snapshot_range(
    root: Path,
    market: str,
    start: str,
    end: str,
    *,
    force: bool = False,
    max_days: int = 0,
) -> list[Path]:
    """parquet 日足から期間内の各営業日スナップショットを data/quotes に書き出す。"""
    dirs = market_dirs(root, market)
    folder = dirs["bars"]
    if not folder.is_dir():
        raise RuntimeError(f"{market}: bars missing at {folder}")

    # 銘柄横断の営業日集合
    day_set: set[str] = set()
    files = sorted(folder.glob("*.parquet"))
    for path in files:
        df = load_bars(path)
        if df.empty:
            continue
        days = pd.to_datetime(df["Date"]).dt.strftime("%Y-%m-%d")
        day_set.update(days.tolist())
    days = sorted(d for d in day_set if start <= d <= end)
    if max_days > 0:
        days = days[-max_days:]
    if not days:
        raise RuntimeError(f"{market}: no trading days in {start}..{end}")

    written: list[Path] = []
    quotes_dir = REPO_ROOT / "data" / "quotes" / ("us" if market_code(market) == "us" else "jp")
    for i, day in enumerate(days, 1):
        arch = quotes_dir / f"{day}.json"
        if arch.is_file() and not force:
            print(f"  snapshot {i}/{len(days)}  {day}  skip (取得済み)", flush=True)
            written.append(arch)
            continue
        print(f"  snapshot {i}/{len(days)}  {day}", flush=True)
        try:
            path = export_web_snapshot(root, market, asof=day, force=force)
            written.append(path)
        except RuntimeError as exc:
            print(f"    skip {day}: {exc}", flush=True)
    return written


def rebuild_latest_cache(root: Path, market: str) -> Path:
    quotes = build_quotes_for_day(root, market, asof=None)
    if not quotes:
        raise RuntimeError(f"{market}: no bars to build latest cache")
    dirs = market_dirs(root, market)
    dirs["latest"].parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(quotes)
    tmp = dirs["latest"].with_suffix(".parquet.tmp")
    df.to_parquet(tmp, index=False)
    tmp.replace(dirs["latest"])
    return dirs["latest"]


def refresh_web_index(root: Path, market: str) -> Path:
    entries = scan_index_entries(root, market)
    return write_index(market, entries, root=root)


def export_compact_bars_sample(
    root: Path,
    market: str,
    tickers: list[str],
    out_dir: Path | None = None,
) -> list[Path]:
    """チャート検証用に少数銘柄だけ JSON を書き出す（全銘柄は容量が大きいので非推奨）。"""
    from history.storage import bars_to_web_json

    dirs = market_dirs(root, market)
    meta = _meta_map(dirs["tickers"])
    code = "us" if market == "us" else "jp"
    folder = out_dir or (REPO_ROOT / "data" / "history" / code / "sample")
    folder.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for ticker in tickers:
        path = bars_path(root, market, ticker)
        df = load_bars(path)
        if df.empty:
            continue
        payload = bars_to_web_json(df, ticker, meta.get(ticker, {}))
        out = folder / f"{ticker}.json"
        import json

        tmp = out.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tmp.replace(out)
        written.append(out)
    return written
