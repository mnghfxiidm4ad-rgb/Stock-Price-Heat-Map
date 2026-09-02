"""日米株式市場ニュースの Backfill / Daily 収集エントリ。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from news.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
