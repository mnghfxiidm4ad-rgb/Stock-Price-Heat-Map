"""C:\\data\\日本株 の日足から、ヒートマップ用の最新値・履歴を読む。"""

from __future__ import annotations

import json
import math
import os
import threading
from bisect import bisect_right
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent


def _resolve_data_root() -> Path:
    env = os.environ.get("STOCK_DATA_ROOT")
    if env:
        return Path(env)
    legacy = Path(r"C:\data\日本株")
    local = REPO_ROOT / "history"

    def has_bars(root: Path) -> bool:
        for folder in ("data", "data_us"):
            if next((root / folder).glob("*.parquet"), None):
                return True
        return (root / "cache" / "latest_quotes.parquet").exists()

    if has_bars(legacy):
        return legacy
    return local


DATA_ROOT = _resolve_data_root()

ProgressFn = Callable[[int, int], None]


def _clean(v: Any) -> float:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(n) or math.isinf(n):
        return 0.0
    return n


def _paths(market: str) -> tuple[Path, Path, Path, Path]:
    if market == "日本株":
        return (
            DATA_ROOT / "data",
            DATA_ROOT / "cache" / "tickers.csv",
            DATA_ROOT / "cache" / "latest_quotes.parquet",
            DATA_ROOT / "cache" / "shares.parquet",
        )
    return (
        DATA_ROOT / "data_us",
        DATA_ROOT / "cache" / "us_tickers.csv",
        DATA_ROOT / "cache" / "latest_quotes_us.parquet",
        DATA_ROOT / "cache" / "shares_us.parquet",
    )


def _meta_map(tickers_path: Path) -> dict[str, dict]:
    meta: dict[str, dict] = {}
    if not tickers_path.exists():
        return meta
    tickers = pd.read_csv(tickers_path, encoding="utf-8-sig")
    for rec in tickers.to_dict("records"):
        meta[str(rec.get("ticker"))] = rec
    return meta


def _load_shares(path: Path) -> dict[str, float]:
    out: dict[str, float] = {}
    if not path.exists():
        return out
    try:
        df = pd.read_parquet(path)
        for rec in df.to_dict("records"):
            ticker = str(rec.get("ticker") or "")
            shares = _clean(rec.get("shares"))
            if ticker and shares > 0:
                out[ticker] = shares
    except Exception:
        return {}
    return out


def _row_dict(
    ticker: str,
    name: str,
    sector: str,
    last: float,
    prev: float,
    vol_last: int,
    vol_avg: float,
    asof: str,
    shares: float,
) -> dict[str, Any]:
    change = (last - prev) / prev * 100.0 if prev else 0.0
    turnover = last * vol_last
    return {
        "ticker": ticker,
        "name": name or ticker,
        "sector": sector or "その他",
        "price": round(last, 4),
        "prev": round(prev, 4),
        "change_pct": round(change, 4),
        "volume": int(vol_last),
        "vol_avg": int(vol_avg),
        "vol_ratio": round((vol_last / vol_avg) if vol_avg else 1.0, 4),
        "turnover": round(turnover, 2),
        "shares": shares,
        "market_cap": round(shares * last, 2) if shares else 0.0,
        "asof": asof,
    }


def enrich_quotes(rows: list[dict], shares_map: dict[str, float]) -> list[dict]:
    out: list[dict] = []
    for rec in rows:
        price = _clean(rec.get("price"))
        vol = _clean(rec.get("volume"))
        shares = float(shares_map.get(str(rec.get("ticker") or ""), 0.0) or _clean(rec.get("shares")))
        out.append(
            {
                "ticker": str(rec.get("ticker") or ""),
                "name": str(rec.get("name") or rec.get("ticker") or ""),
                "sector": str(rec.get("sector") or "その他"),
                "price": round(price, 4),
                "prev": round(_clean(rec.get("prev")), 4),
                "change_pct": round(_clean(rec.get("change_pct")), 4),
                "volume": int(vol),
                "vol_avg": int(_clean(rec.get("vol_avg"))),
                "vol_ratio": round(_clean(rec.get("vol_ratio")) or 1.0, 4),
                "turnover": round(price * vol, 2),
                "shares": shares,
                "market_cap": round(shares * price, 2) if shares else 0.0,
                "asof": str(rec.get("asof") or ""),
            }
        )
    return out


@dataclass
class TickerBars:
    ticker: str
    name: str
    sector: str
    days: np.ndarray
    close: np.ndarray
    volume: np.ndarray


