"""市場ニュース JSON の共通スキーマ。"""

from __future__ import annotations

from typing import Any


MARKETS = ("JP", "US")


def empty_payload(market: str, date: str) -> dict[str, Any]:
    code = "US" if str(market).upper() == "US" else "JP"
    if code == "JP":
        indices = {
            "primary": {"name": "日経平均", "close": "", "change": ""},
            "secondary": {"name": "TOPIX", "close": "", "change": ""},
            "sub": {"name": "ドル/円 (USD/JPY)", "value": "", "change": ""},
        }
    else:
        indices = {
            "primary": {"name": "S&P 500", "close": "", "change": ""},
            "secondary": {"name": "NASDAQ", "close": "", "change": ""},
            "tertiary": {"name": "NYダウ", "close": "", "change": ""},
            "sub": {"name": "米10年債利回り", "value": "", "change": ""},
        }
    return {
        "market": code,
        "date": date,
        "indices": indices,
        "top_sectors": [],
        "bottom_sectors": [],
        "news_bullets": [],
        "source_urls": [],
    }


def normalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    market = str(payload.get("market") or "JP").upper()
    date = str(payload.get("date") or "")
    base = empty_payload(market, date)
    indices = dict(base["indices"])
    raw_idx = payload.get("indices") or {}
    if isinstance(raw_idx, dict):
        for key, default in indices.items():
            item = raw_idx.get(key)
            if isinstance(item, dict):
                merged = dict(default)
                merged.update({k: item.get(k, merged.get(k, "")) for k in set(merged) | set(item)})
                indices[key] = merged
    base["indices"] = indices
    base["top_sectors"] = [str(x) for x in (payload.get("top_sectors") or []) if str(x).strip()]
    base["bottom_sectors"] = [str(x) for x in (payload.get("bottom_sectors") or []) if str(x).strip()]
    base["news_bullets"] = [str(x).strip() for x in (payload.get("news_bullets") or []) if str(x).strip()]
    base["source_urls"] = [str(x).strip() for x in (payload.get("source_urls") or []) if str(x).strip()]
    for extra in ("generated_at", "mode", "trading_day"):
        if extra in payload:
            base[extra] = payload[extra]
    return base
