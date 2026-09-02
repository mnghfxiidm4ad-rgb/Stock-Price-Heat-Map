"""市場ニュース収集 CLI。

python collect_market_news.py --market jp --start 2024-01-01 --end 2026-08-31
python collect_market_news.py --market us --daily
"""

from __future__ import annotations

import argparse
from datetime import date

from news.collect import collect_daily, collect_range


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="日米株式市場のニュース要約を生成する",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  python collect_market_news.py --market jp --start 2024-01-01 --end 2026-08-31\n"
            "  python collect_market_news.py --market us --start 2024-01-01 --end 2026-08-31\n"
            "  python collect_market_news.py --market all --start 2024-01-01 --end 2026-08-31\n"
            "  python collect_market_news.py --market jp --daily\n"
            "  python collect_market_news.py --market us --daily\n"
        ),
    )
    parser.add_argument(
        "--market",
        required=True,
        type=str.lower,
        choices=("jp", "us", "all"),
        help="対象市場",
    )
    parser.add_argument("--start", default="", help="バックフィル開始日 YYYY-MM-DD")
    parser.add_argument("--end", default="", help="バックフィル終了日 YYYY-MM-DD")
    parser.add_argument("--daily", action="store_true", help="直近セッションを日次収集する")
    parser.add_argument("--force", action="store_true", help="既存 JSON を上書きする")
    parser.add_argument("--sleep", type=float, default=1.2, help="営業日ごとの待機秒（レートリミット回避）")
    parser.add_argument("--no-llm", action="store_true", help="LLM を使わずルールベースで書く")
    parser.add_argument("--asof", default="", help="Daily のセッション日を明示する YYYY-MM-DD")
    args = parser.parse_args(argv)

    if args.daily:
        collect_daily(
            args.market,
            force=args.force,
            use_llm=not args.no_llm,
            asof=args.asof or None,
        )
        return 0

    if not args.start:
        parser.error("バックフィルには --start が必要です。日次は --daily を付けてください。")
    end = args.end or date.today().isoformat()
    collect_range(
        args.market,
        args.start,
        end,
        force=args.force,
        sleep_sec=args.sleep,
        use_llm=not args.no_llm,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
