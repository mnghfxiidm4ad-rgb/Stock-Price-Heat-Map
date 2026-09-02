"""ニュース収集用の HTTP ヘルパー（ディスクキャッシュ付き）。"""

from __future__ import annotations

import hashlib
import json
import random
import time
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / ".cache" / "news"

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/atom+xml, application/xml, application/json, text/xml, */*",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
}


def _cache_path(url: str) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:40]
    return CACHE_DIR / f"{digest}.cache"


def session() -> requests.Session:
    sess = requests.Session()
    sess.headers.update(HTTP_HEADERS)
    return sess


def fetch_text(
    url: str,
    *,
    sess: requests.Session | None = None,
    timeout: int = 30,
    ttl_sec: int = 6 * 3600,
    retries: int = 3,
) -> str:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(url)
    if path.exists() and ttl_sec > 0:
        age = time.time() - path.stat().st_mtime
        if age <= ttl_sec:
            return path.read_text(encoding="utf-8", errors="replace")
    own = sess or session()
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            resp = own.get(url, timeout=timeout)
            resp.raise_for_status()
            text = resp.content.decode(resp.encoding or "utf-8", errors="replace")
            path.write_text(text, encoding="utf-8")
            return text
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < retries:
                time.sleep(min(20.0, 1.5 * (2 ** (attempt - 1))) + random.uniform(0, 0.4))
    raise RuntimeError(f"GET failed {url}: {last_exc}") from last_exc


def fetch_json(url: str, **kwargs: Any) -> Any:
    text = fetch_text(url, **kwargs)
    text = text.strip()
    if not text or text[0] not in "{[":
        raise RuntimeError(f"not JSON: {url}")
    return json.loads(text)