@dataclass
class MarketHistory:
    market: str
    bars: list[TickerBars]
    days: list[str] = field(default_factory=list)
    shares: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._dayset = set(self.days)

    def snap(self, value: str | None) -> str | None:
        if not self.days:
            return None
        text = str(value or "").strip()
        if text in self._dayset:
            return text
        try:
            target = pd.Timestamp(text).strftime("%Y-%m-%d")
        except Exception:
            return self.days[-1]
        i = bisect_right(self.days, target) - 1
        if i < 0:
            return self.days[0]
        return self.days[i]

    def shift(self, asof: str, step: int) -> str | None:
        current = self.snap(asof)
        if current is None:
            return None
        i = self.days.index(current) + step
        i = max(0, min(len(self.days) - 1, i))
        return self.days[i]

    def quotes(self, asof: str | None) -> list[dict]:
        day = self.snap(asof)
        if day is None:
            return []
        rows: list[dict] = []
        for bar in self.bars:
            idx = int(np.searchsorted(bar.days, day, side="right")) - 1
            if idx < 1 or bar.days[idx] != day:
                continue
            last = _clean(bar.close[idx])
            prev = _clean(bar.close[idx - 1])
            if prev == 0:
                continue
            vol_last = int(bar.volume[idx])
            start = max(0, idx - 5)
            prev_vol = bar.volume[start:idx]
            vol_avg = float(prev_vol.mean()) if len(prev_vol) else 0.0
            shares = float(self.shares.get(bar.ticker) or 0.0)
            rows.append(
                _row_dict(
                    bar.ticker,
                    bar.name,
                    bar.sector,
                    last,
                    prev,
                    vol_last,
                    vol_avg,
                    day,
                    shares,
                )
            )
        return rows


def _bars_from_file(path: Path, rec: dict) -> TickerBars | None:
    ticker = path.stem
    try:
        df = pd.read_parquet(path, columns=["Date", "Close", "Volume"])
    except Exception:
        return None
    if df.empty or "Date" not in df.columns or "Close" not in df.columns:
        return None
    dates = pd.to_datetime(df["Date"], errors="coerce")
    close = pd.to_numeric(df["Close"], errors="coerce")
    keep = dates.notna() & close.notna()
    if int(keep.sum()) < 2:
        return None
    dates_k = dates[keep]
    close_k = close[keep]
    order = np.argsort(dates_k.to_numpy())
    day_arr = dates_k.dt.strftime("%Y-%m-%d").to_numpy()[order]
    close_arr = close_k.to_numpy(dtype="float64")[order]
    if "Volume" in df.columns:
        vol_k = pd.to_numeric(df.loc[keep, "Volume"], errors="coerce").fillna(0).to_numpy(dtype="float64")
        vol_arr = vol_k[order]
    else:
        vol_arr = np.zeros(len(day_arr), dtype="float64")
    return TickerBars(
        ticker=ticker,
        name=str(rec.get("銘柄名") or ticker),
        sector=str(
            rec.get("33業種区分")
            or rec.get("17業種区分")
            or rec.get("市場・商品区分")
            or "その他"
        ),
        days=day_arr,
        close=close_arr,
        volume=vol_arr,
    )


def _snap_date(days: list[str], value: str | None) -> str | None:
    if not days:
        return None
    text = str(value or "").strip()
    if text in days:
        return text
    if not text:
        return days[-1]
    i = bisect_right(days, text) - 1
    if i < 0:
        return days[0]
    return days[i]


def _repo_market_code(market: str) -> str:
    return "us" if market in {"us", "US", "米株"} else "jp"


def _read_quote_payload(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    quotes = payload.get("quotes")
    if not isinstance(quotes, list) or not quotes:
        return None
    cols = payload.get("cols")
    if quotes and not isinstance(quotes[0], dict):
        names = [str(c) for c in cols] if isinstance(cols, list) and cols else [
            "ticker", "name", "sector", "price", "change_pct", "volume", "vol_ratio", "turnover"
        ]
        asof = str(payload.get("asof") or path.stem)
        expanded: list[dict] = []
        for raw in quotes:
            if not isinstance(raw, list):
                continue
            rec = {names[i]: raw[i] for i in range(min(len(names), len(raw)))}
            rec.setdefault("asof", asof)
            rec.setdefault("prev", 0.0)
            rec.setdefault("vol_avg", 0)
            rec.setdefault("shares", 0.0)
            rec.setdefault("market_cap", 0.0)
            expanded.append(rec)
        quotes = expanded
        payload["quotes"] = quotes
    first = quotes[0] if isinstance(quotes[0], dict) else {}
    asof = str(payload.get("asof") or first.get("asof") or path.stem)
    payload["asof"] = asof
    payload["quotes"] = quotes
    return payload


def load_repo_snapshots(market: str) -> dict[str, dict[str, Any]]:
    code = _repo_market_code(market)
    out: dict[str, dict[str, Any]] = {}
    archive = REPO_ROOT / "data" / "quotes" / code
    if archive.is_dir():
        for path in sorted(archive.glob("????-??-??.json")):
            payload = _read_quote_payload(path)
            if payload:
                out[str(payload["asof"])] = payload
    latest = _read_quote_payload(REPO_ROOT / "data" / f"{code}.json")
    if latest:
        out[str(latest["asof"])] = latest
    return out


def load_latest_quotes(market: str) -> list[dict]:
    _data_dir, _tickers, latest, shares_path = _paths(market)
    if not latest.exists():
        return []
    try:
        rows = pd.read_parquet(latest).to_dict("records")
    except Exception:
        return []
    return enrich_quotes(rows, _load_shares(shares_path))


def load_market_history(
    market: str,
    progress: ProgressFn | None = None,
    force: bool = False,
) -> MarketHistory:
    data_dir, tickers_path, _latest, shares_path = _paths(market)
    meta = _meta_map(tickers_path)
    shares = _load_shares(shares_path)
    files = sorted(data_dir.glob("*.parquet")) if data_dir.exists() else []
    bars: list[TickerBars] = []
    day_set: set[str] = set()
    total = len(files)
    workers = min(8, max(2, (os.cpu_count() or 4)))
    done = 0
    from concurrent.futures import ThreadPoolExecutor, as_completed

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(_bars_from_file, path, meta.get(path.stem, {})) for path in files]
        for fut in as_completed(futs):
            done += 1
            if progress and (done == 1 or done % 200 == 0 or done == total):
                progress(done, total)
            bar = fut.result()
            if bar is None:
                continue
            bars.append(bar)
            day_set.update(bar.days.tolist())
    return MarketHistory(
        market=market,
        bars=bars,
        days=sorted(day_set),
        shares=shares,
    )


