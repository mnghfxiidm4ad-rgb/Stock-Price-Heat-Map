"""主要指標とセクターETFの日次騰落。"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pandas as pd

from news.calendar import parse_day, yyyymmdd

JP_INDEX_MAP = {
    "primary": ("^N225", "日経平均", "close"),
    "secondary": ("1306.T", "TOPIX（ETF）", "close"),
    "sub": ("JPY=X", "ドル/円 (USD/JPY)", "value"),
}
US_INDEX_MAP = {
    "primary": ("^GSPC", "S&P 500", "close"),
    "secondary": ("^IXIC", "NASDAQ", "close"),
    "tertiary": ("^DJI", "NYダウ", "close"),
    "sub": ("^TNX", "米10年債利回り", "value"),
}

JP_SECTOR_ETFS = {
    "1617.T": "食品",
    "1618.T": "エネルギー資源",
    "1619.T": "建設・資材",
    "1620.T": "素材・化学",
    "1621.T": "医薬品",
    "1622.T": "自動車・輸送機",
    "1623.T": "鉄鋼・非鉄",
    "1624.T": "機械",
    "1625.T": "電機・精密",
    "1626.T": "情報通信・サービスその他",
    "1627.T": "電気・ガス",
    "1628.T": "運輸・物流",
    "1629.T": "商社・卸売",
    "1630.T": "小売",
    "1631.T": "銀行",
    "1632.T": "金融（除く銀行）",
    "1633.T": "不動産",
}
US_SECTOR_ETFS = {
    "XLK": "情報技術",
    "XLC": "コミュニケーション・サービス",
    "XLY": "一般消費財",
    "XLP": "生活必需品",
    "XLE": "エネルギー",
    "XLF": "金融",
    "XLV": "ヘルスケア",
    "XLI": "資本財",
    "XLB": "素材",
    "XLRE": "不動産",
    "XLU": "公益事業",
}

_INDEX_FALLBACKS = {
    "^N225": ["^N225"],
    "1306.T": ["1306.T", "^TOPX", "1346.T"],
    "JPY=X": ["JPY=X", "USDJPY=X"],
    "^GSPC": ["^GSPC"],
    "^IXIC": ["^IXIC"],
    "^DJI": ["^DJI"],
    "^TNX": ["^TNX"],
}


def market_symbols(market: str) -> tuple[dict[str, tuple[str, str, str]], dict[str, str]]:
    code = str(market).upper()
    if code == "US":
        return US_INDEX_MAP, US_SECTOR_ETFS
    return JP_INDEX_MAP, JP_SECTOR_ETFS


def all_tickers(market: str) -> list[str]:
    indices, sectors = market_symbols(market)
    names: list[str] = []
    for ticker, _label, _kind in indices.values():
        names.extend(_INDEX_FALLBACKS.get(ticker, [ticker]))
    names.extend(sectors.keys())
    out: list[str] = []
    seen: set[str] = set()
    for name in names:
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out


def _fmt_close(value: float, kind: str, ticker: str) -> str:
    if ticker == "^TNX":
        pct = value / 10.0 if value > 20 else value
        return f"{pct:.2f}%"
    if kind == "value" and ticker in {"JPY=X", "USDJPY=X"}:
        return f"{value:,.2f}"
    if abs(value) >= 1000:
        return f"{value:,.2f}"
    return f"{value:,.2f}"


def _fmt_change_pct(pct: float) -> str:
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.2f}%"


def _fmt_change_pts(delta: float, ticker: str) -> str:
    if ticker == "^TNX":
        pts = delta / 10.0 if abs(delta) > 2 else delta
        sign = "+" if pts >= 0 else ""
        return f"{sign}{pts:.2f}"
    sign = "+" if delta >= 0 else ""
    return f"{sign}{delta:.2f}"


def _series_close(raw: pd.DataFrame, ticker: str) -> pd.Series:
    if raw is None or raw.empty:
        return pd.Series(dtype="float64")
    df = raw
    if isinstance(df.columns, pd.MultiIndex):
        try:
            part = df[ticker]
        except Exception:
            return pd.Series(dtype="float64")
        close = part["Close"] if "Close" in part.columns else part.iloc[:, 0]
    else:
        close = df["Close"] if "Close" in df.columns else df.iloc[:, 0]
    close = pd.to_numeric(close, errors="coerce")
    idx = pd.to_datetime(close.index, errors="coerce")
    close.index = idx
    close = close[close.index.notna()].dropna().sort_index()
    close.index = pd.DatetimeIndex(close.index).tz_localize(None)
    return close


def download_history(tickers: list[str], start: date, end: date) -> pd.DataFrame:
    import yfinance as yf

    start_s = (start - timedelta(days=14)).isoformat()
    end_s = (end + timedelta(days=2)).isoformat()
    raw = yf.download(
        tickers=tickers,
        start=start_s,
        end=end_s,
        interval="1d",
        group_by="ticker",
        auto_adjust=False,
        actions=False,
        threads=False,
        progress=False,
        timeout=30,
    )
    if raw is None:
        return pd.DataFrame()
    return raw


def snapshot_for_day(raw: pd.DataFrame, ticker: str, day: date) -> dict[str, Any] | None:
    close = _series_close(raw, ticker)
    if close.empty:
        return None
    days = [d.date() for d in close.index]
    if day not in days:
        return None
    pos = days.index(day)
    last = float(close.iloc[pos])
    prev = float(close.iloc[pos - 1]) if pos > 0 else last
    if ticker == "^TNX" and last > 20:
        last_v, prev_v = last / 10.0, prev / 10.0
    else:
        last_v, prev_v = last, prev
    change_pct = ((last_v - prev_v) / prev_v * 100.0) if prev_v else 0.0
    return {
        "ticker": ticker,
        "close": last_v,
        "prev": prev_v,
        "change": last_v - prev_v,
        "change_pct": change_pct,
        "asof": yyyymmdd(day),
    }


def resolve_index_bar(raw: pd.DataFrame, ticker: str, day: date) -> dict[str, Any] | None:
    for cand in _INDEX_FALLBACKS.get(ticker, [ticker]):
        bar = snapshot_for_day(raw, cand, day)
        if bar:
            bar["ticker"] = cand
            return bar
    return None


def build_indices(market: str, raw: pd.DataFrame, day: date) -> dict[str, dict[str, str]]:
    mapping, _sectors = market_symbols(market)
    out: dict[str, dict[str, str]] = {}
    for key, (ticker, label, kind) in mapping.items():
        bar = resolve_index_bar(raw, ticker, day)
        if not bar:
            if kind == "value":
                out[key] = {"name": label, "value": "", "change": ""}
            else:
                out[key] = {"name": label, "close": "", "change": ""}
            continue
        if kind == "value":
            out[key] = {
                "name": label,
                "value": _fmt_close(bar["close"], kind, ticker),
                "change": _fmt_change_pts(bar["change"], ticker),
            }
        else:
            out[key] = {
                "name": label,
                "close": _fmt_close(bar["close"], kind, ticker),
                "change": _fmt_change_pct(bar["change_pct"]),
            }
    return out


def sector_changes(market: str, raw: pd.DataFrame, day: date) -> list[tuple[str, float]]:
    _indices, sectors = market_symbols(market)
    rows: list[tuple[str, float]] = []
    for ticker, name in sectors.items():
        bar = snapshot_for_day(raw, ticker, day)
        if not bar:
            continue
        rows.append((name, float(bar["change_pct"])))
    rows.sort(key=lambda x: x[1], reverse=True)
    return rows


def top_bottom_sectors(changes: list[tuple[str, float]], n: int = 2) -> tuple[list[str], list[str]]:
    if not changes:
        return [], []
    top = [name for name, pct in changes if pct > 0][:n]
    bottom = [name for name, pct in reversed(changes) if pct < 0][:n]
    if not top:
        top = [name for name, _pct in changes[:n]]
    if not bottom:
        bottom = [name for name, _pct in changes[-n:]]
    # 同じ名前が両方に入らないようにする
    bottom = [name for name in bottom if name not in top]
    return top[:n], bottom[:n]


def has_primary_bar(market: str, raw: pd.DataFrame, day: date) -> bool:
    mapping, _ = market_symbols(market)
    ticker = mapping["primary"][0]
    return resolve_index_bar(raw, ticker, day) is not None


def sectors_from_heatmap_json(path, day: date) -> list[tuple[str, float]] | None:
    import json
    from pathlib import Path

    target = Path(path)
    if not target.is_file():
        return None
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if str(payload.get("asof") or "") != yyyymmdd(day):
        return None
    quotes = payload.get("quotes") or []
    buckets: dict[str, list[float]] = {}
    for rec in quotes:
        sector = str(rec.get("sector") or "").strip()
        if not sector or sector in {"NASDAQ", "NYSE", "NYSE American", "NYSE Arca", "Cboe BZX", "IEX", "US"}:
            return None
        try:
            pct = float(rec.get("change_pct") or 0)
        except (TypeError, ValueError):
            continue
        buckets.setdefault(sector, []).append(pct)
    if len(buckets) < 4:
        return None
    rows = [(name, sum(vals) / len(vals)) for name, vals in buckets.items() if vals]
    rows.sort(key=lambda x: x[1], reverse=True)
    return rows
