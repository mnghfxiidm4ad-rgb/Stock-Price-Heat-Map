"""公開 RSS / Google News / GDELT / Yahoo Finance から見出しを集める。"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from typing import Iterable
from urllib.parse import quote_plus, urlparse

from news.calendar import gdelt_stamp, parse_day, session_window_utc, yyyymmdd
from news.httputil import fetch_json, fetch_text, session

_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")
_SOURCE_SUFFIX = re.compile(r"\s+[\-\u2013\u2014\|]\s+[^|\-]{2,40}$")


@dataclass
class Article:
    title: str
    url: str
    source: str
    published: datetime | None = None
    summary: str = ""


JP_RSS = [
    "https://www3.nhk.or.jp/rss/news/cat5.xml",
    "https://news.yahoo.co.jp/rss/topics/business.xml",
    "https://www.boj.or.jp/rss/whatsnew.xml",
]
US_RSS = [
    "https://feeds.reuters.com/reuters/businessNews",
    "https://feeds.reuters.com/reuters/USMarketsNews",
    "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "https://www.cnbc.com/id/10001147/device/rss/rss.html",
    "https://feeds.marketwatch.com/marketwatch/topstories/",
    "https://feeds.marketwatch.com/marketwatch/marketpulse/",
    "https://www.federalreserve.gov/feeds/press_all.xml",
]

JP_GOOGLE_Q = '日経平均 OR TOPIX OR 日銀 OR 東証 OR "ドル円" OR 半導体'
US_GOOGLE_Q = 'S&P 500 OR Nasdaq OR "Federal Reserve" OR FOMC OR "Dow Jones" OR Treasury'

JP_GDELT_Q = '(Nikkei OR TOPIX OR "Bank of Japan" OR Yen OR Tokyo) sourcelang:japanese'
US_GDELT_Q = '("S&P 500" OR Nasdaq OR FOMC OR "Federal Reserve" OR "Dow Jones" OR Treasury)'

JP_YF_TICKERS = ["^N225", "1306.T", "JPY=X"]
US_YF_TICKERS = ["^GSPC", "^IXIC", "^DJI", "^TNX", "SPY"]

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "dc": "http://purl.org/dc/elements/1.1/",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rss": "http://purl.org/rss/1.0/",
}


def _clean_text(value: str) -> str:
    text = unescape(_TAG.sub(" ", value or ""))
    text = _SPACE.sub(" ", text).strip()
    text = _SOURCE_SUFFIX.sub("", text).strip()
    return text


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    try:
        dt = parsedate_to_datetime(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S"):
        try:
            raw = text.replace("Z", "+0000") if fmt.endswith("%z") else text
            dt = datetime.strptime(raw[:26], fmt.replace("%z", "+0000") if False else fmt)
        except Exception:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _local(el: ET.Element, names: Iterable[str]) -> str:
    for name in names:
        node = el.find(name)
        if node is not None and (node.text or "").strip():
            return node.text or ""
        node = el.find(name, NS)
        if node is not None and (node.text or "").strip():
            return node.text or ""
    return ""


def parse_feed(xml_text: str) -> list[Article]:
    text = xml_text.strip()
    if not text.startswith("<"):
        return []
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []
    items: list[ET.Element] = []
    items.extend(root.findall(".//item"))
    items.extend(root.findall(".//atom:entry", NS))
    items.extend(root.findall(".//{http://purl.org/rss/1.0/}item"))
    out: list[Article] = []
    for el in items:
        title = _clean_text(_local(el, ("title", "atom:title")))
        if not title:
            continue
        url = _clean_text(_local(el, ("link", "atom:id")))
        link_el = el.find("link")
        if link_el is not None and link_el.get("href"):
            url = link_el.get("href") or url
        atom_link = el.find("atom:link", NS)
        if atom_link is not None and atom_link.get("href"):
            url = atom_link.get("href") or url
        source = _clean_text(_local(el, ("source", "dc:creator", "dc:publisher")))
        if not source:
            src_el = el.find("source")
            if src_el is not None:
                source = _clean_text(src_el.get("url") or src_el.text or "")
        published = _parse_dt(_local(el, ("pubDate", "published", "updated", "dc:date", "atom:published", "atom:updated")))
        summary = _clean_text(_local(el, ("description", "summary", "atom:summary", "content")))
        if not source:
            host = urlparse(url).netloc.replace("www.", "")
            source = host
        out.append(Article(title=title, url=url, source=source, published=published, summary=summary[:400]))
    return out


def fetch_rss_list(urls: list[str]) -> list[Article]:
    sess = session()
    articles: list[Article] = []
    for url in urls:
        try:
            xml_text = fetch_text(url, sess=sess, ttl_sec=3 * 3600)
            articles.extend(parse_feed(xml_text))
        except Exception as exc:  # noqa: BLE001
            print(f"  rss skip {url}: {exc}", flush=True)
    return articles


def google_news_url(query: str, day, lang: str) -> str:
    d = parse_day(day)
    after = yyyymmdd(d.replace(day=d.day) if False else d)
    # after:D-1 before:D+1 でその日前後を拾う
    from datetime import timedelta

    q = f"{query} after:{yyyymmdd(d - timedelta(days=1))} before:{yyyymmdd(d + timedelta(days=1))}"
    if lang == "ja":
        return (
            "https://news.google.com/rss/search?q="
            + quote_plus(q)
            + "&hl=ja&gl=JP&ceid=JP:ja"
        )
    return (
        "https://news.google.com/rss/search?q="
        + quote_plus(q)
        + "&hl=en-US&gl=US&ceid=US:en"
    )


def fetch_google_news(market: str, day) -> list[Article]:
    code = str(market).upper()
    url = google_news_url(JP_GOOGLE_Q if code == "JP" else US_GOOGLE_Q, day, "ja" if code == "JP" else "en")
    try:
        xml_text = fetch_text(url, ttl_sec=6 * 3600)
        return parse_feed(xml_text)
    except Exception as exc:  # noqa: BLE001
        print(f"  google news skip: {exc}", flush=True)
        return []


def fetch_gdelt(market: str, day) -> list[Article]:
    code = str(market).upper()
    start, end = session_window_utc(code, parse_day(day))
    query = JP_GDELT_Q if code == "JP" else US_GDELT_Q
    url = (
        "https://api.gdeltproject.org/api/v2/doc/doc?query="
        + quote_plus(query)
        + "&mode=ArtList&maxrecords=40&sort=DateDesc&format=json"
        + f"&startdatetime={gdelt_stamp(start)}&enddatetime={gdelt_stamp(end)}"
    )
    try:
        payload = fetch_json(url, ttl_sec=24 * 3600, timeout=15)
    except Exception as exc:  # noqa: BLE001
        print(f"  gdelt skip: {exc}", flush=True)
        return []
    arts = payload.get("articles") if isinstance(payload, dict) else None
    if not isinstance(arts, list):
        return []
    out: list[Article] = []
    for rec in arts:
        title = _clean_text(str(rec.get("title") or ""))
        url_s = str(rec.get("url") or "").strip()
        if not title or not url_s:
            continue
        source = str(rec.get("domain") or rec.get("sourceCountry") or "")
        published = _parse_dt(str(rec.get("seendate") or rec.get("seenDate") or ""))
        if published is None:
            seen = str(rec.get("seendate") or "")
            if len(seen) >= 8:
                try:
                    published = datetime.strptime(seen[:14].ljust(14, "0"), "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
                except Exception:
                    published = None
        out.append(Article(title=title, url=url_s, source=source, published=published))
    return out


def fetch_yahoo_news(market: str) -> list[Article]:
    try:
        import yfinance as yf
    except Exception:
        return []
    tickers = JP_YF_TICKERS if str(market).upper() == "JP" else US_YF_TICKERS
    out: list[Article] = []
    for symbol in tickers:
        try:
            items = yf.Ticker(symbol).news or []
        except Exception:
            continue
        for item in items:
            content = item.get("content") if isinstance(item.get("content"), dict) else item
            title = _clean_text(str(content.get("title") or item.get("title") or ""))
            url_s = ""
            for key in ("canonicalUrl", "clickThroughUrl"):
                val = content.get(key) if isinstance(content, dict) else None
                if isinstance(val, dict):
                    url_s = str(val.get("url") or "")
                if url_s:
                    break
            url_s = url_s or str(item.get("link") or item.get("url") or "")
            provider = content.get("provider") if isinstance(content, dict) else None
            source = ""
            if isinstance(provider, dict):
                source = str(provider.get("displayName") or "")
            source = source or str(item.get("publisher") or "")
            published = _parse_dt(str(content.get("pubDate") or ""))
            ts = item.get("providerPublishTime")
            if published is None and isinstance(ts, (int, float)):
                published = datetime.fromtimestamp(float(ts), tz=timezone.utc)
            if title:
                out.append(Article(title=title, url=url_s, source=source, published=published))
    return out


def in_window(article: Article, start: datetime, end: datetime) -> bool:
    if article.published is None:
        return True
    return start <= article.published <= end + timezone.utc.utcoffset(end) if False else start - timezone.utc.utcoffset(start) if False else True


def filter_window(articles: list[Article], market: str, day) -> list[Article]:
    start, end = session_window_utc(market, parse_day(day))
    kept: list[Article] = []
    for art in articles:
        if art.published is None:
            kept.append(art)
            continue
        pub = art.published if art.published.tzinfo else art.published.replace(tzinfo=timezone.utc)
        if start <= pub <= end:
            kept.append(art)
    return kept


def dedupe(articles: list[Article]) -> list[Article]:
    seen: set[str] = set()
    out: list[Article] = []
    for art in articles:
        key = re.sub(r"\s+", "", art.title.lower())[:80]
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(art)
    return out


def collect_articles(market: str, day, *, recent: bool, include_gdelt: bool) -> list[Article]:
    code = str(market).upper()
    articles: list[Article] = []
    articles.extend(fetch_google_news(code, day))
    if recent:
        articles.extend(fetch_rss_list(JP_RSS if code == "JP" else US_RSS))
        articles.extend(fetch_yahoo_news(code))
    if include_gdelt:
        articles.extend(fetch_gdelt(code, day))
    articles = dedupe(articles)
    windowed = filter_window(articles, code, day)
    # 日付不明の見出しは daily では残し、backfill では Google/GDELT のみ採用済み
    if recent:
        known = [a for a in articles if a.published is None]
        return dedupe(windowed + known)
    return windowed or articles[:12]
