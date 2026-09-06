"""指標・見出しと LLM から、客観的な市況要因 3〜4 行を作る。"""

from __future__ import annotations

import json
import re
from typing import Any

from news.llm import complete_json, llm_configured
from news.sources import Article

JP_KEYWORDS = (
    "日銀", "植田", "利上げ", "利下げ", "為替", "円安", "円高", "日経", "TOPIX", "東証",
    "半導体", "決算", "原油", "雇用", "GDP", "CPI", "インフレ", "国債", "FRB", "FOMC",
    "米株", "中国", "関税", "適時開示", "IPO", "自社株", "配当", "日銀総裁", "ドル円",
    "NY", "ダウ", "ナスダック", "債券", "金利",
)
US_KEYWORDS = (
    "fed", "fomc", "powell", "rate", "inflation", "cpi", "pce", "jobs", "payroll",
    "nasdaq", "s&p", "dow", "treasury", "yield", "earnings", "oil", "tariff",
    "gdp", "unemployment", "semiconductor", "nvidia", "apple", "microsoft",
    "bond", "recession", "ai", "chip", "dollar", "yen", "boj",
)
_SKIP = re.compile(r"(写真特集|フォト|gallery|opinion poll|horoscope|recipe|スポーツナビ)", re.I)


def _sign_word(change: str, up: str, down: str, flat: str) -> str:
    text = (change or "").strip()
    if text.startswith("+") and not text.startswith("+0.00"):
        return up
    if text.startswith("-") and not text.startswith("-0.00"):
        return down
    return flat


def _index_lines(indices: dict[str, dict[str, str]]) -> list[str]:
    lines: list[str] = []
    for key in ("primary", "secondary", "tertiary", "sub"):
        item = indices.get(key) or {}
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        close = item.get("close") or item.get("value") or ""
        change = item.get("change") or ""
        if close:
            lines.append(f"- {name}: {close} ({change})")
    return lines


def _index_sentence(market: str, indices: dict[str, dict[str, str]]) -> list[str]:
    bullets: list[str] = []
    primary = indices.get("primary") or {}
    secondary = indices.get("secondary") or {}
    tertiary = indices.get("tertiary") or {}
    sub = indices.get("sub") or {}
    if str(market).upper() == "JP":
        if primary.get("close"):
            tone = _sign_word(primary.get("change", ""), "上昇して", "下落して", "ほぼ変わらず")
            bullets.append(
                f"{primary.get('name')}は前日比{primary.get('change')}の{primary.get('close')}円で{tone}取引を終えた。"
            )
        if secondary.get("close"):
            bullets.append(
                f"{secondary.get('name')}は前日比{secondary.get('change')}の{secondary.get('close')}だった。"
            )
        if sub.get("value"):
            bullets.append(
                f"{sub.get('name')}は{sub.get('value')}円、前日比{sub.get('change')}円。"
            )
    else:
        if primary.get("close"):
            tone = _sign_word(primary.get("change", ""), "上昇して", "下落して", "ほぼ変わらず")
            bullets.append(
                f"{primary.get('name')}は前日比{primary.get('change')}の{primary.get('close')}で{tone}取引を終えた。"
            )
        if secondary.get("close") and tertiary.get("close"):
            bullets.append(
                f"{secondary.get('name')}は{secondary.get('change')}、{tertiary.get('name')}は{tertiary.get('change')}だった。"
            )
        elif secondary.get("close"):
            bullets.append(f"{secondary.get('name')}は前日比{secondary.get('change')}の{secondary.get('close')}。")
        if sub.get("value"):
            bullets.append(f"{sub.get('name')}は{sub.get('value')}、前日比{sub.get('change')}ポイント。")
    return bullets


def _sector_sentence(top: list[str], bottom: list[str]) -> list[str]:
    out: list[str] = []
    if top:
        out.append(f"{'、'.join(top)}セクターが上昇率上位となった。")
    if bottom:
        out.append(f"{'、'.join(bottom)}セクターは軟調だった。")
    return out


def _score(title: str, market: str) -> int:
    low = title.lower()
    keys = JP_KEYWORDS if str(market).upper() == "JP" else US_KEYWORDS
    score = sum(2 for key in keys if key.lower() in low)
    if re.search(r"[0-9]", title):
        score += 1
    if len(title) >= 18:
        score += 1
    return score


