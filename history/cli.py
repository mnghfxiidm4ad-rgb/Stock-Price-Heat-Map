"""全期間の日本株・米株 日足取得 CLI。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from history.export import (
    export_compact_bars_sample,
    export_web_snapshot,
    export_web_snapshot_range,
    rebuild_latest_cache,
    refresh_web_index,
)
from history.fetch import fetch_market_history
from history.storage import default_data_root, market_dirs


def _markets(value: str) -> list[str]:
    if value == "both" or value == "all":
        return ["jp", "us"]
    return [value]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="日本株・米株の全期間日足（OHLCV）を取得し、Web/ヒートマップ用に保存する"
    )
    parser.add_argument("--market", choices=("jp", "us", "both", "all"), default="both")
    parser.add_argument(
        "--period",
        default="max",
        help="yfinance period（start 未指定時）。max / 10y / 5y / 1y など",
    )
    parser.add_argument("--start", default="", help="開始日 YYYY-MM-DD（指定時は period より優先）")
    parser.add_argument("--end", default="", help="終了日 YYYY-MM-DD")
    parser.add_argument(
        "--root",
        default="",
        help="保存ルート（未指定時は STOCK_DATA_ROOT または C:\\data\\日本株 / data/bars_store）",
    )
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--sleep", type=float, default=1.5)
    parser.add_argument("--limit", type=int, default=0, help="先頭 N 銘柄だけ（試験用）")
    parser.add_argument("--tickers", default="", help="カンマ区切りティッカー（例: 7203.T,AAPL）")
    parser.add_argument(
        "--full",
        action="store_true",
        help="既存を無視して全期間を取り直す（デフォルトは増分更新）",
    )
    parser.add_argument(
        "--index-only",
        action="store_true",
        help="取得せず、既存 parquet から Web 用 index/meta だけ更新",
    )
    parser.add_argument(
        "--export-snapshot",
        action="store_true",
        help="最新（または --asof）のヒートマップ用 JSON を data/quotes と data/{jp,us}.json に書き出す",
    )
    parser.add_argument(
        "--force-snapshot",
        action="store_true",
        help="既存より少ない銘柄数でもスナップショットを上書きする",
    )
    parser.add_argument("--asof", default="", help="--export-snapshot の対象日 YYYY-MM-DD")
    parser.add_argument(
        "--export-range",
        action="store_true",
        help="--start/--end の各営業日スナップショットを data/quotes に書き出す（parquet から）",
    )
    parser.add_argument(
        "--max-export-days",
        type=int,
        default=0,
        help="--export-range で末尾から最大 N 営業日だけ書く（0=制限なし）",
    )
    parser.add_argument(
        "--rebuild-latest",
        action="store_true",
        help="cache/latest_quotes*.parquet を日足から再構築",
    )
    parser.add_argument(
        "--sample-json",
        default="",
        help="指定ティッカーのコンパクト JSON を data/history/{jp,us}/sample/ に書き出す（検証用）",
    )
    parser.add_argument("--skip-fetch", action="store_true", help="Yahoo 取得をスキップ（出力処理のみ）")
    args = parser.parse_args(argv)

    root = Path(args.root) if args.root else default_data_root()
    root.mkdir(parents=True, exist_ok=True)
    markets = _markets(args.market)
    ticker_list = [t.strip() for t in args.tickers.split(",") if t.strip()] or None
    limit = args.limit if args.limit > 0 else None
    start = args.start.strip() or None
    end = args.end.strip() or None
    asof = args.asof.strip() or None

    print(f"data root: {root}", flush=True)
    results = []

    for market in markets:
        dirs = market_dirs(root, market)
        print(f"=== {market} bars -> {dirs['bars']}", flush=True)

        if args.index_only:
            path = refresh_web_index(root, market)
            print(f"index: {path}", flush=True)
            results.append({"market": market, "index": str(path)})
            continue

        if not args.skip_fetch:
            summary = fetch_market_history(
                market,
                root=root,
                period=args.period,
                start=start,
                end=end,
                incremental=not args.full,
                batch_size=args.batch_size,
                sleep_sec=args.sleep,
                limit=limit,
                tickers=ticker_list,
            )
            results.append(summary)
            print(json.dumps(summary, ensure_ascii=False), flush=True)

        index_path = refresh_web_index(root, market)
        print(f"web index: {index_path}", flush=True)

        if args.rebuild_latest:
            latest = rebuild_latest_cache(root, market)
            print(f"latest cache: {latest}", flush=True)

        if args.export_snapshot:
            snap = export_web_snapshot(root, market, asof=asof, force=args.force_snapshot)
            print(f"web snapshot: {snap}", flush=True)

        if args.export_range:
            if not start or not end:
                raise SystemExit("--export-range には --start と --end が必要です")
            paths = export_web_snapshot_range(
                root,
                market,
                start,
                end,
                force=args.force_snapshot,
                max_days=args.max_export_days,
            )
            print(f"web snapshots: {len(paths)} days", flush=True)

        if args.sample_json:
            samples = [t.strip() for t in args.sample_json.split(",") if t.strip()]
            # 市場に属するティッカーだけ
            if market == "jp":
                samples = [t for t in samples if t.endswith(".T") or t.endswith(".t")]
            else:
                samples = [t for t in samples if not (t.endswith(".T") or t.endswith(".t"))]
            if samples:
                paths = export_compact_bars_sample(root, market, samples)
                print(f"sample json: {len(paths)} files", flush=True)

    print("done", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
