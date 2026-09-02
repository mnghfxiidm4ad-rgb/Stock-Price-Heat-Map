"""Gemini / OpenAI で市況要因の JSON を生成する。"""

from __future__ import annotations

import json
import os
import random
import re
import time
from typing import Any

import requests

_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)


def llm_configured() -> bool:
    return bool(_gemini_key() or _openai_key())


def _gemini_key() -> str:
    return (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip()


def _openai_key() -> str:
    return (os.environ.get("OPENAI_API_KEY") or "").strip()


def _extract_json(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    m = _JSON_FENCE.search(raw)
    if m:
        raw = m.group(1)
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("LLM response is not JSON")
    data = json.loads(raw[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("LLM JSON is not an object")
    return data


def _gemini_complete(prompt: str, timeout: int) -> str:
    key = _gemini_key()
    model = os.environ.get("GEMINI_MODEL") or "gemini-2.0-flash"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    resp = requests.post(
        url,
        params={"key": key},
        json={
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.2,
                "responseMimeType": "application/json",
            },
        },
        timeout=timeout,
    )
    if resp.status_code == 429:
        raise TimeoutError("gemini 429")
    resp.raise_for_status()
    payload = resp.json()
    parts = (((payload.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
    texts = [str(p.get("text") or "") for p in parts if isinstance(p, dict)]
    text = "\n".join(texts).strip()
    if not text:
        raise RuntimeError("empty Gemini response")
    return text


def _openai_complete(prompt: str, timeout: int) -> str:
    key = _openai_key()
    model = os.environ.get("OPENAI_MODEL") or "gpt-4o-mini"
    resp = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": "You write factual Japanese market summaries as JSON."},
                {"role": "user", "content": prompt},
            ],
        },
        timeout=timeout,
    )
    if resp.status_code == 429:
        raise TimeoutError("openai 429")
    resp.raise_for_status()
    return str(resp.json()["choices"][0]["message"]["content"] or "")


def complete_json(prompt: str, *, retries: int = 4, timeout: int = 60) -> dict[str, Any]:
    if not llm_configured():
        raise RuntimeError("GEMINI_API_KEY または OPENAI_API_KEY がありません")
    last_exc: Exception | None = None
    use_gemini = bool(_gemini_key())
    for attempt in range(1, retries + 1):
        try:
            text = _gemini_complete(prompt, timeout) if use_gemini else _openai_complete(prompt, timeout)
            return _extract_json(text)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            wait = min(45.0, 2.0 * (2 ** (attempt - 1))) + random.uniform(0, 0.6)
            print(f"  llm retry {attempt}/{retries} after {wait:.0f}s: {exc}", flush=True)
            time.sleep(wait)
            if use_gemini and _openai_key() and attempt >= 2:
                use_gemini = False
                print("  llm fallback to OpenAI", flush=True)
    raise RuntimeError(f"LLM failed: {last_exc}") from last_exc
