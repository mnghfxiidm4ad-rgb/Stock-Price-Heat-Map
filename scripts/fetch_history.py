"""Yahoo Finance から日足の全期間（または指定期間）を取得し、history/ に保存する。"""

from __future__ import annotations

import json
import os
import random
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
HISTORY_ROOT = Path(os.environ.get("STOCK_HISTORY_ROOT", str(ROOT / "history")))

OHLCV_COLS = ["Date", "Open", "High", "Low", "Close", "Adj Close", "Volume"]
FRESH_DAYS = 7
START_TOLERANCE_DAYS = 10
PERIOD_RANK = {
    "1d": 1,
    "5d": 2,
    "1mo": 3,
    "3mo": 4,
    "6mo": 5,
    "1y": 6,
    "2y": 7,
    "5y": 8,
    "10y": 9,
    "ytd": 6,
    "max": 100,
}
PERIOD_OFFSETS = {
    "1d": pd.DateOffset(days=1),
    "5d": pd.DateOffset(days=7),
    "1mo": pd.DateOffset(months=1),
    "3mo": pd.DateOffset(months=3),
    "6mo": pd.DateOffset(months=6),
    "1y": pd.DateOffset(years=1),
    "2y": pd.DateOffset(years=2),
    "5y": pd.DateOffset(years=5),
    "10y": pd.DateOffset(years=10),
}

DownloadFn = Callable[..., pd.DataFrame]


def history_dir(market: str) -> Path:
    return HISTORY_ROOT / ("jp" if market == "jp" else "us")


def parquet_path(market: str, ticker: str) -> Path:
    safe = str(ticker).replace("/", "-").replace("\\", "-")
    return history_dir(market) / f"{safe}.parquet"


def meta_path(market: str) -> Path:
    return history_dir(market) / "_meta.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def load_meta(market: str) -> dict[str, Any]:
    path = meta_path(market)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_meta(market: str, meta: dict[str, Any]) -> None:
    path = meta_path(market)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def requested_start(period: str, start: str, today: pd.Timestamp | None = None) -> pd.Timestamp | None:
    if str(start or "").strip():
        return pd.Timestamp(start).normalize()
    today = today if today is not None else pd.Timestamp.now().normalize()
    key = (period or "max").strip().lower() or "max"
    if key == "max":
        return None
    if key == "ytd":
        return pd.Timestamp(year=int(today.year), month=1, day=1)
    offset = PERIOD_OFFSETS.get(key)
    if offset is None:
        return None
    return (today - offset).normalize()


def period_label(period: str, start: str) -> str:
    if str(start or "").strip():
        return f"{start} 以降"
    key = (period or "max").strip().lower() or "max"
    labels = {
        "1y": "1年",
        "2y": "2年",
        "5y": "5年",
        "10y": "10年",
        "max": "最長（Yahoo にある全期間）",
        "ytd": "年初来",
        "6mo": "6か月",
        "3mo": "3か月",
        "1mo": "1か月",
    }
    return labels.get(key, key)


def _naive_days(series: pd.Series) -> pd.Series:
    dt = pd.to_datetime(series, errors="coerce", utc=True)
    return dt.dt.tz_localize(None).dt.normalize()


def bounds_frame(path: Path) -> pd.DataFrame:
    if not path.is_file():
        return pd.DataFrame()
    try:
        raw = pd.read_parquet(path, columns=["Date"])
    except Exception:
        return pd.DataFrame()
    if raw is None or raw.empty or "Date" not in raw.columns:
        return pd.DataFrame()
    days = _naive_days(raw["Date"]).dropna()
    if days.empty:
        return pd.DataFrame()
    return pd.DataFrame({"Date": [days.min(), days.max()]})


def load_existing_bars(path: Path) -> pd.DataFrame:
    if not path.is_file():
        return pd.DataFrame()
    try:
        df = pd.read_parquet(path)
    except Exception:
        return pd.DataFrame()
    if df is None or df.empty:
        return pd.DataFrame()
    work = df.copy()
    if "Date" not in work.columns:
        work = work.reset_index()
        if "Date" not in work.columns and work.columns.size:
            work = work.rename(columns={work.columns[0]: "Date"})
    if "Date" not in work.columns or "Close" not in work.columns:
        return pd.DataFrame()
    work["Date"] = _naive_days(work["Date"])
    work = work.dropna(subset=["Date", "Close"])
    work = work.drop_duplicates(subset=["Date"], keep="last").sort_values("Date").reset_index(drop=True)
    return work


