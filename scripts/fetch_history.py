#!/usr/bin/env python3
"""全期間の日本株・米株 日足取得エントリポイント。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from history.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
