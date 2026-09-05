from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import update_quotes as uq  # noqa: E402


class QuoteIndexTests(unittest.TestCase):
    def test_write_index_from_archive(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            uq.DATA_DIR = root
            archive = root / "quotes" / "jp"
            archive.mkdir(parents=True)
            (archive / "2026-09-01.json").write_text("{}", encoding="utf-8")
            (archive / "2026-09-03.json").write_text("{}", encoding="utf-8")
            days = uq.write_quote_index("jp", ["2026-09-04"])
            self.assertEqual(days, ["2026-09-01", "2026-09-03", "2026-09-04"])
            payload = json.loads((archive / "index.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["count"], 3)
            self.assertEqual(payload["min_day"], "2026-09-01")
            self.assertEqual(payload["max_day"], "2026-09-04")

    def test_repo_quote_days_exist(self) -> None:
        uq.DATA_DIR = ROOT / "data"
        days = uq.list_quote_days("jp")
        self.assertIn("2026-09-04", days)
        self.assertGreaterEqual(len(days), 2)


if __name__ == "__main__":
    unittest.main()