def merge_bars(old: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    frames = [f for f in (old, new) if f is not None and not f.empty]
    if not frames:
        return pd.DataFrame(columns=OHLCV_COLS)
    both = pd.concat(frames, ignore_index=True)
    both["Date"] = _naive_days(both["Date"])
    both = both.dropna(subset=["Date"])
    if "Close" in both.columns:
        both = both.dropna(subset=["Close"])
    both = both.drop_duplicates(subset=["Date"], keep="last").sort_values("Date").reset_index(drop=True)
    keep = [c for c in OHLCV_COLS if c in both.columns]
    return both[keep]


def write_bars(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".parquet.tmp")
    df.to_parquet(tmp, index=False)
    tmp.replace(path)


def infer_fetched_max(existing: pd.DataFrame, meta_period: str) -> bool:
    if (meta_period or "").lower() == "max":
        return True
    if existing is None or existing.empty or "Date" not in existing.columns:
        return False
    min_d = pd.Timestamp(existing["Date"].min())
    max_d = pd.Timestamp(existing["Date"].max())
    span_days = int((max_d - min_d).days) if pd.notna(min_d) and pd.notna(max_d) else 0
    return len(existing) >= 2000 or span_days >= 3650


def plan_ticker(
    existing: pd.DataFrame,
    want_start: pd.Timestamp | None,
    fetched_max: bool,
    today: pd.Timestamp | None = None,
    force: bool = False,
) -> str:
    """skip / incremental / download"""
    if force:
        return "download"
    today = today if today is not None else pd.Timestamp.now().normalize()
    fresh = today - pd.Timedelta(days=FRESH_DAYS)
    if existing is None or existing.empty or "Date" not in existing.columns:
        return "download"
    min_d = pd.Timestamp(existing["Date"].min()).normalize()
    max_d = pd.Timestamp(existing["Date"].max()).normalize()
    end_ok = max_d >= fresh
    if want_start is None:
        start_ok = bool(fetched_max)
    else:
        start_ok = bool(fetched_max) or min_d <= want_start + pd.Timedelta(days=START_TOLERANCE_DAYS)
    if start_ok and end_ok:
        return "skip"
    if start_ok:
        return "incremental"
    return "download"


def ohlcv_frame(raw: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame()
    df: pd.DataFrame | None = None
    if isinstance(raw.columns, pd.MultiIndex):
        names = [str(x) for x in raw.columns.get_level_values(0)]
        other = [str(x) for x in raw.columns.get_level_values(1)]
        if symbol in names:
            df = raw[symbol]
        elif symbol in other:
            df = raw.xs(symbol, axis=1, level=1)
        else:
            return pd.DataFrame()
    else:
        df = raw
    if df is None or df.empty:
        return pd.DataFrame()
    work = df.dropna(how="all").copy()
    if work.empty:
        return pd.DataFrame()
    if "Date" not in work.columns:
        work = work.reset_index()
    date_col = None
    for cand in ("Date", "Datetime", "index"):
        if cand in work.columns:
            date_col = cand
            break
    if date_col is None:
        return pd.DataFrame()
    if date_col != "Date":
        work = work.rename(columns={date_col: "Date"})
    work["Date"] = _naive_days(work["Date"])
    work = work.dropna(subset=["Date"])
    rename = {c: str(c).title() if str(c).lower() == "adj close" else c for c in work.columns}
    work = work.rename(columns=rename)
    if "Adj Close" not in work.columns and "Adj close" in work.columns:
        work = work.rename(columns={"Adj close": "Adj Close"})
    if "Close" not in work.columns:
        return pd.DataFrame()
    if "Volume" not in work.columns:
        work["Volume"] = 0
    keep = [c for c in OHLCV_COLS if c in work.columns]
    out = work[keep].dropna(subset=["Close"])
    out = out.drop_duplicates(subset=["Date"], keep="last").sort_values("Date").reset_index(drop=True)
    return out


def default_download(
    symbols: list[str],
    *,
    period: str | None = None,
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


class RunControl:
    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled and sys.stdin.isatty()
        self.paused = False
        self.quit = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not self.enabled:
            return
        self._thread = threading.Thread(target=self._watch, daemon=True, name="keys")
        self._thread.start()

    def _watch(self) -> None:
        getter = _key_reader()
        if getter is None:
            self.enabled = False
            return
        while not self.quit:
            ch = getter()
            if not ch:
                time.sleep(0.15)
                continue
            key = ch.lower()
            if key == "p":
                self.paused = True
                print("\n  一時停止中。 [R] 再開  [Q] 保存して終了", flush=True)
            elif key == "r":
                self.paused = False
                print("  再開します。", flush=True)
            elif key == "q":
                self.quit = True
                self.paused = False
                print("\n  終了要求を受け取りました。保存済みのファイルはそのまま残します。", flush=True)

    def wait_if_paused(self) -> None:
        while self.paused and not self.quit:
            time.sleep(0.2)


def _key_reader():
    if sys.platform == "win32":
        try:
            import msvcrt
        except ImportError:
            return None

        def _win() -> str:
            if not msvcrt.kbhit():
                return ""
            raw = msvcrt.getch()
            try:
                return raw.decode("utf-8", errors="ignore")
            except Exception:
                return ""

        return _win
    try:
        import select
        import termios
        import tty
    except ImportError:
        return None
    if not sys.stdin.isatty():
        return None
    fd = sys.stdin.fileno()
    try:
        old = termios.tcgetattr(fd)
    except termios.error:
        return None

    def _posix() -> str:
        try:
            ready, _, _ = select.select([sys.stdin], [], [], 0)
            if not ready:
                return ""
            tty.setcbreak(fd)
            return sys.stdin.read(1)
        except Exception:
            return ""
        finally:
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
            except Exception:
                pass

    return _posix


def _bar(done: int, total: int, width: int = 28) -> str:
    if total <= 0:
        return "░" * width
    frac = min(1.0, max(0.0, done / total))
    filled = int(round(width * frac))
    return "█" * filled + "░" * (width - filled)


def _short_name(rec: dict, ticker: str) -> str:
    name = str(rec.get("銘柄名") or rec.get("name") or ticker)
    return name.replace("　", " ").strip()[:20] or ticker


def _load_universe(market: str) -> pd.DataFrame:
    scripts_dir = str(Path(__file__).resolve().parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import update_quotes as uq

    session = uq._session()
    return uq.load_jp_universe(session) if market == "jp" else uq.load_us_universe(session)


def _date_span(df: pd.DataFrame) -> str:
    if df is None or df.empty or "Date" not in df.columns:
        return "-"
    lo = pd.Timestamp(df["Date"].min()).strftime("%Y-%m-%d")
    hi = pd.Timestamp(df["Date"].max()).strftime("%Y-%m-%d")
    return f"{lo} .. {hi}"


def _better_period(old: str, this_run: str) -> str:
    if PERIOD_RANK.get(this_run, 0) >= PERIOD_RANK.get(old, 0):
        return this_run
    return old or this_run


def run_history(
    market: str,
    period: str = "max",
    start: str = "",
    batch_size: int = 40,
    sleep_sec: float = 2.0,
    force: bool = False,
    tickers: list[str] | None = None,
    enable_keys: bool = True,
    download_fn: DownloadFn | None = None,
    today: pd.Timestamp | None = None,
) -> int:
    today = today if today is not None else pd.Timestamp.now().normalize()
    want_start = requested_start(period, start, today=today)
    run_period = "max" if want_start is None and not str(start or "").strip() else (period or "max").strip().lower()
    dest = history_dir(market)
    dest.mkdir(parents=True, exist_ok=True)
    downloader = download_fn or default_download
    label = "日本株" if market == "jp" else "米株"
    print("=" * 64, flush=True)
    print(f" {label}  日足の全期間取得", flush=True)
    print(f" 期間: {period_label(period, start)}", flush=True)
    print(f" 保存先: {dest}", flush=True)
    print(" 取得済みの日付はスキップし、足りない期間だけ追加します。", flush=True)
    print(" 操作: [P] 一時停止   [R] 再開   [Q] 保存して終了", flush=True)
    print("=" * 64, flush=True)

    if tickers:
        universe = pd.DataFrame({"ticker": list(tickers), "銘柄名": list(tickers)})
    else:
        print(f"銘柄リストを取得しています ({label}) ...", flush=True)
        universe = _load_universe(market)
        if "ticker" not in universe.columns:
            raise RuntimeError("ticker column missing")
    symbols = [str(t) for t in universe["ticker"].tolist() if str(t).strip()]
    meta_rows = {str(rec.get("ticker")): rec for rec in universe.to_dict("records")}
    meta = load_meta(market)
    control = RunControl(enabled=enable_keys)
    control.start()

    planned: dict[str, str] = {}
    last_dates: dict[str, pd.Timestamp] = {}
    for sym in symbols:
        path = parquet_path(market, sym)
        existing = bounds_frame(path)
        fetched_max = infer_fetched_max(existing, str((meta.get(sym) or {}).get("period") or ""))
        action = plan_ticker(existing, want_start, fetched_max, today=today, force=force)
        planned[sym] = action
        if not existing.empty:
            last_dates[sym] = pd.Timestamp(existing["Date"].max()).normalize()

    skip_n = sum(1 for a in planned.values() if a == "skip")
    incr_n = sum(1 for a in planned.values() if a == "incremental")
    dl_n = sum(1 for a in planned.values() if a == "download")
    print(
        f"対象 {len(symbols):,} 銘柄  スキップ {skip_n:,}  追記 {incr_n:,}  取得 {dl_n:,}",
        flush=True,
    )
    if want_start is None:
        print("Yahoo へ要求する期間: 上場来の全期間 (period=max)", flush=True)
    else:
        print(f"Yahoo へ要求する期間: {want_start.strftime('%Y-%m-%d')} 〜 本日", flush=True)
    print(flush=True)

    saved = 0
    appended = 0
    skipped = 0
    failed = 0
    done = 0
    total = len(symbols)
    work_symbols = [s for s in symbols if planned[s] != "skip"]
    skipped = skip_n
    done = skip_n
    if skip_n:
        print(f"  取得済み（期間カバー済み） {skip_n:,} 銘柄をスキップ", flush=True)

    def _status() -> None:
        pct = (100.0 * done / total) if total else 100.0
        print(
            f"  {_bar(done, total)} {done:,}/{total:,}  {pct:5.1f}%  "
            f"保存 {saved:,}  追記 {appended:,}  スキップ {skipped:,}  失敗 {failed:,}",
            flush=True,
        )

    def _persist_one(sym: str, new_df: pd.DataFrame, action: str) -> None:
        nonlocal saved, appended, failed
        old = load_existing_bars(parquet_path(market, sym))
        merged = merge_bars(old, new_df)
        if merged.empty:
            failed += 1
            print(f"  {sym:<12} {_short_name(meta_rows.get(sym, {}), sym):<20}  データなし", flush=True)
            return
        write_bars(parquet_path(market, sym), merged)
        this_period = "max" if want_start is None else run_period
        old_period = str((meta.get(sym) or {}).get("period") or "")
        meta[sym] = {
            "min_day": pd.Timestamp(merged["Date"].min()).strftime("%Y-%m-%d"),
            "max_day": pd.Timestamp(merged["Date"].max()).strftime("%Y-%m-%d"),
            "rows": int(len(merged)),
            "period": _better_period(old_period, this_period),
            "updated_at": _now_iso(),
        }
        verb = "追記" if action == "incremental" and not old.empty else "保存"
        if action == "incremental":
            appended += 1
        else:
            saved += 1
        extra = ""
        if not old.empty:
            extra = f"  (既存 {_date_span(old)})"
        print(
            f"  {sym:<12} {_short_name(meta_rows.get(sym, {}), sym):<20}  "
            f"{verb} {_date_span(merged)}  {len(merged):,}日{extra}",
            flush=True,
        )

    def _run_group(group: list[str], action: str) -> None:
        nonlocal done, failed
        if not group:
            return
        size = max(1, int(batch_size))
        batches = [group[i : i + size] for i in range(0, len(group), size)]
        for bi, chunk in enumerate(batches, start=1):
            control.wait_if_paused()
            if control.quit:
                return
            if action == "incremental":
                starts = [last_dates[s] + pd.Timedelta(days=1) for s in chunk if s in last_dates]
                start_s = min(starts).strftime("%Y-%m-%d") if starts else None
                period_s = None
                req = f"{start_s} 〜 本日" if start_s else "差分"
            elif want_start is not None:
                start_s = want_start.strftime("%Y-%m-%d")
                period_s = None
                req = f"{start_s} 〜 本日"
            else:
                start_s = None
                period_s = "max"
                req = "上場来の全期間 (max)"
            print(
                f"\n  batch {bi}/{len(batches)}  {chunk[0]} .. {chunk[-1]}  {len(chunk)}銘柄",
                flush=True,
            )
            print(f"  Yahoo 取得中: {req}", flush=True)
            raw = downloader(chunk, period=period_s, start=start_s)
            got = 0
            for sym in chunk:
                control.wait_if_paused()
                if control.quit:
                    break
                frame = ohlcv_frame(raw, sym)
                if frame.empty and len(chunk) == 1 and not isinstance(getattr(raw, "columns", None), pd.MultiIndex):
                    frame = ohlcv_frame(raw, chunk[0])
                if frame.empty:
                    failed += 1
                    print(
                        f"  {sym:<12} {_short_name(meta_rows.get(sym, {}), sym):<20}  取得失敗",
                        flush=True,
                    )
                else:
                    _persist_one(sym, frame, action)
                    got += 1
                done += 1
            save_meta(market, meta)
            print(f"    got {got}/{len(chunk)}", flush=True)
            _status()
            if control.quit:
                return
            if bi < len(batches) and sleep_sec > 0:
                time.sleep(sleep_sec)

    download_list = [s for s in work_symbols if planned[s] == "download"]
    incr_list = [s for s in work_symbols if planned[s] == "incremental"]
    _run_group(download_list, "download")
    if not control.quit:
        _run_group(incr_list, "incremental")

    save_meta(market, meta)
    print(flush=True)
    print("-" * 64, flush=True)
    print(f" 完了  {label}", flush=True)
    print(
        f" 対象 {len(symbols):,}  保存 {saved:,}  追記 {appended:,}  スキップ {skipped:,}  失敗 {failed:,}",
        flush=True,
    )
    print(f" 保存先 {dest}", flush=True)
    if control.quit:
        print(" 途中終了しました。再実行すると続き（未取得期間）だけ取得します。", flush=True)
    print("-" * 64, flush=True)
    return 0


def run_from_args(args: Any) -> int:
    markets = ["jp", "us"] if args.market == "both" else [args.market]
    tickers = None
    raw = str(getattr(args, "tickers", "") or "").strip()
    if raw:
        tickers = [t.strip() for t in raw.replace(";", ",").split(",") if t.strip()]
    code = 0
    for market in markets:
        code = run_history(
            market=market,
            period=str(getattr(args, "period", "") or "max"),
            start=str(getattr(args, "start", "") or ""),
            batch_size=int(getattr(args, "batch_size", 40) or 40),
            sleep_sec=float(getattr(args, "sleep", 2.0) or 0.0),
            force=bool(getattr(args, "force", False)),
            tickers=tickers,
            enable_keys=not bool(getattr(args, "no_keys", False)),
        )
    return code


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Fetch full daily OHLCV history into history/")
    parser.add_argument("--market", choices=("jp", "us", "both"), default="both")
    parser.add_argument("--period", default="max", help="yfinance period (1y, 5y, max, ...)")
    parser.add_argument("--start", default="", help="start date YYYY-MM-DD")
    parser.add_argument("--batch-size", type=int, default=40)
    parser.add_argument("--sleep", type=float, default=2.0)
    parser.add_argument("--force", action="store_true", help="re-download even if coverage exists")
    parser.add_argument("--tickers", default="", help="comma-separated subset for testing")
    parser.add_argument("--no-keys", action="store_true", help="disable P/R/Q keyboard control")
    args = parser.parse_args()
    return run_from_args(args)


if __name__ == "__main__":
    raise SystemExit(main())
