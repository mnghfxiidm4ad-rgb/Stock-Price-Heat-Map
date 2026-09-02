"""市場ニュース JSON の読み書き。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from news.schema import normalize_payload

ROOT = Path(__file__).resolve().parent.parent
NEWS_ROOT = ROOT / "data" / "market_news"


def market_code(market: str) -> str:
    text = str(market).strip()
    if text in {"US", "us", "米株"}:
        return "US"
    return "JP"


def market_dir(market: str) -> Path:
    return NEWS_ROOT / market_code(market).lower()


def news_path(market: str, day: str) -> Path:
    return market_dir(market) / f"{day}.json"


def load_day(market: str, day: str) -> dict[str, Any] | None:
    path = news_path(market, day)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def write_day(market: str, payload: dict[str, Any]) -> Path:
    data = normalize_payload(payload)
    folder = market_dir(data["market"])
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{data['date']}.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)
    refresh_index(data["market"])
    return path


def list_days(market: str) -> list[str]:
    folder = market_dir(market)
    if not folder.is_dir():
        return []
    return sorted(p.stem for p in folder.glob("????-??-??.json"))


def refresh_index(market: str) -> Path:
    code = market_code(market)
    folder = market_dir(code)
    folder.mkdir(parents=True, exist_ok=True)
    days = list_days(code)
    payload = {"market": code, "days": days, "count": len(days)}
    path = folder / "index.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
