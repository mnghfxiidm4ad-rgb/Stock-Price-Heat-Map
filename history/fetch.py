"""Yahoo Finance から全期間（または指定期間）の日足を取得する。"""

from __future__ import annotations

import random
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from history.progress import ProgressTracker
from history.storage import (
    bars_path,
    last_bar_date,
    load_bars,
    merge_bars,
    write_bars,
)

ROOT = Path(__file__).resolve().parent.parent


def _load_update_quotes():
    scripts = ROOT / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    import update_quotes as uq  # type: ignore

    return uq


def load_universe(market: str) -> pd.DataFrame:
    uq = _load_update_quotes()
    session = uq._session()
    if market == "jp":
        return uq.load_jp_universe(session)
    return uq.load_us_universe(session)


def _extract_symbol_frame(raw: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame()
    if isinstance(raw.columns, pd.MultiIndex):
        try:
            df = raw[symbol].dropna(how="all")
        except Exception:
            return pd.DataFrame()
        return df
    if len(raw.columns):
        return raw.dropna(how="all")
    return pd.DataFrame()


def download_ohlcv(
    symbols: list[str],
    *,
    period: str | None = "max",
    start: str | None = None,
    end: str | None = None,
    retries: int = 5,
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
        "timeout": 60,
    }
    if start:
        kwargs["start"] = start
        if end:
            kwargs["end"] = end
    else:
        kwargs["period"] = period or "max"

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


def incremental_start(path: Path, overlap_days: int = 7) -> str | None:
    last = last_bar_date(path)
    if not last:
        return None
    day = datetime.strptime(last, "%Y-%m-%d").date() - timedelta(days=overlap_days)
    return day.isoformat()


def fetch_and_store_batch(
    symbols: list[str],
    *,
    market: str,
    root: Path,
    period: str = "max",
    start: str | None = None,
    end: str | None = None,
    incremental: bool = False,
) -> dict[str, int]:
    """バッチ取得して parquet に保存。ticker -> 保存本数。"""
    saved: dict[str, int] = {}
    if not symbols:
        return saved

    batch_start = start
    batch_period = period
    if incremental and not start:
        starts = []
        missing = False
        for sym in symbols:
            path = bars_path(root, market, sym)
            s = incremental_start(path)
            if s is None:
                missing = True
                break
            starts.append(s)
        if missing:
            batch_start = None
            batch_period = period
        else:
            batch_start = min(starts)
            batch_period = None

    raw = download_ohlcv(
        symbols,
        period=batch_period,
        start=batch_start,
        end=end,
    )
    for sym in symbols:
        part = _extract_symbol_frame(raw, sym)
        if part is None or part.empty:
            continue
        path = bars_path(root, market, sym)
        if incremental and path.is_file():
            merged = merge_bars(load_bars(path), part)
        else:
            merged = part
        write_bars(path, merged)
        saved[sym] = int(len(load_bars(path)))
    return saved


def _fresh_enough(path: Path, *, end: str | None, incremental: bool) -> bool:
    """増分取得で、既に目標日まで揃っている銘柄はスキップする。"""
    if not incremental:
        return False
    last = last_bar_date(path)
    if not last:
        return False
    target = end or datetime.utcnow().strftime("%Y-%m-%d")
    return last >= target


def fetch_market_history(
    market: str,
    *,
    root: Path,
    period: str = "max",
    start: str | None = None,
    end: str | None = None,
    incremental: bool = True,
    batch_size: int = 20,
    sleep_sec: float = 1.5,
    limit: int | None = None,
    tickers: list[str] | None = None,
) -> dict[str, Any]:
    universe = load_universe(market)
    if tickers:
        want = {t.strip() for t in tickers if t.strip()}
        universe = universe[universe["ticker"].astype(str).isin(want)].copy()
    symbols = [str(t) for t in universe["ticker"].tolist()]
    if limit is not None and limit > 0:
        symbols = symbols[:limit]

    dirs_tickers = root / "cache"
    dirs_tickers.mkdir(parents=True, exist_ok=True)
    out_csv = dirs_tickers / ("tickers.csv" if market == "jp" else "us_tickers.csv")
    universe.to_csv(out_csv, index=False, encoding="utf-8-sig")

    tracker = ProgressTracker(market, len(symbols), phase="fetch")
    tracker.log(f"{market}: {len(symbols):,} 銘柄  root={root}  incremental={incremental}")

    ok = 0
    fail = 0
    skipped = 0
    processed = 0
    pending: list[str] = []

    def flush_pending() -> None:
        nonlocal ok, fail, processed, pending
        if not pending:
            return
        chunk = pending
        pending = []
        tracker.update(
            current=f"{chunk[0]}..{chunk[-1]}",
            message=f"Yahoo取得 {len(chunk)} 銘柄",
        )
        try:
            saved = fetch_and_store_batch(
                chunk,
                market=market,
                root=root,
                period=period,
                start=start,
                end=end,
                incremental=incremental,
            )
        except Exception as exc:  # noqa: BLE001
            tracker.log(f"  batch error: {exc}")
            saved = {}
        got = len(saved)
        miss = len(chunk) - got
        ok += got
        fail += miss
        processed += len(chunk)
        tracker.update(
            done=processed + skipped,
            saved=ok,
            skipped=skipped,
            failed=fail,
            current=chunk[-1],
            message=f"保存 {got}/{len(chunk)}",
        )
        time.sleep(sleep_sec)

    for sym in symbols:
        path = bars_path(root, market, sym)
        if _fresh_enough(path, end=end, incremental=incremental):
            skipped += 1
            processed += 0
            tracker.update(
                done=processed + skipped,
                saved=ok,
                skipped=skipped,
                failed=fail,
                current=sym,
                message=f"スキップ（取得済み {last_bar_date(path)}）",
            )
            continue
        pending.append(sym)
        if len(pending) >= batch_size:
            flush_pending()

    flush_pending()
    tracker.finish(
        f"{market}: 保存 {ok} / スキップ {skipped} / 失敗 {fail} / 対象 {len(symbols)}"
    )
    return {
        "market": market,
        "requested": len(symbols),
        "saved": ok,
        "skipped": skipped,
        "missing": fail,
    }