class MarketBundle:
    def __init__(self, market: str) -> None:
        self.market = market
        self.lock = threading.Lock()
        self.latest: list[dict] = []
        self.history: MarketHistory | None = None
        self.snapshots: dict[str, dict[str, Any]] = {}
        self.loading = False
        self.progress = (0, 0)
        self.error = ""

    def load_latest(self) -> list[dict]:
        rows = load_latest_quotes(self.market)
        snaps = load_repo_snapshots(self.market)
        if not rows and snaps:
            last = max(snaps)
            rows = list(snaps[last].get("quotes") or [])
        with self.lock:
            self.snapshots = snaps
            self.latest = rows
        return rows

    def start_history(self, force: bool = False) -> None:
        with self.lock:
            if self.loading:
                return
            if self.history is not None and not force:
                return
            self.loading = True
            self.error = ""
            self.progress = (0, 1)

        def run() -> None:
            try:

                def progress(done: int, total: int) -> None:
                    with self.lock:
                        self.progress = (done, total)

                history = load_market_history(self.market, progress=progress, force=force)
                with self.lock:
                    self.history = history
                    self.progress = (len(history.bars), len(history.bars))
                    if history.days:
                        self.latest = history.quotes(history.days[-1])
            except Exception as exc:  # noqa: BLE001
                with self.lock:
                    self.error = str(exc)
            finally:
                with self.lock:
                    self.loading = False

        threading.Thread(target=run, daemon=True, name=f"hist-{self.market}").start()

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            history = self.history
            latest = self.latest
            loading = self.loading
            progress = self.progress
            error = self.error
            snap_days = sorted(self.snapshots)
        asof = ""
        if latest:
            asof = str(latest[0].get("asof") or "")
        if snap_days:
            asof = snap_days[-1]
        if history and history.days:
            asof = history.days[-1]
        parquet_days = list(history.days) if history and history.days else []
        days = parquet_days or snap_days
        return {
            "market": self.market,
            "data_root": str(DATA_ROOT),
            "latest_count": len(latest),
            "history_ready": bool(days),
            "history_loading": loading,
            "history_progress": {"done": progress[0], "total": progress[1]},
            "day_count": len(days),
            "min_day": days[0] if days else "",
            "max_day": days[-1] if days else asof,
            "asof": asof,
            "error": error,
        }

    def days(self) -> list[str]:
        with self.lock:
            if self.history and self.history.days:
                return list(self.history.days)
            return sorted(self.snapshots)

    def quotes(self, asof: str | None = None) -> tuple[list[dict], str, bool]:
        with self.lock:
            history = self.history
            latest = list(self.latest)
            snapshots = dict(self.snapshots)
        if history and history.days:
            day = history.snap(asof) or history.days[-1]
            return history.quotes(day), day, True
        if snapshots:
            days = sorted(snapshots)
            day = _snap_date(days, asof) or days[-1]
            rows = list((snapshots.get(day) or {}).get("quotes") or [])
            return rows, day, True
        if latest:
            day = str(latest[0].get("asof") or "")
            return latest, day, False
        return [], "", False


_BUNDLES: dict[str, MarketBundle] = {
    "日本株": MarketBundle("日本株"),
    "米株": MarketBundle("米株"),
}


def get_bundle(market: str) -> MarketBundle:
    key = "米株" if market in {"us", "US", "米株"} else "日本株"
    bundle = _BUNDLES[key]
    if not bundle.latest:
        bundle.load_latest()
    if bundle.history is None:
        bundle.start_history(False)
    return bundle


def warmup() -> None:
    for bundle in _BUNDLES.values():
        bundle.load_latest()
    _BUNDLES["日本株"].start_history(False)
