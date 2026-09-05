"""株価ヒートマップのローカルWEBサーバー。"""

from __future__ import annotations

import argparse
import json
import threading
import time
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from data import DATA_ROOT, get_bundle, warmup
from history.storage import bars_path, bars_to_web_json, load_bars, market_dirs, read_index
from news.storage import list_days, load_day, market_code
import pandas as pd

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"


def _json_default(obj):
    if hasattr(obj, "item"):
        return obj.item()
    return str(obj)


def _json(handler: SimpleHTTPRequestHandler, payload: dict, status: int = 200) -> None:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=_json_default).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(raw)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(raw)


def _file(handler: SimpleHTTPRequestHandler, path: Path, content_type: str) -> None:
    data = path.read_bytes()
    handler.send_response(200)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Cache-Control", "no-cache")
    handler.end_headers()
    handler.wfile.write(data)


def _param(qs: dict[str, list[str]], key: str, default: str = "") -> str:
    vals = qs.get(key)
    return vals[0] if vals else default


class Handler(SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:
        print("[%s] %s" % (self.log_date_time_string(), fmt % args))

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path == "/api/status":
            jp = get_bundle("日本株").snapshot()
            us = get_bundle("米株").snapshot()
            _json(self, {"jp": jp, "us": us, "data_root": str(DATA_ROOT)})
            return
        if path == "/api/meta":
            market = _param(qs, "market", "日本株")
            bundle = get_bundle(market)
            snap = bundle.snapshot()
            snap["days"] = bundle.days()
            _json(self, snap)
            return
        if path == "/api/quotes":
            market = _param(qs, "market", "日本株")
            asof = _param(qs, "asof") or None
            bundle = get_bundle(market)
            quotes, day, history_ready = bundle.quotes(asof)
            snap = bundle.snapshot()
            if not quotes:
                label = bundle.market
                if label == "日本株":
                    hint = "先に C:\\data\\日本株\\start.bat（または update.bat）で日足を取得してください。"
                else:
                    hint = "先に C:\\data\\日本株\\start_us.bat（または update_us.bat）で日足を取得してください。"
                _json(
                    self,
                    {
                        "ok": False,
                        "market": label,
                        "asof": day,
                        "history_ready": history_ready,
                        "quotes": [],
                        "sectors": [],
                        "message": f"{label}の日足がありません。{hint}",
                        "status": snap,
                    },
                )
                return
            sectors = sorted({str(r.get("sector") or "その他") for r in quotes})
            _json(
                self,
                {
                    "ok": True,
                    "market": bundle.market,
                    "asof": day,
                    "history_ready": history_ready,
                    "quotes": quotes,
                    "sectors": sectors,
                    "message": "",
                    "status": snap,
                },
            )
            return
        if path == "/api/news":
            market = _param(qs, "market", "日本株")
            asof = _param(qs, "asof")
            code = market_code(market)
            if not asof:
                _json(self, {"ok": False, "market": code, "date": "", "message": "asof が必要です"})
                return
            payload = load_day(code, asof)
            if not payload:
                _json(
                    self,
                    {
                        "ok": False,
                        "market": code,
                        "date": asof,
                        "indices": {},
                        "top_sectors": [],
                        "bottom_sectors": [],
                        "news_bullets": [],
                        "source_urls": [],
                        "message": "この日の市場ニュースはまだありません",
                    },
                )
                return
            out = dict(payload)
            out["ok"] = True
            out["message"] = ""
            _json(self, out)
            return
        if path == "/api/news-days":
            market = _param(qs, "market", "日本株")
            code = market_code(market)
            _json(self, {"ok": True, "market": code, "days": list_days(code)})
            return
        if path == "/api/history-index":
            market = _param(qs, "market", "日本株")
            payload = read_index(market)
            if not payload:
                code = "us" if market_code(market) == "US" else "jp"
                _json(
                    self,
                    {
                        "ok": False,
                        "market": code,
                        "count": 0,
                        "tickers": [],
                        "message": "日足 index がありません。scripts/fetch_history.py を実行してください。",
                    },
                )
                return
            out = dict(payload)
            out["ok"] = True
            _json(self, out)
            return
        if path == "/api/bars":
            market = _param(qs, "market", "日本株")
            ticker = _param(qs, "ticker")
            start = _param(qs, "start")
            end = _param(qs, "end")
            if not ticker:
                _json(self, {"ok": False, "message": "ticker が必要です"}, status=400)
                return
            code = "us" if market_code(market) == "US" else "jp"
            path_bars = bars_path(DATA_ROOT, code, ticker)
            df = load_bars(path_bars)
            if df.empty:
                _json(
                    self,
                    {
                        "ok": False,
                        "ticker": ticker,
                        "market": code,
                        "message": f"{ticker} の日足がありません",
                        "bars": [],
                    },
                )
                return
            if start:
                df = df[df["Date"] >= pd.Timestamp(start)]
            if end:
                df = df[df["Date"] <= pd.Timestamp(end)]
            dirs = market_dirs(DATA_ROOT, code)
            meta = {}
            if dirs["tickers"].is_file():
                try:
                    tickers = pd.read_csv(dirs["tickers"], encoding="utf-8-sig")
                    hit = tickers[tickers["ticker"].astype(str) == ticker]
                    if not hit.empty:
                        meta = hit.iloc[0].to_dict()
                except Exception:
                    meta = {}
            payload = bars_to_web_json(df, ticker, meta)
            payload["market"] = code
            _json(self, payload)
            return
        if path in {"/", "/index.html"}:
            _file(self, ROOT / "index.html", "text/html; charset=utf-8")
            return
        if path.startswith("/data/"):
            rel = path[len("/data/") :]
            target = (ROOT / "data" / rel).resolve()
            data_root = (ROOT / "data").resolve()
            if not str(target).startswith(str(data_root)) or not target.is_file():
                self.send_error(404)
                return
            _file(self, target, "application/json; charset=utf-8")
            return
        if path.startswith("/static/"):
            rel = path[len("/static/") :]
            target = (STATIC / rel).resolve()
            if not str(target).startswith(str(STATIC.resolve())) or not target.is_file():
                self.send_error(404)
                return
            types = {
                ".css": "text/css; charset=utf-8",
                ".js": "text/javascript; charset=utf-8",
                ".html": "text/html; charset=utf-8",
                ".svg": "image/svg+xml",
                ".png": "image/png",
                ".ico": "image/x-icon",
            }
            _file(self, target, types.get(target.suffix, "application/octet-stream"))
            return
        self.send_error(404)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        if parsed.path == "/api/reload":
            market = _param(qs, "market", "日本株")
            bundle = get_bundle(market)
            bundle.load_latest()
            bundle.start_history(force=True)
            _json(self, bundle.snapshot())
            return
        self.send_error(404)


def main() -> None:
    parser = argparse.ArgumentParser(description="株価ヒートマップ WEB")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    url = f"http://{args.host}:{args.port}"
    threading.Thread(target=warmup, daemon=True, name="warmup").start()
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"株価ヒートマップ  {url}", flush=True)
    print(f"データ: {DATA_ROOT}", flush=True)
    print("終了するときは Ctrl+C か、この窓を閉じてください。", flush=True)
    if not args.no_browser:
        threading.Thread(target=lambda: (time.sleep(0.8), webbrowser.open(url)), daemon=True).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n停止しました。")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