def headline_to_bullet(title: str, market: str) -> str:
    text = re.sub(r"\s+", " ", title.strip())
    if str(market).upper() == "US" and re.search(r"[A-Za-z]", text) and not re.search(r"[\u3040-\u30ff\u4e00-\u9faf]", text):
        return f"海外メディアは「{text.rstrip('.')}」と伝えた。"
    if not text.endswith("。"):
        text += "。"
    return text


def news_bullets_from_articles(articles: list[Article], market: str, limit: int = 4) -> tuple[list[str], list[str]]:
    ranked = []
    for art in articles:
        title = art.title.strip()
        if len(title) < 12 or _SKIP.search(title):
            continue
        ranked.append((_score(title, market), art))
    ranked.sort(key=lambda x: x[0], reverse=True)
    bullets: list[str] = []
    urls: list[str] = []
    seen: set[str] = set()
    for score, art in ranked:
        if score <= 0 and len(bullets) >= 2:
            continue
        key = re.sub(r"\W+", "", art.title.lower())[:40]
        if key in seen:
            continue
        seen.add(key)
        bullets.append(headline_to_bullet(art.title, market))
        if art.url:
            urls.append(art.url)
        if len(bullets) >= limit:
            break
    return bullets, urls[:10]


def _prompt(market: str, date: str, indices: dict[str, dict[str, str]], top: list[str], bottom: list[str], articles: list[Article], mode: str) -> str:
    code = "US" if str(market).upper() == "US" else "JP"
    label = "米国株" if code == "US" else "日本株"
    heads = [a.title for a in articles[:24] if a.title]
    payload = {
        "market": code,
        "market_label": label,
        "date": date,
        "indices": indices,
        "top_sectors": top,
        "bottom_sectors": bottom,
        "headlines": heads,
        "mode": mode,
    }
    extra = (
        "当日の大引け後ニュース見出しも踏まえてください。"
        if mode == "daily"
        else "当時の客観的な市況要因を、指数の動きと整合する範囲で書いてください。憶測は禁止です。"
    )
    return (
        "あなたは金融市場の記録係です。扇動や投資助言はせず、客観的事実だけを日本語で書いてください。\n"
        f"{extra}\n"
        "出力は次の JSON のみです。news_bullets は 3〜4 本の文にしてください。\n"
        '{"news_bullets": ["...", "..."]}\n\n'
        "入力データ:\n"
        + "\n".join(_index_lines(indices))
        + "\n"
        + json.dumps(payload, ensure_ascii=False)
    )


def _llm_bullets(
    market: str,
    date: str,
    indices: dict[str, dict[str, str]],
    top: list[str],
    bottom: list[str],
    articles: list[Article],
    mode: str,
) -> list[str] | None:
    if not llm_configured():
        return None
    try:
        data = complete_json(_prompt(market, date, indices, top, bottom, articles, mode))
    except Exception as exc:  # noqa: BLE001
        print(f"  llm skip: {exc}", flush=True)
        return None
    bullets = [str(x).strip() for x in (data.get("news_bullets") or []) if str(x).strip()]
    if len(bullets) < 3:
        return None
    return bullets[:4]


def build_payload(
    *,
    market: str,
    date: str,
    indices: dict[str, dict[str, str]],
    top_sectors: list[str],
    bottom_sectors: list[str],
    articles: list[Article],
    generated_at: str,
    mode: str,
    use_llm: bool = True,
) -> dict[str, Any]:
    urls = [a.url for a in articles if a.url][:10]
    bullets: list[str] = []
    if use_llm:
        bullets = _llm_bullets(market, date, indices, top_sectors, bottom_sectors, articles, mode) or []
    if not bullets:
        fact = _index_sentence(market, indices)[:1]
        news, more_urls = news_bullets_from_articles(articles, market, limit=3)
        sectors = _sector_sentence(top_sectors, bottom_sectors)[:1]
        if more_urls:
            urls = more_urls
        seen: set[str] = set()
        for line in news + fact + sectors:
            key = re.sub(r"\s+", "", line)
            if not key or key in seen:
                continue
            seen.add(key)
            bullets.append(line)
        bullets = bullets[:4]
    return {
        "market": "US" if str(market).upper() == "US" else "JP",
        "date": date,
        "indices": indices,
        "top_sectors": top_sectors,
        "bottom_sectors": bottom_sectors,
        "news_bullets": bullets[:4],
        "source_urls": urls,
        "generated_at": generated_at,
        "mode": mode,
        "trading_day": True,
    }
